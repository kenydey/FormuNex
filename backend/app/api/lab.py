"""Lab execution (方案 v1.1 §5.3.1, M7): method library, instruments, tasks from DOE plans, samples,
measurement entry / instrument file import, second-person review, amendments, value picks, ELN sync."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from ._principal import CurrentUser, current_user

router = APIRouter(prefix="/lab", tags=["lab"])

MAX_IMPORT_BYTES = 10 * 1024 * 1024


def _session():
    from ..db.database import default_session_factory

    return default_session_factory()()


def _call(session, fn, *args, **kwargs):
    from ..services.eln.connectors.base import ConnectorError
    from ..services.lab.methods import LabError, LabForbidden

    try:
        return fn(session, *args, **kwargs)
    except LabForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except (LabError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc).strip("'")) from None
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None


# --------------------------------------------------------------------------- methods & instruments


class MethodIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    name_zh: str = ""
    name_en: str = ""
    metric: str = Field(default="", max_length=80)
    unit: str = Field(default="", max_length=32)
    rounding: dict[str, Any] = Field(default_factory=dict)  # {"decimals": n}
    valid_min: float | None = None
    valid_max: float | None = None
    active: bool = True


class InstrumentIn(BaseModel):
    asset_no: str = Field(min_length=1, max_length=64)
    name: str = ""
    model: str = ""
    location: str = ""
    calibrated_at: datetime | None = None
    calibration_due: datetime | None = None
    parser: str = ""
    active: bool = True


@router.get("/methods")
def list_methods(metric: str | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from ..db.models_lab import TestMethodRow
    from ..services.lab.methods import method_dict

    with _session() as session:
        q = select(TestMethodRow).order_by(TestMethodRow.code)
        if metric:
            q = q.where(TestMethodRow.metric == metric)
        return [method_dict(m) for m in session.execute(q).scalars()]


@router.post("/methods/seed")
def seed_methods(user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.lab.methods import seed_methods as seed

    user.require("lab.review")
    with _session() as session:
        n = seed(session)
        session.commit()
    return {"added": n}


@router.post("/methods", status_code=201)
def create_method(body: MethodIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.lab.methods import method_dict, save_method

    user.require("lab.review")
    with _session() as session:
        m = _call(session, save_method, body.model_dump())
        session.commit()
        return method_dict(m)


@router.put("/methods/{method_id}")
def update_method(method_id: str, body: MethodIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.lab.methods import method_dict, save_method

    user.require("lab.review")
    with _session() as session:
        m = _call(session, save_method, body.model_dump(), method_id)
        session.commit()
        return method_dict(m)


@router.get("/instruments")
def list_instruments() -> list[dict[str, Any]]:
    from sqlalchemy import select

    from ..db.models_lab import InstrumentRow
    from ..services.lab.methods import instrument_dict

    with _session() as session:
        return [instrument_dict(i) for i in session.execute(select(InstrumentRow).order_by(InstrumentRow.asset_no)).scalars()]


@router.post("/instruments", status_code=201)
def create_instrument(body: InstrumentIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.lab.methods import instrument_dict, save_instrument

    user.require("lab.review")
    with _session() as session:
        i = _call(session, save_instrument, body.model_dump())
        session.commit()
        return instrument_dict(i)


@router.put("/instruments/{instrument_id}")
def update_instrument(instrument_id: str, body: InstrumentIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.lab.methods import instrument_dict, save_instrument

    user.require("lab.review")
    with _session() as session:
        i = _call(session, save_instrument, body.model_dump(), instrument_id)
        session.commit()
        return instrument_dict(i)


# --------------------------------------------------------------------------- tasks & samples


class DispatchIn(BaseModel):
    doe_plan_id: str
    metrics: list[str | dict[str, Any]] = Field(min_length=1, max_length=30)
    domain: str | None = None
    project_id: str | None = None
    # run id → {metric: predicted value | {"predicted", "sigma"}} (calibrates conformal intervals later)
    predictions: dict[int, dict[str, Any]] = Field(default_factory=dict)
    assignee_id: str | None = None
    include_infeasible: bool = False
    due_at: datetime | None = None


class TaskIn(BaseModel):
    factors: dict[str, Any] = Field(default_factory=dict)
    metrics: list[str | dict[str, Any]] = Field(min_length=1, max_length=30)
    domain: str
    project_id: str | None = None
    formulation_version_id: str | None = None
    assignee_id: str | None = None
    due_at: datetime | None = None
    notes: str = ""
    substrate: str = ""


class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=2000)


@router.post("/tasks/dispatch", status_code=201)
def dispatch_plan(body: DispatchIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    """One lab task (+ sample + experiment record) per run of a stored DOE plan; idempotent per run."""
    from ..services.lab import tasks

    user.require("doe.write", body.project_id)
    with _session() as session:
        out = _call(session, tasks.dispatch_plan, body.doe_plan_id, metrics=body.metrics, user_id=user.id,
                    domain=body.domain, project_id=body.project_id, predictions=body.predictions,
                    assignee_id=body.assignee_id, include_infeasible=body.include_infeasible, due_at=body.due_at)
        session.commit()
        return {"created": [tasks.task_dict(session, t) for t in out["created"]], "skipped": out["skipped"]}


@router.post("/tasks", status_code=201)
def create_task(body: TaskIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.lab import tasks

    user.require("doe.write", body.project_id)
    with _session() as session:
        t = _call(session, tasks.create_task, factors=body.factors, metrics=body.metrics, domain=body.domain,
                  user_id=user.id, project_id=body.project_id, formulation_version_id=body.formulation_version_id,
                  assignee_id=body.assignee_id, due_at=body.due_at, notes=body.notes, substrate=body.substrate)
        session.commit()
        return tasks.task_dict(session, t)


@router.get("/tasks")
def list_tasks(status: str | None = None, project_id: str | None = None, doe_plan_id: str | None = None,
               assignee_id: str | None = None, limit: int = Query(200, ge=1, le=1000)) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from ..db.models_lab import LabTaskRow
    from ..services.lab.tasks import task_dict

    with _session() as session:
        q = select(LabTaskRow)
        for col, val in ((LabTaskRow.status, status), (LabTaskRow.project_id, project_id),
                         (LabTaskRow.doe_plan_id, doe_plan_id), (LabTaskRow.assignee_id, assignee_id)):
            if val:
                q = q.where(col == val)
        rows = session.execute(q.order_by(LabTaskRow.task_no).limit(limit)).scalars().all()
        return [task_dict(session, t) for t in rows]


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    from ..services.lab import tasks

    with _session() as session:
        t = _call(session, tasks.get_task, task_id)
        return tasks.task_dict(session, t, with_measurements=True)


@router.post("/tasks/{task_id}/claim")
def claim_task(task_id: str, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.lab import tasks

    user.require("lab.execute")
    with _session() as session:
        t = _call(session, tasks.claim, task_id, user.id)
        session.commit()
        return tasks.task_dict(session, t)


@router.post("/tasks/{task_id}/submit")
def submit_task(task_id: str, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.lab import tasks

    user.require("lab.execute")
    with _session() as session:
        t = _call(session, tasks.submit, task_id, user.id)
        session.commit()
        return tasks.task_dict(session, t)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str, body: ReasonIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.lab import tasks

    user.require("doe.write")
    with _session() as session:
        t = _call(session, tasks.cancel, task_id, user.id, body.reason)
        session.commit()
        return tasks.task_dict(session, t)


@router.get("/samples/{sample_no}")
def scan_sample(sample_no: str) -> dict[str, Any]:
    """Barcode scan: the sample and its task."""
    from sqlalchemy import select

    from ..db.models_lab import LabTaskRow, SampleRow
    from ..services.lab.tasks import task_dict

    with _session() as session:
        s = session.execute(select(SampleRow).where(SampleRow.sample_no == sample_no)).scalar_one_or_none()
        if s is None:
            raise HTTPException(status_code=404, detail="样品不存在")
        task = session.get(LabTaskRow, s.lab_task_id) if s.lab_task_id else None
        return {"sample_no": s.sample_no, "experiment_id": s.experiment_id, "substrate": s.substrate,
                "task": task_dict(session, task, with_measurements=True) if task else None}


# --------------------------------------------------------------------------- measurements


class MeasurementIn(BaseModel):
    metric: str = Field(min_length=1, max_length=80)
    value: float
    unit: str | None = None
    method_id: str | None = None
    method_code: str | None = None
    instrument_id: str | None = None
    measured_at: datetime | None = None
    note: str = Field(default="", max_length=1000)


class MeasurementsIn(BaseModel):
    measurements: list[MeasurementIn] = Field(min_length=1, max_length=200)


class ReviewIn(BaseModel):
    decision: Literal["accept", "reject"]
    reason: str = Field(default="", max_length=2000)


class AmendIn(BaseModel):
    value: float | None = None
    note: str | None = Field(default=None, max_length=1000)
    reason: str = Field(min_length=1, max_length=2000)


def _measurement_response(session, rows, warnings) -> dict[str, Any]:
    from ..services.lab.measurements import measurement_dict

    return {"measurements": [measurement_dict(r) for r in rows], "warnings": warnings}


@router.post("/tasks/{task_id}/measurements", status_code=201)
def record_measurements(task_id: str, body: MeasurementsIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.lab import measurements as meas

    user.require("lab.execute")
    with _session() as session:
        rows, warnings = _call(session, meas.record, task_id, [m.model_dump() for m in body.measurements],
                               user_id=user.id, username=user.username)
        session.commit()
        return _measurement_response(session, rows, warnings)


@router.get("/parsers")
def list_parsers() -> dict[str, Any]:
    from ..services.lab.parsers import BUILTIN_TEMPLATES

    return BUILTIN_TEMPLATES


@router.post("/measurements/import", status_code=201)
async def import_instrument_file(
    file: UploadFile = File(...),
    template: str = Form(..., description="内置模板名，或模板 JSON"),
    methods: str = Form("{}", description='指标 → 方法编号的 JSON，如 {"color_l": "GB/T 11186.3"}'),
    instrument_id: str | None = Form(None),
    user: CurrentUser = Depends(current_user),
) -> dict[str, Any]:
    """Parse an instrument export and record each sample's values on its task (unreviewed)."""
    from sqlalchemy import select

    from ..db.models_lab import SampleRow
    from ..services.lab import measurements as meas
    from ..services.lab.methods import LabError
    from ..services.lab.parsers import BUILTIN_TEMPLATES, parse
    from ..services.plm import storage

    user.require("lab.execute")
    data = await file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="文件过大（上限 10 MB）")
    try:
        tpl = BUILTIN_TEMPLATES[template] if template in BUILTIN_TEMPLATES else json.loads(template)
        method_map = json.loads(methods or "{}")
    except ValueError:
        raise HTTPException(status_code=422, detail="template / methods 不是有效 JSON 或内置模板名") from None
    if not isinstance(tpl, dict) or not isinstance(method_map, dict):
        raise HTTPException(status_code=422, detail="template / methods 必须是 JSON 对象")
    try:
        readings, warnings = parse(data, tpl, filename=file.filename or "")
    except LabError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    import uuid

    name = (file.filename or "upload").replace("/", "_").replace("\\", "_")[-120:]
    raw_key = storage.put(f"lab/raw/{uuid.uuid4().hex}/{name}", data)
    by_sample: dict[str, list[dict[str, Any]]] = {}
    for r in readings:
        by_sample.setdefault(r.sample_no, []).append(
            {"metric": r.metric, "value": r.value, "unit": r.unit, "method_code": method_map.get(r.metric),
             "instrument_id": instrument_id,
             "note": f"仪器文件 {name}" + (f"，{r.readings} 次读数" if r.readings > 1 else "")})
    recorded, errors = [], []
    with _session() as session:
        for sample_no, entries in by_sample.items():
            s = session.execute(select(SampleRow).where(SampleRow.sample_no == sample_no)).scalar_one_or_none()
            if s is None or not s.lab_task_id:
                errors.append({"sample_no": sample_no, "error": "样品不存在或未关联任务单"})
                continue
            skipped = sorted({e["metric"] for e in entries if not e["method_code"]})
            entries = [e for e in entries if e["method_code"]]
            if skipped:
                warnings.append(f"{sample_no}：未指定检测方法，已跳过 {', '.join(skipped)}")
            if not entries:
                errors.append({"sample_no": sample_no, "error": "没有指定了检测方法的指标"})
                continue
            try:
                with session.begin_nested():
                    rows, w = meas.record(session, s.lab_task_id, entries, user_id=user.id, username=user.username,
                                          raw_file_key=raw_key)
                recorded += rows
                warnings += [f"{sample_no}：{x}" for x in w]
            except LabError as exc:
                errors.append({"sample_no": sample_no, "error": str(exc)})
        session.commit()
        out = _measurement_response(session, recorded, warnings)
    out.update({"errors": errors, "raw_file_key": raw_key, "readings": len(readings)})
    return out


