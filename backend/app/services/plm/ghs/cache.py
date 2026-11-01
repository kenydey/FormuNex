"""In-process cache of the regulatory data the classification engine reads (方案 §4.6, §7).

The real-time compliance WebSocket (``/ws/formulations/{id}/compliance``) re-classifies the whole formulation on every
edit; reading 50 substances, their classifications and raw-material
compositions from the database each time would cost far more than the
engine itself. :class:`CachedLookup` serves those reads from a process-wide
cache of *detached snapshots* (plain namespaces, never live ORM rows, so a
closed session cannot invalidate them).

Freshness (stage 3 / SR-062): entries expire per entry (no single global
clock anymore), with tiered TTLs — user-editable rows (materials and
their compositions) expire fast as a backstop, reference data rides the
default. Any flush in this process that touches a watched row invalidates
just that row (or its kind when the key cannot be derived safely, e.g. a
deleted row's foreign key) — the old after_flush full clear is gone.
Other worker processes still rely on TTL expiry.
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
# Tiered TTLs: materials and compositions are edited through the UI, so a
# short TTL bounds staleness even if a write path misses targeted
# invalidation; reference rows (substances, classifications, transport,
# statements) change via imports and are covered by targeted invalidation.
TTL_BY_KIND: dict[str, float] = {"material": 60.0, "compositions": 60.0}
_WATCHED = (SubstanceRow, SubstanceClassificationRow, MaterialCompositionRow, SubstanceTransportRow, MaterialRow,
            GhsStatementRow)
_MISSING = object()


def _snapshot(row: Any) -> SimpleNamespace | None:
    if row is None:
        return None
    return SimpleNamespace(**{c.key: getattr(row, c.key) for c in row.__table__.columns})


class RegulatoryDataCache:
    """Thread-safe ``(kind, key) → value`` map with per-entry expiry.

    Stage 3 / SR-062: the single global expiry clock is gone — each entry
    carries its own deadline under its kind's TTL tier. :meth:`fetch`
    singleflights concurrent loads of the same key (one leader loads,
    followers wait on an event and recheck; a failed leader raises to its
    own caller and the next follower becomes leader, so every caller
    terminates with either a value or its own load's exception).
    """

    def __init__(
        self,
        ttl_s: float = TTL_S,
        max_entries: int = 10_000,
        ttl_by_kind: dict[str, float] | None = None,
    ) -> None:
        self.ttl_s = ttl_s
        self.ttl_by_kind = dict(TTL_BY_KIND if ttl_by_kind is None else ttl_by_kind)
        self.max_entries = max(1, max_entries)
        self._data: dict[tuple[str, str], tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self._inflight: dict[tuple[str, str], threading.Event] = {}
        self.hits = 0
        self.misses = 0

    def _ttl_for(self, kind: str) -> float:
        return float(self.ttl_by_kind.get(kind, self.ttl_s))

    def _lookup_locked(self, kind: str, key: str, *, count: bool = True) -> Any:
        """Lock-held lookup: expired entries drop and count as misses."""
        entry = self._data.get((kind, key))
        if entry is None:
            if count:
                self.misses += 1
            return _MISSING
        value, expires = entry
        if time.monotonic() >= expires:
            del self._data[(kind, key)]
            if count:
                self.misses += 1
            return _MISSING
        if count:
            self.hits += 1
        return value

    def clear(self) -> None:
        # Only the data map: in-flight loads are left alone (their leaders
        # clean up their own events). A caller that clears and then reads
        # may still observe a just-completed load — clear is best-effort
        # freshness; precise invalidation is invalidate/invalidate_kind.
        with self._lock:
            self._data.clear()

    def get(self, kind: str, key: str) -> Any:
        with self._lock:
            return self._lookup_locked(kind, key)

    def put(self, kind: str, key: str, value: Any) -> Any:
        with self._lock:
            # Stage 3 / SR-061: bound the map. Without a cap a long-lived
            # process accumulates one snapshot per substance/classification
            # row it ever read. FIFO eviction (dict insertion order);
            # re-putting an existing key refreshes the value, not its age.
            if (kind, key) not in self._data and len(self._data) >= self.max_entries:
                now = time.monotonic()
                for k in [k for k, (_, exp) in self._data.items() if now >= exp]:
                    del self._data[k]
                if (kind, key) not in self._data and len(self._data) >= self.max_entries:
                    self._data.pop(next(iter(self._data)))
            self._data[(kind, key)] = (value, time.monotonic() + self._ttl_for(kind))
        return value

    def invalidate(self, kind: str, key: str) -> None:
        with self._lock:
            self._data.pop((kind, key), None)

    def invalidate_kind(self, kind: str) -> None:
        with self._lock:
            for k in [k for k in self._data if k[0] == kind]:
                del self._data[k]

    def fetch(self, kind: str, key: str, load) -> Any:
        while True:
            value = self.get(kind, key)
            if value is not _MISSING:
                return value
            with self._lock:
                # Recheck without recounting: the get() above already
                # counted this caller's miss.
                value = self._lookup_locked(kind, key, count=False)
                if value is not _MISSING:
                    return value
                evt = self._inflight.get((kind, key))
                if evt is None:
                    evt = threading.Event()
                    self._inflight[(kind, key)] = evt
                    leader = True
                else:
                    leader = False
            if leader:
                try:
                    value = load()
                except Exception:
                    with self._lock:
                        self._inflight.pop((kind, key), None)
                    evt.set()
                    raise
                self.put(kind, key, value)
                with self._lock:
                    self._inflight.pop((kind, key), None)
                evt.set()
                return value
            evt.wait()

    def __len__(self) -> int:
        return len(self._data)


CACHE = RegulatoryDataCache()


def _invalidate_row(obj: Any, *, deleted: bool) -> None:
    """Targeted invalidation for one flushed row.

    Deleted rows can only be trusted for primary-key attributes (other
    attributes are expired after the flush), so deletes of rows keyed by
    a foreign key drop the whole kind instead of guessing the key.
    """
    if isinstance(obj, SubstanceRow):
        CACHE.invalidate("substance", obj.id)
        # A cas_no change would leave the old cas key stale; kind-level
        # is always correct here and cas-keyed entries are cheap to rebuild.
        CACHE.invalidate_kind("substance_cas")
    elif isinstance(obj, SubstanceClassificationRow):
        if deleted:
            CACHE.invalidate_kind("classifications")
        else:
            CACHE.invalidate("classifications", obj.substance_id)
    elif isinstance(obj, SubstanceTransportRow):
        if deleted:
            CACHE.invalidate_kind("transport")
        elif obj.substance_id:
            CACHE.invalidate("transport", obj.substance_id)
    elif isinstance(obj, MaterialCompositionRow):
        if deleted:
            CACHE.invalidate_kind("compositions")
        else:
            CACHE.invalidate("compositions", obj.material_id)
    elif isinstance(obj, MaterialRow):
        CACHE.invalidate("material", obj.id)
        if deleted:
            # The material's compositions are keyed by material_id == id.
            CACHE.invalidate("compositions", obj.id)
    elif isinstance(obj, GhsStatementRow):
        CACHE.invalidate("statement", obj.code)


@event.listens_for(Session, "after_flush")
def _invalidate_on_write(session: Session, _ctx) -> None:
    # Stage 3 / SR-062: no more full clear. Every watched row invalidates
    # just its own entries (or its kind when the key is not safely
    # derivable), so an edit to one substance no longer wipes the whole
    # cache the compliance WebSocket relies on. No early return: one
    # flush can touch several kinds.
    for obj in session.new:
        if isinstance(obj, _WATCHED):
            _invalidate_row(obj, deleted=False)
    for obj in session.dirty:
        if isinstance(obj, _WATCHED):
            _invalidate_row(obj, deleted=False)
    for obj in session.deleted:
        if isinstance(obj, _WATCHED):
            _invalidate_row(obj, deleted=True)


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
