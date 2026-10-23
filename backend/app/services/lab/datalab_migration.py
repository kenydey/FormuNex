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


def _is_inline(ref: dict[str, Any]) -> bool:
    """A native ``sample_refs`` entry carries the row itself; a Datalab one is only ``{id, item_id}``."""
    return "planned_params" in ref


def migrate_campaigns(session: Session, fetch_item: Callable[[str], dict[str, Any] | None], *,
                      dry_run: bool = False, prune_missing: bool = False,
                      actor: str = "migration") -> dict[str, Any]:
    """Copy Datalab-backed workbench (campaign) rows into ``campaigns.sample_refs``.

    In Datalab mode a campaign's ``sample_refs`` are an index (``{id, item_id}``) and every row —
    planned / actual parameters, measurements, status, note, tags — lives in a Datalab item. The
    native store reads the same rows inline from ``sample_refs``. Migration fetches each item and
    writes the row inline, keeping ``item_id`` unchanged: training records are labelled
    ``wb:<campaign>:<item_id>`` and that label is their identity, so a new id would duplicate
    them. Each migrated row gets an ``eln_links`` entry back to its Datalab item. Idempotent: inline
    rows are skipped. An item Datalab no longer has (404) is reported and its ref kept as it is,
    unless ``prune_missing`` drops it.
    """
    from ...db.campaign_store import _parse_row_from_item
    from ...db.models import Campaign

    campaigns = session.execute(select(Campaign).order_by(Campaign.id)).scalars().all()
    report: dict[str, Any] = {"campaigns": 0, "rows": 0, "already_native": 0, "missing": [], "pruned": 0,
                              "errors": [], "dry_run": dry_run}
    for campaign in campaigns:
        refs = [dict(r) for r in (campaign.sample_refs or [])]
        if not refs or all(_is_inline(r) for r in refs):
            report["already_native"] += len(refs)
            continue
        new_refs: list[dict[str, Any]] = []
        moved = 0
        for ref in refs:
            if _is_inline(ref):
                report["already_native"] += 1
                new_refs.append(ref)
                continue
            item_id = str(ref.get("item_id") or "")
            where = {"campaign_id": campaign.id, "row_id": ref.get("id"), "item_id": item_id}
            try:
                item = fetch_item(item_id) if item_id else None
            except Exception as exc:  # noqa: BLE001 — report and carry on with the others
                report["errors"].append({**where, "error": f"{type(exc).__name__}: {exc}"})
                new_refs.append(ref)
                continue
            if item is None:
                report["missing"].append(where)
                if prune_missing:
                    report["pruned"] += 1
                else:
                    new_refs.append(ref)
                continue
            try:
                row = _parse_row_from_item(campaign.id, int(ref["id"]), item_id, item)
            except Exception as exc:  # noqa: BLE001
                report["errors"].append({**where, "error": f"无法解析：{exc}"})
                new_refs.append(ref)
                continue
            inline = {
                **ref,  # parent_* links and any other index fields survive
                "id": row.id, "item_id": item_id, "status": row.status,
                "planned_params": row.planned_params, "actual_params": row.actual_params,
                "measurements": row.measurements, "note": row.note, "tags": row.tags,
                "migrated_from": "datalab",
            }
            if row.refcode:
                inline["datalab_refcode"] = row.refcode
            new_refs.append(inline)
            moved += 1
            if not dry_run:
                session.add(ElnLinkRow(id=str(uuid.uuid4()), connector="datalab", external_id=item_id,
                                       local_type="campaign_row", local_id=f"{campaign.id}:{row.id}",
                                       last_synced_at=utcnow(), sync_state="ok"))
        report["rows"] += moved
        if moved or (prune_missing and len(new_refs) != len(refs)):
            report["campaigns"] += 1
            if not dry_run:
                campaign.sample_refs = new_refs  # a new list: the JSON column sees the change
                campaign.updated_at = utcnow()
    if not dry_run:
        session.flush()
        hash_chain.append(session, actor=actor, action="lab.datalab_migrate_campaigns", entity_type="campaign",
                          entity_id="*", detail={k: v for k, v in report.items() if k != "dry_run"})
    return report
