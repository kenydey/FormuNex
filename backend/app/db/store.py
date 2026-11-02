"""Experiment persistence stores.

Two backends implement the same small interface so the ``ModelRegistry``
is agnostic to where data lives (the Datalab backend was removed; the
local main-DB ledger is the only production store):

* :class:`LocalExperimentStore` — SQLAlchemy-backed inline JSON (local main-DB store / tests).
* :class:`JsonExperimentStore` — single-file JSON store for lightweight tests and migration.

Interface: ``all() -> list[ExperimentRecord]``, ``add(records)``, ``clear()``.

``add`` is an upsert, not a blind append (P1-9/P1-10/P1-11): a record with a
non-empty ``label`` replaces the stored row carrying that label instead of
adding a second one, and a labelless record identical (domain, project,
factors, cure temperature, measured) to one already stored is skipped. The
training registry, the SQL lab-ledger mirror and the workbench "回灌并训练"
button all write through here, so a second sync / second click must not grow
the training set.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..services._fsutil import replace_with_retry
from ..domain.schemas import ExperimentRecord, Measurement
from .models import ExperimentRow
from .session_utils import commit_session

logger = logging.getLogger(__name__)

class ExperimentStore(Protocol):
    def all(self) -> list[ExperimentRecord]: ...
    def add(self, records: list[ExperimentRecord]) -> None: ...
    def clear(self) -> None: ...
    def close(self) -> None: ...


def _new_training_item_id() -> str:
    return f"formumind_exp_{uuid.uuid4().hex[:8]}"


def _coerce_factor_floats(raw: dict | None) -> dict[str, float | str]:
    """Keep numeric factor levers and discrete str values; drop metadata blobs."""
    # v8: 保留离散 str 因子值（如材料名），只丢 bool/dict/list 这类真正的 blob。
    out: dict[str, float | str] = {}
    for key, val in (raw or {}).items():
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)):
            out[str(key)] = float(val)
            continue
        if isinstance(val, str):
            try:
                out[str(key)] = float(val)
            except ValueError:
                out[str(key)] = val  # 离散因子值，保留
    return out


class JsonExperimentStore:
    """Single-file JSON persistence (the original v0.1 store)."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def all(self) -> list[ExperimentRecord]:
        with self._lock:
            if not self.path.exists():
                return []
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
            except (json.JSONDecodeError, FileNotFoundError) as exc:
                logger.warning("JSON store corrupted, resetting: %s", exc)
                return []
            out: list[ExperimentRecord] = []
            for row in raw:
                if not isinstance(row, dict):
                    continue
                try:
                    payload = dict(row)
                    payload["factors"] = _coerce_factor_floats(payload.get("factors"))
                    out.append(ExperimentRecord(**payload))
                except Exception as exc:
                    logger.warning("Skipping corrupt experiment JSON row: %s", exc)
            return out

    def _write(self, records: list[ExperimentRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        import tempfile

        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(self.path.parent),
            prefix=self.path.name,
            suffix=".tmp",
            delete=False,
        ) as fh:
            fh.write(json.dumps([r.model_dump() for r in records], indent=2, ensure_ascii=False))
            tmp_path = fh.name
        replace_with_retry(tmp_path, str(self.path))

    def add(self, records: list[ExperimentRecord]) -> None:
        """Upsert by label; skip labelless records identical to one stored."""
        with self._lock:
            current = self.all()
            for rec in records:
                if rec.label:
                    match = next((r for r in current if r.label == rec.label), None)
                    if match is not None:
                        if _same_training_values(match, rec):
                            continue
                        _copy_record_values(match, rec)
                        continue
                    current.append(rec)
                elif any(not r.label and _same_training_values(r, rec) for r in current):
                    continue
                else:
                    current.append(rec)
            self._write(current)

    def clear(self) -> None:
        with self._lock:
            self._write([])

    def close(self) -> None:
        return None


def _same_training_values(a: ExperimentRecord, b: ExperimentRecord) -> bool:
    """Training-relevant equality: label is identity, these are the payload."""
    return (
        a.domain == b.domain
        and a.project_id == b.project_id
        and a.factors == b.factors
        and a.cure_temperature_c == b.cure_temperature_c
        and a.measured == b.measured
    )


def _copy_record_values(target: ExperimentRecord, rec: ExperimentRecord) -> None:
    """Copy training values between records. ``measured`` is a computed
    read-only view over ``measurements``, so it must be written via the list."""
    target.domain = rec.domain
    target.project_id = rec.project_id
    target.factors = dict(rec.factors)
    target.cure_temperature_c = rec.cure_temperature_c
    target.measurements = [Measurement(metric=k, value=v) for k, v in rec.measured.items()]
    target.source = rec.source


def _apply_record_to_row(row: ExperimentRow, rec: ExperimentRecord) -> None:
    row.domain = rec.domain.value
    row.project_id = rec.project_id
    row.factors = dict(rec.factors)
    row.cure_temperature_c = rec.cure_temperature_c
    row.measured = dict(rec.measured)
    row.source = rec.source
    row.label = rec.label


def _row_to_record(row: ExperimentRow) -> ExperimentRecord:
    return ExperimentRecord(
        domain=row.domain,
        project_id=row.project_id or "",
        factors=_coerce_factor_floats(row.factors),
        cure_temperature_c=row.cure_temperature_c,
        measured=row.measured,
        source=row.source,
        label=row.label,
    )


def _record_to_row(rec: ExperimentRecord) -> ExperimentRow:
    return ExperimentRow(
        domain=rec.domain.value,
        project_id=rec.project_id,
        factors=rec.factors,
        cure_temperature_c=rec.cure_temperature_c,
        measured=rec.measured,
        source=rec.source,
        label=rec.label,
    )


class LocalExperimentStore:
    """SQLAlchemy-backed experiment store (main database, whatever dialect is configured)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._write_lock = threading.Lock()

    def all(self) -> list[ExperimentRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ExperimentRow)
                .where(ExperimentRow.item_id.is_(None))
                .order_by(ExperimentRow.id)
            ).all()
            out: list[ExperimentRecord] = []
            for r in rows:
                try:
                    out.append(_row_to_record(r))
                except Exception as exc:
                    logger.warning("Skipping corrupt experiment SQL row id=%s: %s", getattr(r, "id", "?"), exc)
            return out

    def add(self, records: list[ExperimentRecord]) -> None:
        """Upsert by label on the ``item_id IS NULL`` partition; skip labelless exact duplicates.

        This is the P1-9 fix at the persistence layer: the SQL lab-ledger
        mirror (``persist_workbench_lab_ledger``) already wrote one row per
        ``wb:{campaign}:{item_id}`` label, so a second insert here used to
        double the training set on every restart reload.
        """
        if not records:
            return
        with self._write_lock, commit_session(self._session_factory) as session:
            existing = session.scalars(
                select(ExperimentRow).where(ExperimentRow.item_id.is_(None))
            ).all()
            by_label = {r.label: r for r in existing if r.label}
            for rec in records:
                if rec.label and rec.label in by_label:
                    row = by_label[rec.label]
                    if _same_training_values(_row_to_record(row), rec):
                        continue
                    _apply_record_to_row(row, rec)
                    continue
                if not rec.label and any(
                    not r.label and _same_training_values(_row_to_record(r), rec)
                    for r in existing
                ):
                    continue
                row = _record_to_row(rec)
                session.add(row)
                existing.append(row)
                if rec.label:
                    by_label[rec.label] = row

    def clear(self) -> None:
        with self._write_lock, commit_session(self._session_factory) as session:
            session.execute(delete(ExperimentRow).where(ExperimentRow.item_id.is_(None)))

    def count(self) -> int:
        with self._session_factory() as session:
            return len(
                session.scalars(select(ExperimentRow.id).where(ExperimentRow.item_id.is_(None))).all()
            )

    def close(self) -> None:
        return None


_store: ExperimentStore | None = None


def get_experiment_store(settings: Settings | None = None) -> ExperimentStore:
    """The experiment store is always the local main-DB ledger (Datalab removal
    batch 2: the DatalabExperimentStore and the datalab/auto switch are gone).
    ``settings`` is accepted for call-site compatibility; only the deprecated
    ``sqlite`` alias is still honoured (with a warning)."""
    global _store
    if _store is not None:
        return _store
    s = settings or get_settings()
    from .database import default_session_factory

    factory = default_session_factory()
    backend = (s.experiment_backend or "native").lower()
    if backend == "sqlite":
        # Phase 2D (2-24): "sqlite" is a deprecated alias of "native" (same as campaign_backend).
        logger.warning("experiment_backend 'sqlite' is a deprecated alias of 'native'")
        backend = "native"
    # native (default): FormuNex's own database is the system of record (方案 v1.1 §5.3.1).
    if backend not in ("native",):
        logger.warning("Unknown experiment_backend %r; using the native store", backend)
    _store = LocalExperimentStore(factory)
    return _store


def reset_experiment_store(store: ExperimentStore | None = None) -> None:
    """Test helper — inject a store or clear the singleton."""
    global _store
    _store = store
