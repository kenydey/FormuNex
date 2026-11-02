"""One-time Datalab -> native import (Datalab removal, batch 1).

A Datalab-mode campaign keeps its rows as pointers in
``Campaign.sample_refs`` (``{"id", "item_id"}``); the row data itself lives
in Datalab items. A native campaign embeds the full row data in the same
JSON column. This tool materialises Datalab rows into native refs, so the
Datalab backend, client and connector can be deleted afterwards without
losing workbench data.

Scope, stated honestly:

- Rows: fetched per item and parsed with the same
  ``_parse_row_from_item`` the Datalab store itself uses, then written as
  local refs. Re-running is safe: refs that already embed row data are
  skipped, so a partial run resumes instead of duplicating.
- Version history: the Datalab versions API returns metadata only
  (id / version / action / timestamp / creator) — no per-version content
  snapshots exist to import. Each imported row therefore gets ONE audit
  entry (``workbench_row.import``, snapshot = current row fields) plus
  full provenance in the report (refcode, version count and ids). Anyone
  needing the old per-version contents must read them from Datalab
  before it is decommissioned; this tool does not invent snapshots.
- Measurements: the row's measurement block is imported inside the ref.
  The separate measurement store is untouched — rows synced there by
  past sync-datalab clicks already live locally and need no migration.
- Attachments: already local (ExperimentAttachment); the tool only
  reports per-row local attachment counts. Datalab file ids recorded in
  notes stay as provenance text.
- ``Campaign.datalab_collection_id`` is kept as provenance (the column
  stays; dropping columns is forbidden by migration discipline).

Writes are per-campaign transactions: refs and audit entries commit
together (``hash_chain.append`` joins the caller's transaction), so a
failure mid-run leaves earlier campaigns imported and later ones
untouched. Without ``--apply`` (the default) nothing is written.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_SNAPSHOT_FIELDS = ("status", "planned_params", "actual_params", "measurements", "note", "tags")


def is_datalab_pointer(ref: dict) -> bool:
    """A local ref embeds full row data; a Datalab ref is just a pointer."""
    return "planned_params" not in ref and "measurements" not in ref


def _local_ref(row, old_ref: dict) -> dict:
    ref = {
        "id": row.id,
        "item_id": row.item_id,
        "status": row.status,
        "planned_params": dict(row.planned_params or {}),
        "actual_params": dict(row.actual_params or {}),
        "measurements": dict(row.measurements or {}),
        "note": row.note,
        "tags": list(row.tags or []),
    }
    for key in ("parent_sample_id", "parent_campaign_id"):
        if old_ref.get(key) is not None:
            ref[key] = old_ref[key]
    return ref


def _snapshot(row) -> dict:
    return {
        "status": row.status,
        "planned_params": dict(row.planned_params or {}),
        "actual_params": dict(row.actual_params or {}),
        "measurements": dict(row.measurements or {}),
        "note": row.note,
        "tags": list(row.tags or []),
    }


def _attachment_counts(session, campaign_id: int, rows) -> dict:
    """Local attachment counts per imported row (same label mapping as the
    workbench API's counts endpoint; rows not ingested as experiments count 0)."""
    from sqlalchemy import func

    from ..db.models import ExperimentAttachment, ExperimentRow
    from ..services.workbench_training import workbench_record_label

    counts = {r.id: 0 for r in rows}
    if not rows:
        return counts
    label_by_row = {r.id: workbench_record_label(campaign_id, r.item_id) for r in rows}
    exp_by_label: dict[str, int] = {}
    for exp_id, label in (
        session.query(ExperimentRow.id, ExperimentRow.label)
        .filter(ExperimentRow.label.in_(set(label_by_row.values())))
        .order_by(ExperimentRow.id)
    ):
        exp_by_label.setdefault(label, exp_id)
    n_by_exp = dict(
        session.query(ExperimentAttachment.experiment_id, func.count(ExperimentAttachment.id))
        .filter(ExperimentAttachment.experiment_id.in_(set(exp_by_label.values()) or {0}))
        .group_by(ExperimentAttachment.experiment_id)
        .all()
    )
    for row_id, label in label_by_row.items():
        exp_id = exp_by_label.get(label)
        if exp_id is not None:
            counts[row_id] = int(n_by_exp.get(exp_id, 0))
    return counts


def run_import(
    *,
    session_factory,
    api_url: str,
    apply: bool,
    actor: str = "system",
    campaign_ids: list[int] | None = None,
    transport: Any = None,
) -> dict:
    """Import Datalab-pointer rows into native refs. Returns the report."""
    from ..db.campaign_store import _parse_row_from_item
    from ..db.datalab_client import list_item_versions
    from ..db.models import Campaign
    from ..services.audit import hash_chain
    from .datalab_sync import fetch_item_data

    report: dict[str, Any] = {
        "applied": apply,
        "campaigns": [],
        "totals": {"rows_imported": 0, "rows_skipped_native": 0, "rows_failed": 0,
                   "datalab_versions": 0, "attachments": 0},
    }
    with session_factory() as session:
        query = session.query(Campaign).order_by(Campaign.id)
        if campaign_ids:
            query = query.filter(Campaign.id.in_(campaign_ids))
        campaigns = query.all()
        for campaign in campaigns:
            refs = list(campaign.sample_refs or [])
            new_refs: list[dict] = []
            imported: list[tuple[dict, Any, dict]] = []  # (ref, row, version_info)
            failed: list[dict] = []
            skipped = 0
            for ref in refs:
                if not is_datalab_pointer(ref):
                    new_refs.append(ref)
                    skipped += 1
                    continue
                row_id = int(ref["id"])
                item_id = str(ref["item_id"])
                item_data = fetch_item_data(api_url, item_id, _transport=transport)
                if item_data is None:
                    failed.append({"row_id": row_id, "item_id": item_id, "error": "fetch failed"})
                    new_refs.append(ref)
                    continue
                try:
                    row = _parse_row_from_item(campaign.id, row_id, item_id, item_data)
                except Exception as exc:  # noqa: BLE001 - per-row failure, continue
                    failed.append({"row_id": row_id, "item_id": item_id, "error": str(exc)})
                    new_refs.append(ref)
                    continue
                versions = list_item_versions(api_url, item_data.get("refcode") or "", _transport=transport)
                version_info = {
                    "refcode": item_data.get("refcode"),
                    "version_count": len(versions),
                    "version_ids": [v.get("id") for v in versions],
                }
                new_refs.append(_local_ref(row, ref))
                imported.append((ref, row, version_info))
            camp_report = {
                "campaign_id": campaign.id,
                "rows_total": len(refs),
                "rows_imported": len(imported),
                "rows_skipped_native": skipped,
                "rows_failed": failed,
                "datalab_versions": sum(v["version_count"] for _, _, v in imported),
                "version_inventory": {str(row.id): v for _, row, v in imported},
                "attachments": _attachment_counts(session, campaign.id, [row for _, row, _ in imported]),
            }
            if apply and imported:
                campaign.sample_refs = new_refs
                session.add(campaign)
                for _, row, version_info in imported:
                    snap = _snapshot(row)
                    hash_chain.append(
                        session, actor=actor, action="workbench_row.import",
                        entity_type="workbench_row", entity_id=f"{campaign.id}:{row.id}",
                        after=snap,
                        detail={"campaign_id": campaign.id, "row_id": row.id, "snapshot": snap,
                                "datalab_refcode": version_info["refcode"],
                                "datalab_version_count": version_info["version_count"],
                                "datalab_version_ids": version_info["version_ids"]},
                    )
                session.commit()
            report["campaigns"].append(camp_report)
            report["totals"]["rows_imported"] += len(imported)
            report["totals"]["rows_skipped_native"] += skipped
            report["totals"]["rows_failed"] += len(failed)
            report["totals"]["datalab_versions"] += camp_report["datalab_versions"]
            report["totals"]["attachments"] += sum(camp_report["attachments"].values())
    return report
