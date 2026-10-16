"""One-off migration of Datalab training records into FormuNex's own database (方案 §5.3.1 item 6).

In Datalab mode an ``experiments`` row is only an index (``item_id``) and the
factors / results live in a Datalab item. Migration copies them into the row,
adds one ``measurements`` row per result with ``review_status = "legacy"``
(kept, visible, used by optimisation as before, but not accepted by the release
gate until re-measured or reviewed), records an ``eln_links`` entry back to the
Datalab item and clears ``item_id`` so the native store owns the record.
Idempotent: rows already migrated have no ``item_id``.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...clock import utcnow
from ...db.models import ExperimentRow, MeasurementRow
from ...db.models_lab import ElnLinkRow
from ...db.store import _record_from_item_data
from ..audit import hash_chain


def migrate(session: Session, fetch_item: Callable[[str], dict[str, Any] | None], *, dry_run: bool = False,
            actor: str = "migration") -> dict[str, Any]:
    rows = session.execute(select(ExperimentRow).where(ExperimentRow.item_id.is_not(None))
                           .order_by(ExperimentRow.id)).scalars().all()
    report: dict[str, Any] = {"candidates": len(rows), "migrated": 0, "measurements": 0, "missing": [], "errors": [],
                              "dry_run": dry_run}
    for row in rows:
        item_id = str(row.item_id)
        try:
            item = fetch_item(item_id)
        except Exception as exc:  # noqa: BLE001 — report and carry on with the others
            report["errors"].append({"item_id": item_id, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if item is None:
            report["missing"].append(item_id)
            continue
        try:
            rec = _record_from_item_data(item)
        except Exception as exc:  # noqa: BLE001
            report["errors"].append({"item_id": item_id, "error": f"无法解析：{exc}"})
            continue
        measured = {k: v for k, v in rec.measured.items() if isinstance(v, (int, float))}
        report["migrated"] += 1
        report["measurements"] += len(measured)
        if dry_run:
            continue
        row.domain = rec.domain.value
        row.project_id = rec.project_id or row.project_id
        row.factors = rec.factors
        row.cure_temperature_c = rec.cure_temperature_c
        row.measured = dict(measured)
        row.source = rec.source or row.source
        row.label = rec.label or row.label
        have = set(session.execute(select(MeasurementRow.metric).where(
            MeasurementRow.experiment_id == row.id)).scalars())
        for metric, value in measured.items():
            if metric in have:
                continue
            session.add(MeasurementRow(id=str(uuid.uuid4()), experiment_id=row.id, metric=metric[:80],
                                       value=float(value), review_status="legacy", note="自 Datalab 迁移",
                                       external_ref=f"datalab:{item_id}:{metric}"[:200], measured_at=utcnow()))
        session.add(ElnLinkRow(id=str(uuid.uuid4()), connector="datalab", external_id=item_id, local_type="experiment",
                               local_id=str(row.id), last_synced_at=utcnow(), sync_state="ok"))
        row.item_id = None
    if not dry_run:
        session.flush()
        hash_chain.append(session, actor=actor, action="lab.datalab_migrate", entity_type="experiment",
                          entity_id="*", detail={k: v for k, v in report.items() if k != "dry_run"})
    return report
