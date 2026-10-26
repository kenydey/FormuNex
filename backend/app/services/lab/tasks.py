"""Lab tasks (work orders) and samples (方案 §5.3.1, M7).

A DOE plan is dispatched as one task per run: the task carries the run's
factor settings and the metrics to measure (optionally with the model's
prediction, which later calibrates the cockpit's conformal intervals). Each
task gets a sample (barcode = sample number) and an experiment row that holds
its measurements; the experiment's flat ``measured`` mirror is filled only
from reviewed values.

Status: open → in_progress (claimed) → pending_review (submitted) → done
(every requested metric has a reviewed value) · cancelled.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...clock import utcnow
from ...db import doe_plan_store
from ...db.models import ExperimentRow, MeasurementRow, ProjectRow
from ...db.models_lab import LabTaskRow, SampleRow
from ...domain.project_spec import check_process_window
from ...domain.schemas import ProductDomain
from ..audit import hash_chain
from .methods import LabError, LabForbidden

ACTIVE = ("open", "in_progress", "pending_review")


def project_process_window(session: Session, project_id: str | None) -> dict[str, Any] | None:
    """补齐计划 2-10: 读项目的规格契约工艺窗口（spec_contract.process_window）。

    ProjectRow.payload 本身就是平铺的 workspace dict（见 project_store），
    不是嵌套在 "workspace" 键下；旧的嵌套读取恒为空，工艺窗口警告永不触发（P0-3）。
    找不到项目 / 无契约 / 无窗口 → None（调用方按无窗口处理，不报错）。
    """
    if not project_id:
        return None
    row = session.get(ProjectRow, project_id)
    if row is None:
        return None
    spec = (row.payload or {}).get("spec_contract") or {}
    window = spec.get("process_window")
    return dict(window) if isinstance(window, dict) and window else None


def project_process_window_custom(session: Session, project_id: str | None) -> list[dict] | None:
    """读项目的自定义工艺窗口（spec_contract.process_window_custom，与既有窗口平行）。"""
    if not project_id:
        return None
    row = session.get(ProjectRow, project_id)
    if row is None:
        return None
    spec = (row.payload or {}).get("spec_contract") or {}
    custom = spec.get("process_window_custom")
    if not isinstance(custom, list):
        return None
    params = [p for p in custom if isinstance(p, dict)]
    return params or None


def task_process_window_warnings(session: Session, project_id: str | None, factors: dict[str, Any]) -> list[str]:
    """补齐计划 2-10: 任务因子 vs 契约工艺窗口的偏离警告（只警告，不拦截）。"""
    return check_process_window(
        factors,
        project_process_window(session, project_id),
        project_process_window_custom(session, project_id),
    )


def _next_task_no(session: Session) -> str:
    prefix = f"LT-{utcnow():%Y%m%d}-"
    n = session.execute(select(func.count()).select_from(LabTaskRow).where(LabTaskRow.task_no.like(prefix + "%"))).scalar()
    return f"{prefix}{(n or 0) + 1:04d}"


def _requested(metrics: list[Any]) -> list[dict[str, Any]]:
    out = []
    for m in metrics:
        item = {"metric": m} if isinstance(m, str) else dict(m)
        if not item.get("metric"):
            raise LabError("待测指标缺少 metric")
        out.append(item)
    return out


def create_task(session: Session, *, factors: dict[str, Any], metrics: list[Any], domain: str, user_id: str,
                project_id: str | None = None, doe_plan_id: str | None = None, run_index: int | None = None,
                formulation_version_id: str | None = None, assignee_id: str | None = None, due_at=None,
                notes: str = "", substrate: str = "") -> LabTaskRow:
    try:
        domain = ProductDomain(domain).value
    except ValueError:
        raise LabError(f"未知产品领域 {domain}") from None
    task = LabTaskRow(id=str(uuid.uuid4()), task_no=_next_task_no(session), project_id=project_id,
                      doe_plan_id=doe_plan_id, run_index=run_index, formulation_version_id=formulation_version_id,
                      factors=dict(factors), requested_metrics=_requested(metrics), assignee_id=assignee_id,
                      status="open", due_at=due_at, notes=notes, created_by=user_id)
    session.add(task)
    session.flush()
    exp = ExperimentRow(domain=domain, project_id=project_id or "", factors=dict(factors), measured={},
                        source="lab_task", label=task.task_no)
    session.add(exp)
    session.flush()
    session.add(SampleRow(id=str(uuid.uuid4()), sample_no=f"{task.task_no}-S1", lab_task_id=task.id,
                          experiment_id=exp.id, substrate=substrate))
    session.flush()
    hash_chain.append(session, actor=user_id, action="lab.task.create", entity_type="lab_task", entity_id=task.id,
                      detail={"task_no": task.task_no, "doe_plan_id": doe_plan_id, "run_index": run_index})
    return task


def dispatch_plan(session: Session, plan_id: str, *, metrics: list[Any], user_id: str, domain: str | None = None,
                  project_id: str | None = None, predictions: dict[int, dict[str, Any]] | None = None,
                  assignee_id: str | None = None, include_infeasible: bool = False, due_at=None) -> dict[str, Any]:
    """One task per run of a stored DOE plan. Idempotent: runs that already have an active or finished task
    are skipped."""
    plan = doe_plan_store.load(session, plan_id)
    if plan is None:
        raise LabError("DOE 计划不存在")
    domain = domain or (plan.domain.value if plan.domain else None)
    if not domain:
        raise LabError("请指定产品领域（DOE 计划未记录 domain）")
    if not metrics:
        raise LabError("至少需要一个待测指标")
    existing = {r for (r,) in session.execute(select(LabTaskRow.run_index).where(
        LabTaskRow.doe_plan_id == plan_id, LabTaskRow.status != "cancelled")).all()}
    created, skipped = [], []
    warnings: list[dict[str, Any]] = []
    # 补齐计划 2-10: 契约工艺窗口只读一次，逐 run 比对因子，偏离只警告不拦截。
    window = project_process_window(session, project_id)
    for run in plan.runs:
        if run.run_id in existing:
            skipped.append({"run": run.run_id, "reason": "已有任务单"})
            continue
        if run.infeasible and not include_infeasible:
            skipped.append({"run": run.run_id, "reason": run.infeasible_reason or "不可行"})
            continue
        run_warnings = check_process_window(dict(run.natural), window)
        if run_warnings:
            warnings.append({"run": run.run_id, "warnings": run_warnings})
        pred = (predictions or {}).get(run.run_id) or {}
        reqs = []
        for m in _requested(metrics):
            p = pred.get(m["metric"])
            if isinstance(p, dict):
                m = {**m, **{k: p[k] for k in ("predicted", "sigma") if k in p}}
            elif isinstance(p, (int, float)):
                m = {**m, "predicted": float(p)}
            reqs.append(m)
        t = create_task(session, factors=dict(run.natural), metrics=reqs, domain=domain, user_id=user_id,
                        project_id=project_id, doe_plan_id=plan_id, run_index=run.run_id, assignee_id=assignee_id,
                        due_at=due_at)
        created.append(t)
    try:
        if created and plan.status == "draft":
            doe_plan_store.set_status(session, plan_id, "active")
    except (LookupError, ValueError):
        pass
    return {"created": created, "skipped": skipped, "process_window_warnings": warnings}


def task_sample(session: Session, task: LabTaskRow) -> SampleRow:
    s = session.execute(select(SampleRow).where(SampleRow.lab_task_id == task.id).order_by(SampleRow.created_at)
                        .limit(1)).scalar_one_or_none()
    if s is None:
        raise LabError("任务单没有样品")
    return s


def get_task(session: Session, task_id: str) -> LabTaskRow:
    t = session.get(LabTaskRow, task_id)
    if t is None:
        raise LabError("任务单不存在")
    return t


def claim(session: Session, task_id: str, user_id: str) -> LabTaskRow:
    t = get_task(session, task_id)
    if t.status not in ("open", "in_progress"):
        raise LabError(f"任务单状态为 {t.status}，不能领取")
    if t.assignee_id and t.assignee_id != user_id:
        raise LabForbidden("任务单已指派给其他人")
    t.assignee_id = user_id
    t.status = "in_progress"
    hash_chain.append(session, actor=user_id, action="lab.task.claim", entity_type="lab_task", entity_id=t.id)
    return t


def submit(session: Session, task_id: str, user_id: str) -> LabTaskRow:
    t = get_task(session, task_id)
    if t.status != "in_progress":
        raise LabError(f"任务单状态为 {t.status}，只能提交进行中的任务")
    if t.assignee_id != user_id:
        raise LabForbidden("只有领取人可以提交任务单")
    sample = task_sample(session, t)
    recorded = {m for (m,) in session.execute(select(MeasurementRow.metric).where(
        MeasurementRow.experiment_id == sample.experiment_id, MeasurementRow.review_status != "rejected")).all()}
    missing = [r["metric"] for r in t.requested_metrics or [] if r["metric"] not in recorded]
    if missing:
        raise LabError(f"以下指标尚未录入：{', '.join(missing)}")
    t.status = "pending_review"
    hash_chain.append(session, actor=user_id, action="lab.task.submit", entity_type="lab_task", entity_id=t.id)
    refresh_status(session, t)
    return t


def cancel(session: Session, task_id: str, user_id: str, reason: str) -> LabTaskRow:
    t = get_task(session, task_id)
    if t.status in ("done", "cancelled"):
        raise LabError(f"任务单状态为 {t.status}，不能取消")
    if not reason.strip():
        raise LabError("取消必须说明理由")
    t.status = "cancelled"
    t.notes = (t.notes + "\n" if t.notes else "") + f"取消：{reason}"
    hash_chain.append(session, actor=user_id, action="lab.task.cancel", entity_type="lab_task", entity_id=t.id,
                      detail={"reason": reason})
    return t


def refresh_status(session: Session, t: LabTaskRow) -> None:
    """pending_review → done once every requested metric has a reviewed value and nothing awaits review."""
    if t.status != "pending_review":
        return
    sample = task_sample(session, t)
    rows = session.execute(select(MeasurementRow.metric, MeasurementRow.review_status).where(
        MeasurementRow.experiment_id == sample.experiment_id)).all()
    reviewed = {m for m, st in rows if st == "reviewed"}
    pending = any(st == "unreviewed" for _m, st in rows)
    if not pending and all(r["metric"] in reviewed for r in t.requested_metrics or []):
        t.status = "done"
        t.updated_at = utcnow()


def task_dict(session: Session, t: LabTaskRow, *, with_measurements: bool = False) -> dict[str, Any]:
    sample = session.execute(select(SampleRow).where(SampleRow.lab_task_id == t.id)).scalars().first()
    out = {"id": t.id, "task_no": t.task_no, "status": t.status, "project_id": t.project_id,
           "doe_plan_id": t.doe_plan_id, "run_index": t.run_index, "factors": t.factors,
           "requested_metrics": t.requested_metrics, "assignee_id": t.assignee_id, "notes": t.notes,
           "due_at": t.due_at.isoformat() if t.due_at else None,
           "sample_no": sample.sample_no if sample else None,
           "experiment_id": sample.experiment_id if sample else None}
    if sample is not None and sample.experiment_id is not None:
        exp = session.get(ExperimentRow, sample.experiment_id)
        out["measured"] = exp.measured if exp else {}  # reviewed values only (see measurements.sync_mirror)
    if with_measurements and sample is not None:
        from .measurements import measurement_dict

        rows = session.execute(select(MeasurementRow).where(MeasurementRow.experiment_id == sample.experiment_id)
                               .order_by(MeasurementRow.created_at)).scalars().all()
        out["measurements"] = [measurement_dict(r) for r in rows]
    return out
