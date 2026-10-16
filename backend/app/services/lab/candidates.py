"""Reviewed lab values feeding other workspaces (方案 §5.3.1): SDS section 9 candidates and the
prediction / measurement pairs that calibrate the cockpit's conformal intervals."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import MeasurementRow
from ...db.models_lab import LabTaskRow, SampleRow
from ...db.models_plm import ProductRevisionRow
from .measurements import representative_values

# Lab metric → SDS section 9 property key (sds_builder.PROPERTIES).
SDS_PROPERTY_MAP: dict[str, str] = {
    "density_g_cm3": "density_g_cm3", "ph": "ph", "flash_point_c": "flash_point_c",
    "solids_content_pct": "solids_content_pct", "voc_g_l": "voc_g_l",
    "kinematic_viscosity_40c": "kinematic_viscosity_40c", "initial_boiling_point_c": "initial_boiling_point_c",
}


def sds_property_candidates(session: Session, revision_id: str) -> dict[str, Any]:
    """Section 9 values from the revision's winning experiment — reviewed measurements only — in the shape
    ``POST /plm/revisions/{id}/documents`` accepts as ``sds_properties``."""
    rev = session.get(ProductRevisionRow, revision_id)
    if rev is None:
        raise KeyError(revision_id)
    if rev.winning_experiment_id is None:
        return {"revision_id": rev.id, "experiment_id": None, "sds_properties": {},
                "notes": ["修订版没有定型实验（winning_experiment_id），无实测理化值可用"]}
    values = representative_values(session, rev.winning_experiment_id)
    props, unmapped = {}, []
    for metric, v in values.items():
        key = SDS_PROPERTY_MAP.get(metric)
        if key is None:
            unmapped.append(metric)
            continue
        m = session.get(MeasurementRow, v["measurement_ids"][-1])
        props[key] = {"value": v["value"], "source": "measured", "method": m.test_method if m else "",
                      "measurement_ids": v["measurement_ids"], "basis": v["basis"]}
    return {"revision_id": rev.id, "experiment_id": rev.winning_experiment_id, "sds_properties": props,
            "not_sds_properties": sorted(unmapped)}


def conformal_pairs(session: Session, metric: str, *, doe_plan_id: str | None = None,
                    project_id: str | None = None) -> list[dict[str, Any]]:
    """(predicted, reviewed actual) pairs, oldest first, from tasks dispatched with predictions."""
    q = select(LabTaskRow).where(LabTaskRow.status != "cancelled")
    if doe_plan_id:
        q = q.where(LabTaskRow.doe_plan_id == doe_plan_id)
    if project_id:
        q = q.where(LabTaskRow.project_id == project_id)
    pairs = []
    for task in session.execute(q.order_by(LabTaskRow.created_at)).scalars():
        req = next((r for r in task.requested_metrics or [] if r.get("metric") == metric and "predicted" in r), None)
        if req is None:
            continue
        sample = session.execute(select(SampleRow).where(SampleRow.lab_task_id == task.id)).scalars().first()
        if sample is None or sample.experiment_id is None:
            continue
        actual = representative_values(session, sample.experiment_id).get(metric)
        if actual is None:
            continue
        pairs.append({"id": task.task_no, "predicted": float(req["predicted"]), "actual": float(actual["value"]),
                      "sigma": req.get("sigma")})
    return pairs
