"""Idempotent task push / measurement pull between FormuNex and an external ELN, with a conflict queue.

Each pushed task has an ``eln_links`` row. Its ``content_hash`` stores two
32-character digests: the remote values and the local mirrored values as of the
last sync. A pull then knows who changed what:

* remote unchanged → nothing to do (local edits stay: FormuNex is the system of record);
* remote changed, local unchanged → new values are recorded (unreviewed, into the review queue);
* both changed → an ``eln_sync_conflicts`` row waits for a person (keep_local / take_remote).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...clock import utcnow
from ...config import get_settings
from ...db.models import MeasurementRow
from ...db.models_lab import ElnLinkRow, ElnSyncConflictRow, LabTaskRow, TestMethodRow
from ..audit import hash_chain
from ..lab import measurements as meas
from ..lab import tasks as task_svc
from ..lab.methods import LabError
from .connectors.base import ConnectorError, ElnConnector

CONNECTORS = ("elabftw", "datalab")


def get_connector(name: str) -> ElnConnector:
    s = get_settings()
    if name == "elabftw":
        if not s.lab_elabftw_url:
            raise ConnectorError("未配置 eLabFTW（FORMUNEX_LAB_ELABFTW_URL / FORMUNEX_LAB_ELABFTW_API_KEY）")
        from .connectors.elabftw import ElabftwConnector

        return ElabftwConnector(s.lab_elabftw_url, s.lab_elabftw_api_key)
    if name == "datalab":
        from ...db.datalab_client import datalab_headers
        from .connectors.datalab import DatalabConnector

        return DatalabConnector(s.datalab_api_url, headers=datalab_headers(), timeout=s.datalab_timeout_seconds)
    raise ConnectorError(f"未知 ELN 连接器 {name}")


def _digest(state: dict[str, float]) -> str:
    return hashlib.sha256(json.dumps(sorted(state.items())).encode()).hexdigest()[:32]


def _local_state(session: Session, connector: str, external_id: str) -> dict[str, float]:
    rows = session.execute(select(MeasurementRow).where(
        MeasurementRow.external_ref.like(f"{connector}:{external_id}:%"),
        MeasurementRow.review_status != "rejected").order_by(MeasurementRow.created_at)).scalars().all()
    return {r.metric: r.value for r in rows}


def push_tasks(session: Session, connector: ElnConnector, task_ids: list[str], *, user_id: str) -> dict[str, Any]:
    if not connector.can_push:
        raise ConnectorError(f"{connector.name} 连接器不支持推送任务单")
    pushed, skipped, errors = [], [], []
    for tid in task_ids:
        task = session.get(LabTaskRow, tid)
        if task is None:
            errors.append({"task_id": tid, "error": "任务单不存在"})
            continue
        if session.execute(select(ElnLinkRow).where(ElnLinkRow.connector == connector.name,
                                                    ElnLinkRow.local_type == "lab_task",
                                                    ElnLinkRow.local_id == task.id)).first():
            skipped.append(task.task_no)
            continue
        payload = task_svc.task_dict(session, task)
        reqs = []
        for r in payload["requested_metrics"]:
            if not r.get("method_code"):
                m = session.execute(select(TestMethodRow).where(TestMethodRow.metric == r["metric"],
                                                                TestMethodRow.active.is_(True))).scalars().first()
                if m is not None:
                    r = {**r, "method_code": m.code, "unit": r.get("unit") or m.unit}
            reqs.append(r)
        payload["requested_metrics"] = reqs
        try:
            rec = connector.push_task(payload)
        except ConnectorError as exc:
            errors.append({"task_id": tid, "error": str(exc)})
            continue
        empty = _digest({})
        session.add(ElnLinkRow(id=str(uuid.uuid4()), connector=connector.name, external_id=rec.external_id,
                               external_url=rec.url, local_type="lab_task", local_id=task.id,
                               content_hash=empty + empty, last_synced_at=utcnow(), sync_state="ok"))
        pushed.append({"task_no": task.task_no, "external_id": rec.external_id, "url": rec.url})
    session.flush()
    if pushed:
        hash_chain.append(session, actor=user_id, action="eln.push", entity_type="eln", entity_id=connector.name,
                          detail={"pushed": pushed})
    return {"pushed": pushed, "skipped": skipped, "errors": errors}


def _apply(session: Session, link: ElnLinkRow, task: LabTaskRow, remote: dict[str, float],
           local: dict[str, float]) -> tuple[int, list[str]]:
    changed = [{"metric": k, "value": v} for k, v in remote.items() if local.get(k) != v]
    rows, errors = meas.record_external(session, task, changed, connector=link.connector,
                                        external_id=link.external_id)
    new_local = _local_state(session, link.connector, link.external_id)
    link.content_hash = _digest(remote) + _digest(new_local)
    link.last_synced_at = utcnow()
    link.sync_state = "ok" if not errors else "error"
    return len(rows), errors


def pull(session: Session, connector: ElnConnector, *, task_ids: list[str] | None = None) -> dict[str, Any]:
    q = select(ElnLinkRow).where(ElnLinkRow.connector == connector.name, ElnLinkRow.local_type == "lab_task")
    if task_ids:
        q = q.where(ElnLinkRow.local_id.in_(task_ids))
    report = {"checked": 0, "unchanged": 0, "recorded": 0, "conflicts": 0, "errors": []}
    for link in session.execute(q).scalars().all():
        report["checked"] += 1
        task = session.get(LabTaskRow, link.local_id)
        if task is None or task.status == "cancelled":
            continue
        try:
            remote = {v.metric: v.value for v in connector.pull_measurements(link.external_id)}
        except ConnectorError as exc:
            link.sync_state = "error"
            report["errors"].append({"task_no": task.task_no, "error": str(exc)})
            continue
        local = _local_state(session, link.connector, link.external_id)
        last_remote, last_local = (link.content_hash or "")[:32], (link.content_hash or "")[32:]
        if _digest(remote) == last_remote:
            report["unchanged"] += 1
            continue
        if _digest(local) != last_local:
            open_conflict = session.execute(select(ElnSyncConflictRow).where(
                ElnSyncConflictRow.link_id == link.id, ElnSyncConflictRow.status == "open")).first()
            if not open_conflict:
                session.add(ElnSyncConflictRow(id=str(uuid.uuid4()), link_id=link.id, local_snapshot=local,
                                               remote_snapshot=remote))
            link.sync_state = "conflict"
            report["conflicts"] += 1
            continue
        n, errors = _apply(session, link, task, remote, local)
        report["recorded"] += n
        report["errors"] += [{"task_no": task.task_no, "error": e} for e in errors]
    session.flush()
    return report


def resolve_conflict(session: Session, conflict_id: str, *, resolution: str, user_id: str) -> ElnSyncConflictRow:
    c = session.get(ElnSyncConflictRow, conflict_id)
    if c is None or c.status != "open":
        raise LabError("冲突不存在或已处理")
    if resolution not in ("keep_local", "take_remote"):
        raise LabError("resolution 只能是 keep_local / take_remote")
    link = session.get(ElnLinkRow, c.link_id)
    task = session.get(LabTaskRow, link.local_id)
    local = _local_state(session, link.connector, link.external_id)
    remote = {k: float(v) for k, v in (c.remote_snapshot or {}).items()}
    if resolution == "take_remote":
        _apply(session, link, task, remote, local)
    else:  # FormuNex values stand; this remote state is acknowledged and will not conflict again
        link.content_hash = _digest(remote) + _digest(local)
        link.sync_state = "ok"
        link.last_synced_at = utcnow()
    c.status, c.resolution, c.resolved_by, c.resolved_at = "resolved", resolution, user_id, utcnow()
    hash_chain.append(session, actor=user_id, action="eln.conflict.resolve", entity_type="eln_link", entity_id=link.id,
                      detail={"resolution": resolution, "local": local, "remote": remote})
    session.flush()
    return c
