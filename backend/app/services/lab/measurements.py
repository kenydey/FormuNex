"""Measurement entry, validation, review, amendment and value selection (方案 §5.3.1, M7).

Entry checks: the method exists and is active, the metric and unit match the
method, the value lies in the method's valid range (and is rounded per GB/T
8170 to the method's decimals), the instrument's calibration is not overdue.
Outliers against the other values of the same metric in the same DOE plan
(robust z-score) are flagged, not refused.

Review: a second person (``lab.review``, never the operator) accepts or
rejects each value. Only reviewed values reach the experiment's ``measured``
mirror — the view optimisation, SDS section 9 and the release gate read.
Amendments keep the original value and the reason; amending a reviewed value
sends it back to review. Repeated measurements are all kept; which one
represents the point follows ``lab_value_strategy`` unless a reviewer picks one.
"""
from __future__ import annotations

import statistics
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...clock import utcnow
from ...config import get_settings
from ...db.models import ExperimentRow, MeasurementRow
from ...db.models_lab import InstrumentRow, LabTaskRow, MeasurementAmendmentRow, MeasurementPickRow, SampleRow
from ..audit import hash_chain
from ..plm.documents.coa_builder import round_gbt8170
from . import tasks as task_svc
from .methods import LabError, LabForbidden, calibration, resolve_method

MIN_FOR_ANOMALY = 5


def measurement_dict(r: MeasurementRow) -> dict[str, Any]:
    return {"id": r.id, "experiment_id": r.experiment_id, "metric": r.metric, "value": r.value, "unit": r.unit,
            "test_method": r.test_method, "method_id": r.method_id, "instrument": r.instrument,
            "instrument_id": r.instrument_id, "operator": r.operator, "operator_id": r.operator_id,
            "sample_id": r.sample_id, "lab_task_id": r.lab_task_id, "review_status": r.review_status,
            "reviewed_by": r.reviewed_by, "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "raw_file_key": r.raw_file_key, "external_ref": r.external_ref, "note": r.note,
            "measured_at": r.measured_at.isoformat() if r.measured_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None}


def _check_value(method, metric: str, value: float, unit: str | None) -> tuple[float, str]:
    if method.metric and metric != method.metric:
        raise LabError(f"方法 {method.code} 测定的是 {method.metric}，不是 {metric}")
    if unit and method.unit and unit != method.unit:
        raise LabError(f"{metric}：单位 {unit} 与方法 {method.code} 规定的 {method.unit} 不一致")
    if (method.valid_min is not None and value < method.valid_min) or (
            method.valid_max is not None and value > method.valid_max):
        raise LabError(f"{metric} = {value:g} 超出方法 {method.code} 的有效范围 "
                       f"[{method.valid_min if method.valid_min is not None else '-∞'}, "
                       f"{method.valid_max if method.valid_max is not None else '+∞'}]")
    decimals = (method.rounding or {}).get("decimals")
    if decimals is not None:
        value = float(round_gbt8170(value, int(decimals)))
    return value, unit or method.unit or ""


def _instrument(session: Session, instrument_id: str | None, warnings: list[str]) -> InstrumentRow | None:
    if not instrument_id:
        return None
    instr = session.get(InstrumentRow, instrument_id)
    if instr is None:
        # 补齐计划 2-14: 批量录入网格里填的是资产编号（非 UUID），回退按 asset_no 解析。
        instr = session.execute(select(InstrumentRow).where(
            InstrumentRow.asset_no == instrument_id, InstrumentRow.active.is_(True))).scalars().first()
    if instr is None or not instr.active:
        raise LabError("仪器不存在或已停用")
    cal = calibration(instr)
    if cal.state == "overdue":
        msg = f"仪器 {instr.asset_no} 校准已于 {cal.due:%Y-%m-%d} 过期"
        if get_settings().lab_block_uncalibrated:
            raise LabError(msg + "，不能录入（请先更新校准记录）")
        warnings.append(msg)
    elif cal.state == "unknown":
        warnings.append(f"仪器 {instr.asset_no} 没有校准有效期记录")
    return instr


def _peer_values(session: Session, task: LabTaskRow, metric: str) -> list[float]:
    if not task.doe_plan_id:
        return []
    q = (select(MeasurementRow.value).join(LabTaskRow, LabTaskRow.id == MeasurementRow.lab_task_id)
         .where(LabTaskRow.doe_plan_id == task.doe_plan_id, MeasurementRow.metric == metric,
                MeasurementRow.review_status != "rejected"))
    return [v for (v,) in session.execute(q).all()]


