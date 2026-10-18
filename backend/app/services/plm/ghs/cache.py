"""In-process cache of the regulatory data the classification engine reads (方案 §4.6, §7).

The real-time compliance WebSocket (``/ws/formulations/{id}/compliance``) re-classifies the whole formulation on every
edit; reading 50 substances, their classifications and raw-material
compositions from the database each time would cost far more than the
engine itself. :class:`CachedLookup` serves those reads from a process-wide
cache of *detached snapshots* (plain namespaces, never live ORM rows, so a
closed session cannot invalidate them).

Freshness: entries expire after :data:`TTL_S`; any flush in this process that
touches a substance, classification, composition, transport entry, material or
GHS statement clears the cache at once (other worker processes rely on the TTL).
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from ....db.models import MaterialRow
from ....db.models_regulatory import (
    GhsStatementRow,
    MaterialCompositionRow,
    SubstanceClassificationRow,
    SubstanceRow,
    SubstanceTransportRow,
)
from ..regulatory import cas as cas_mod
from .inputs import _Lookup
from .ruleset import RuleSet

TTL_S = 300.0
_WATCHED = (SubstanceRow, SubstanceClassificationRow, MaterialCompositionRow, SubstanceTransportRow, MaterialRow,
            GhsStatementRow)
_MISSING = object()


def _snapshot(row: Any) -> SimpleNamespace | None:
    if row is None:
        return None
    return SimpleNamespace(**{c.key: getattr(row, c.key) for c in row.__table__.columns})


class RegulatoryDataCache:
    """Thread-safe ``(kind, key) → value`` map with one expiry clock for the whole cache."""

    def __init__(self, ttl_s: float = TTL_S) -> None:
        self.ttl_s = ttl_s
        self._data: dict[tuple[str, str], Any] = {}
        self._lock = threading.Lock()
        self._born = time.monotonic()
        self.hits = 0
        self.misses = 0

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._born = time.monotonic()

    def get(self, kind: str, key: str) -> Any:
        with self._lock:
            if time.monotonic() - self._born > self.ttl_s:
                self._data.clear()
                self._born = time.monotonic()
            value = self._data.get((kind, key), _MISSING)
            if value is _MISSING:
                self.misses += 1
            else:
                self.hits += 1
            return value

    def put(self, kind: str, key: str, value: Any) -> Any:
        with self._lock:
            self._data[(kind, key)] = value
        return value

    def fetch(self, kind: str, key: str, load) -> Any:
        value = self.get(kind, key)
        return self.put(kind, key, load()) if value is _MISSING else value

    def __len__(self) -> int:
        return len(self._data)


CACHE = RegulatoryDataCache()


@event.listens_for(Session, "after_flush")
def _invalidate_on_write(session: Session, _ctx) -> None:
    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, _WATCHED):
            CACHE.clear()
            return


class CachedLookup(_Lookup):
    """:class:`~.inputs._Lookup` served from :data:`CACHE`."""

    def __init__(self, session: Session, rs: RuleSet, cache: RegulatoryDataCache | None = None) -> None:
        super().__init__(session, rs)
        self.cache = cache or CACHE

    def substance(self, substance_id: str | None = None, cas: str | None = None):
        if substance_id:
            return self.substance_by_id(substance_id)
        norm = cas_mod.normalize(cas) if cas else None
        if not norm:
            return None
        return self.cache.fetch("substance_cas", norm, lambda: _snapshot(self.session.execute(
            select(SubstanceRow).where(SubstanceRow.cas_no == norm)).scalar_one_or_none()))

    def substance_by_id(self, substance_id: str):
        return self.cache.fetch("substance", substance_id,
                                lambda: _snapshot(self.session.get(SubstanceRow, substance_id)))

    def classifications(self, substance_id: str):
        return self.cache.fetch("classifications", substance_id,
                                lambda: [_snapshot(r) for r in super(CachedLookup, self).classifications(substance_id)])

    def material(self, material_id: str):
        return self.cache.fetch("material", material_id, lambda: _snapshot(self.session.get(MaterialRow, material_id)))

    def compositions(self, material_id: str):
        return self.cache.fetch("compositions", material_id,
                                lambda: [_snapshot(r) for r in super(CachedLookup, self).compositions(material_id)])

    def transport(self, substance_ids: list[str]):
        out = []
        missing = [i for i in substance_ids if self.cache.get("transport", i) is _MISSING]
        if missing:
            found: dict[str, list] = {i: [] for i in missing}
            for row in super().transport(missing):
                found[row.substance_id].append(_snapshot(row))
            for sid, rows in found.items():
                self.cache.put("transport", sid, rows)
        for sid in substance_ids:
            rows = self.cache.get("transport", sid)
            out.extend(rows if rows is not _MISSING else [])
        return out


def statement_texts(session: Session, codes: list[str], cache: RegulatoryDataCache | None = None
                    ) -> dict[str, dict[str, str]]:
    """Cached :func:`.service.statement_texts`."""
    cache = cache or CACHE
    out: dict[str, dict[str, str]] = {}
    missing = []
    for code in codes:
        hit = cache.get("statement", code)
        if hit is _MISSING:
            missing.append(code)
        elif hit is not None:
            out[code] = hit
    if missing:
        rows = {r.code: {"zh": r.text_zh, "en": r.text_en}
                for r in session.execute(select(GhsStatementRow).where(GhsStatementRow.code.in_(missing))).scalars()}
        for code in missing:
            cache.put("statement", code, rows.get(code))
            if code in rows:
                out[code] = rows[code]
    return out