@router.get("/review-queue")
def review_queue(project_id: str | None = None, limit: int = Query(200, ge=1, le=1000)) -> list[dict[str, Any]]:
    from ..services.lab import measurements as meas

    with _session() as session:
        return meas.review_queue(session, project_id=project_id, limit=limit)


@router.post("/measurements/{measurement_id}/review")
def review_measurement(measurement_id: str, body: ReviewIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.lab import measurements as meas

    user.require("lab.review")
    with _session() as session:
        m = _call(session, meas.review, measurement_id, decision=body.decision, user_id=user.id, reason=body.reason)
        session.commit()
        return meas.measurement_dict(m)


@router.post("/measurements/{measurement_id}/amend")
def amend_measurement(measurement_id: str, body: AmendIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.lab import measurements as meas

    if not (user.principal.can("lab.execute") or user.principal.can("lab.review")):
        raise HTTPException(status_code=403, detail="缺少权限 lab.execute")
    with _session() as session:
        m = _call(session, meas.amend, measurement_id, value=body.value, note=body.note, reason=body.reason,
                  user_id=user.id, can_review=user.principal.can("lab.review"))
        session.commit()
        return meas.measurement_dict(m)


@router.post("/measurements/{measurement_id}/pick")
def pick_measurement(measurement_id: str, body: ReasonIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.lab import measurements as meas

    user.require("lab.review")
    with _session() as session:
        row = _call(session, meas.pick, measurement_id, user_id=user.id, reason=body.reason)
        session.commit()
        return {"experiment_id": row.experiment_id, "metric": row.metric, "measurement_id": row.measurement_id,
                "values": meas.representative_values(session, row.experiment_id)}


@router.get("/measurements/{measurement_id}/history")
def measurement_history(measurement_id: str) -> list[dict[str, Any]]:
    from ..services.lab import measurements as meas

    with _session() as session:
        return _call(session, meas.history, measurement_id)


# --------------------------------------------------------------------------- ELN connectors


class PushIn(BaseModel):
    task_ids: list[str] = Field(min_length=1, max_length=500)


class PullIn(BaseModel):
    task_ids: list[str] | None = None


class ResolveIn(BaseModel):
    resolution: Literal["keep_local", "take_remote"]


@router.get("/eln/connectors")
def eln_connectors() -> list[dict[str, Any]]:
    from ..config import get_settings
    from ..services.eln.sync import CONNECTORS

    s = get_settings()
    configured = {"elabftw": bool(s.lab_elabftw_url and s.lab_elabftw_api_key), "datalab": bool(s.datalab_api_url)}
    return [{"name": n, "configured": configured[n], "can_push": n != "datalab"} for n in CONNECTORS]


@router.post("/eln/{connector}/push")
def eln_push(connector: str, body: PushIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.eln import sync

    user.require("doe.write")
    with _session() as session:
        conn = _call(session, lambda _s: sync.get_connector(connector))
        out = _call(session, sync.push_tasks, conn, body.task_ids, user_id=user.id)
        session.commit()
        return out


@router.post("/eln/{connector}/pull")
def eln_pull(connector: str, body: PullIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.eln import sync

    user.require("lab.review")
    with _session() as session:
        conn = _call(session, lambda _s: sync.get_connector(connector))
        out = _call(session, sync.pull, conn, task_ids=body.task_ids)
        session.commit()
        return out


@router.get("/eln/conflicts")
def eln_conflicts(status: str = "open") -> list[dict[str, Any]]:
    from sqlalchemy import select

    from ..db.models_lab import ElnLinkRow, ElnSyncConflictRow, LabTaskRow

    with _session() as session:
        rows = session.execute(select(ElnSyncConflictRow, ElnLinkRow).join(
            ElnLinkRow, ElnLinkRow.id == ElnSyncConflictRow.link_id).where(ElnSyncConflictRow.status == status)
            .order_by(ElnSyncConflictRow.created_at)).all()
        out = []
        for c, link in rows:
            task = session.get(LabTaskRow, link.local_id)
            out.append({"id": c.id, "connector": link.connector, "external_id": link.external_id,
                        "external_url": link.external_url, "task_no": task.task_no if task else None,
                        "local": c.local_snapshot, "remote": c.remote_snapshot, "status": c.status,
                        "resolution": c.resolution, "created_at": c.created_at.isoformat()})
        return out


@router.post("/eln/conflicts/{conflict_id}/resolve")
def eln_resolve(conflict_id: str, body: ResolveIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.eln import sync

    user.require("lab.review")
    with _session() as session:
        c = _call(session, sync.resolve_conflict, conflict_id, resolution=body.resolution, user_id=user.id)
        session.commit()
        return {"id": c.id, "status": c.status, "resolution": c.resolution}