def robust_z(value: float, peers: list[float]) -> float | None:
    if len(peers) < MIN_FOR_ANOMALY:
        return None
    med = statistics.median(peers)
    mad = statistics.median(abs(p - med) for p in peers)
    if mad == 0:
        return None if value == med else float("inf")
    return 0.6745 * (value - med) / mad


def record(session: Session, task_id: str, entries: list[dict[str, Any]], *, user_id: str, username: str,
           raw_file_key: str | None = None, external_ref: str | None = None) -> tuple[list[MeasurementRow], list[str]]:
    """Validate and store a batch of values for one task (all or nothing)."""
    task = task_svc.get_task(session, task_id)
    if task.status == "open" and task.assignee_id in (None, user_id):
        task_svc.claim(session, task.id, user_id)
    if task.status != "in_progress":
        raise LabError(f"任务单状态为 {task.status}，不能录入（需先领取；已提交的需复核驳回后补录）")
    if task.assignee_id != user_id:
        raise LabForbidden("任务单已指派给其他人")
    sample = task_svc.task_sample(session, task)
    requested = {r["metric"] for r in task.requested_metrics or []}
    warnings: list[str] = []
    rows = []
    z_limit = get_settings().lab_anomaly_z
    for e in entries:
        metric = str(e.get("metric") or "").strip()
        if not metric:
            raise LabError("测量值缺少指标名")
        try:
            raw = float(e["value"])
        except (KeyError, TypeError, ValueError):
            raise LabError(f"{metric}：数值无效") from None
        method = resolve_method(session, e.get("method_id"), e.get("method_code"))
        value, unit = _check_value(method, metric, raw, e.get("unit"))
        instr = _instrument(session, e.get("instrument_id"), warnings)
        if requested and metric not in requested:
            warnings.append(f"{metric} 不在任务单的待测指标中")
        z = robust_z(value, _peer_values(session, task, metric))
        note = str(e.get("note") or "")
        if z is not None and abs(z) > z_limit:
            flag = f"疑似异常值（稳健 z = {z:.1f}，同一 DOE 计划内比较）"
            warnings.append(f"{metric} = {value:g}：{flag}")
            note = f"{note}；{flag}" if note else flag
        if value != raw:
            note = (note + "；" if note else "") + f"按 GB/T 8170 修约（原始读数 {raw:g}）"
        row = MeasurementRow(
            id=str(uuid.uuid4()), experiment_id=sample.experiment_id, metric=metric[:80], value=value, unit=unit[:32],
            test_method=method.code[:80], method_id=method.id, instrument=(instr.name if instr else "")[:120],
            instrument_id=instr.id if instr else None, operator=username[:80], operator_id=user_id,
            measured_at=e.get("measured_at") or utcnow(), sample_id=sample.id, lab_task_id=task.id,
            raw_file_key=raw_file_key, review_status="unreviewed", external_ref=e.get("external_ref") or external_ref,
            note=note)
        session.add(row)
        rows.append(row)
    session.flush()
    hash_chain.append(session, actor=user_id, action="lab.measurement.record", entity_type="lab_task",
                      entity_id=task.id, detail={"measurements": [
                          {"id": r.id, "metric": r.metric, "value": r.value, "method": r.test_method} for r in rows],
                          "raw_file_key": raw_file_key})
    return rows, warnings


def record_external(session: Session, task: LabTaskRow, values: list[dict[str, Any]], *, connector: str,
                    external_id: str) -> tuple[list[MeasurementRow], list[str]]:
    """Values pulled from an external ELN: validated like typed ones, stored unreviewed, operator = connector."""
    sample = task_svc.task_sample(session, task)
    rows, errors = [], []
    for v in values:
        try:
            method = resolve_method(session, code=v.get("method_code")) if v.get("method_code") else _method_for(
                session, v["metric"])
            value, unit = _check_value(method, v["metric"], float(v["value"]), v.get("unit") or None)
        except LabError as exc:
            errors.append(f"{v.get('metric')}: {exc}")
            continue
        row = MeasurementRow(
            id=str(uuid.uuid4()), experiment_id=sample.experiment_id, metric=v["metric"][:80], value=value,
            unit=unit[:32], test_method=method.code[:80], method_id=method.id, operator=connector[:80],
            operator_id=None, measured_at=utcnow(), sample_id=sample.id, lab_task_id=task.id,
            review_status="unreviewed", external_ref=f"{connector}:{external_id}:{v['metric']}"[:200],
            note=f"来自 {connector}")
        session.add(row)
        rows.append(row)
    session.flush()
    if rows:
        hash_chain.append(session, actor=f"eln:{connector}", action="lab.measurement.pull", entity_type="lab_task",
                          entity_id=task.id, detail={"external_id": external_id, "measurements": [
                              {"id": r.id, "metric": r.metric, "value": r.value} for r in rows]})
    return rows, errors


def _method_for(session: Session, metric: str):
    from ...db.models_lab import TestMethodRow

    m = session.execute(select(TestMethodRow).where(TestMethodRow.metric == metric, TestMethodRow.active.is_(True))
                        .order_by(TestMethodRow.code)).scalars().first()
    if m is None:
        raise LabError(f"没有测定 {metric} 的检测方法")
    return m


def _get(session: Session, measurement_id: str) -> MeasurementRow:
    m = session.get(MeasurementRow, measurement_id)
    if m is None:
        raise LabError("测量记录不存在")
    return m


def review(session: Session, measurement_id: str, *, decision: str, user_id: str, reason: str = "") -> MeasurementRow:
    m = _get(session, measurement_id)
    if m.review_status != "unreviewed":
        raise LabError(f"该测量值状态为 {m.review_status}，不在复核队列中")
    if m.operator_id and m.operator_id == user_id:
        raise LabForbidden("录入人不能复核自己录入的数据")
    if decision not in ("accept", "reject"):
        raise LabError("decision 只能是 accept / reject")
    if decision == "reject" and not reason.strip():
        raise LabError("驳回必须说明理由")
    m.review_status = "reviewed" if decision == "accept" else "rejected"
    m.reviewed_by = user_id
    m.reviewed_at = utcnow()
    if reason:
        m.note = (m.note + "；" if m.note else "") + f"复核{'驳回' if decision == 'reject' else '意见'}：{reason}"
    hash_chain.append(session, actor=user_id, action=f"lab.measurement.{decision}", entity_type="measurement",
                      entity_id=m.id, detail={"metric": m.metric, "value": m.value, "reason": reason})
    sync_mirror(session, m.experiment_id)
    task = session.get(LabTaskRow, m.lab_task_id) if m.lab_task_id else None
    if task is not None:
        if decision == "reject" and task.status == "pending_review":
            task.status = "in_progress"  # back to the operator for a re-measurement
        task_svc.refresh_status(session, task)
    return m


def amend(session: Session, measurement_id: str, *, value: float | None = None, note: str | None = None,
          reason: str, user_id: str, can_review: bool) -> MeasurementRow:
    m = _get(session, measurement_id)
    if not reason.strip():
        raise LabError("修改必须说明理由")
    if m.operator_id != user_id and not can_review:
        raise LabForbidden("只有录入人或复核人可以修改测量值")
    changes: list[tuple[str, str, str]] = []
    if value is not None and float(value) != m.value:
        method = resolve_method(session, m.method_id, m.test_method if not m.method_id else None)
        new_value, _unit = _check_value(method, m.metric, float(value), m.unit)
        changes.append(("value", f"{m.value:g}", f"{new_value:g}"))
        m.value = new_value
    if note is not None and note != (m.note or ""):
        changes.append(("note", (m.note or "")[:200], note[:200]))
        m.note = note
    if not changes:
        raise LabError("没有需要修改的内容")
    for field, old, new in changes:
        session.add(MeasurementAmendmentRow(id=str(uuid.uuid4()), measurement_id=m.id, field=field, old_value=old,
                                            new_value=new, reason=reason, amended_by=user_id))
    was_reviewed = m.review_status == "reviewed"
    if m.review_status != "unreviewed":
        m.review_status = "unreviewed"  # any change goes back to review
        m.reviewed_by = None
        m.reviewed_at = None
    session.flush()
    hash_chain.append(session, actor=user_id, action="lab.measurement.amend", entity_type="measurement",
                      entity_id=m.id, detail={"changes": changes, "reason": reason, "was_reviewed": was_reviewed})
    sync_mirror(session, m.experiment_id)
    return m


def pick(session: Session, measurement_id: str, *, user_id: str, reason: str = "") -> MeasurementPickRow:
    m = _get(session, measurement_id)
    if m.review_status != "reviewed":
        raise LabError("只能指定已复核的测量值")
    row = session.execute(select(MeasurementPickRow).where(
        MeasurementPickRow.experiment_id == m.experiment_id, MeasurementPickRow.metric == m.metric)).scalar_one_or_none()
    if row is None:
        row = MeasurementPickRow(id=str(uuid.uuid4()), experiment_id=m.experiment_id, metric=m.metric,
                                 measurement_id=m.id, picked_by=user_id, reason=reason)
        session.add(row)
    else:
        row.measurement_id, row.picked_by, row.reason, row.picked_at = m.id, user_id, reason, utcnow()
    session.flush()
    hash_chain.append(session, actor=user_id, action="lab.measurement.pick", entity_type="measurement",
                      entity_id=m.id, detail={"metric": m.metric, "reason": reason})
    sync_mirror(session, m.experiment_id)
    return row


def representative_values(session: Session, experiment_id: int) -> dict[str, dict[str, Any]]:
    """Per metric: the value that represents this test point, from reviewed measurements only."""
    rows = session.execute(select(MeasurementRow).where(MeasurementRow.experiment_id == experiment_id,
                                                        MeasurementRow.review_status == "reviewed")
                           .order_by(MeasurementRow.created_at)).scalars().all()
    picks = {p.metric: p.measurement_id for p in session.execute(
        select(MeasurementPickRow).where(MeasurementPickRow.experiment_id == experiment_id)).scalars()}
    strategy = get_settings().lab_value_strategy
    by_metric: dict[str, list[MeasurementRow]] = {}
    for r in rows:
        by_metric.setdefault(r.metric, []).append(r)
    out = {}
    for metric, rs in by_metric.items():
        picked = next((r for r in rs if r.id == picks.get(metric)), None)
        if picked is not None:
            out[metric] = {"value": picked.value, "basis": "manual", "measurement_ids": [picked.id], "unit": picked.unit}
        elif strategy == "mean" and len(rs) > 1:
            out[metric] = {"value": statistics.fmean(r.value for r in rs), "basis": "mean",
                           "measurement_ids": [r.id for r in rs], "unit": rs[-1].unit}
        else:
            out[metric] = {"value": rs[-1].value, "basis": "latest", "measurement_ids": [rs[-1].id], "unit": rs[-1].unit}
    return out


def sync_mirror(session: Session, experiment_id: int) -> None:
    exp = session.get(ExperimentRow, experiment_id)
    if exp is None:
        return
    values = representative_values(session, experiment_id)
    # Keep values with no native rows (legacy imports, ``legacy`` rows migrated from Datalab) and replace the rest
    # with their reviewed representative — an unreviewed-only metric drops out of the mirror until reviewed.
    # P1-13(b) exception: a metric whose typed rows are all QC-origin
    # (``source_document_id`` set by a report upload, no lab row) keeps its
    # pre-existing mirror value instead of dropping out. The QC value itself
    # is only staged until reviewed, so dropping the previously known value
    # on an unrelated review event would silently lose training data; once
    # the QC value is reviewed it arrives here as the representative and
    # overwrites, as usual. Lab-origin unreviewed-only metrics still drop out
    # (amend → back-to-review must unpublish the value).
    rows = session.execute(select(MeasurementRow).where(
        MeasurementRow.experiment_id == experiment_id, MeasurementRow.review_status != "legacy")).scalars().all()
    native = {r.metric for r in rows}
    qc_staged = {r.metric for r in rows if r.source_document_id} - set(values)
    measured = {k: v for k, v in (exp.measured or {}).items() if k not in native or k in qc_staged}
    measured.update({k: v["value"] for k, v in values.items()})
    exp.measured = measured
    session.flush()


def review_queue(session: Session, *, project_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    q = (select(MeasurementRow, LabTaskRow.task_no, SampleRow.sample_no)
         .join(LabTaskRow, LabTaskRow.id == MeasurementRow.lab_task_id, isouter=True)
         .join(SampleRow, SampleRow.id == MeasurementRow.sample_id, isouter=True)
         .where(MeasurementRow.review_status == "unreviewed"))
    if project_id:
        q = q.where(LabTaskRow.project_id == project_id)
    out = []
    for m, task_no, sample_no in session.execute(q.order_by(MeasurementRow.created_at).limit(limit)).all():
        out.append({**measurement_dict(m), "task_no": task_no, "sample_no": sample_no})
    return out


def history(session: Session, measurement_id: str) -> list[dict[str, Any]]:
    _get(session, measurement_id)
    return [{"field": a.field, "old": a.old_value, "new": a.new_value, "reason": a.reason, "by": a.amended_by,
             "at": a.amended_at.isoformat()} for a in session.execute(
        select(MeasurementAmendmentRow).where(MeasurementAmendmentRow.measurement_id == measurement_id)
        .order_by(MeasurementAmendmentRow.amended_at)).scalars()]
