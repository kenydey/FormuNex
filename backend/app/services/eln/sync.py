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
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


def _like_escape(value: str) -> str:
    """Escape a value interpolated into a LIKE pattern (``%``, ``_``, ``\\``).

    Stage 1 / SR-052: connector/external ids come from the remote ELN,
    so an id containing ``%`` or ``_`` must not widen the match to other
    links' measurements. The pattern below always passes ``escape="\\"``.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _external_ref_prefix(connector: str, external_id: str) -> str:
    return f"{_like_escape(connector)}:{_like_escape(external_id)}:%"

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

CONNECTORS = ("elabftw",)


def get_connector(name: str) -> ElnConnector:
    s = get_settings()
    if name == "elabftw":
        if not s.lab_elabftw_url:
            raise ConnectorError("未配置 eLabFTW（FORMUNEX_LAB_ELABFTW_URL / FORMUNEX_LAB_ELABFTW_API_KEY）")
        from .connectors.elabftw import ElabftwConnector

        return ElabftwConnector(s.lab_elabftw_url, s.lab_elabftw_api_key)
    raise ConnectorError(f"未知 ELN 连接器 {name}")


def _digest(state: dict[str, float]) -> str:
    # P3-C14: normalize numerics before hashing — json.dumps spells 500
    # as "500" but 500.0 as "500.0", so an int-valued remote state and a
    # float-valued local state hashed differently and every pull looked
    # like a remote change (a spurious re-pull, and a false conflict when
    # a local edit existed). The live eLabFTW connector already float()s,
    # so current libraries are unaffected in practice; this guards future
    # connectors. Non-numeric values pass through unchanged.
    norm: dict[str, object] = {}
    for k, v in state.items():
        try:
            norm[k] = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            norm[k] = v
    return hashlib.sha256(json.dumps(sorted(norm.items())).encode()).hexdigest()[:32]


def _rejected_metrics(session: Session, connector: str, external_id: str) -> set[str]:
    """Metrics whose latest external measurement for this link was rejected.

    Mirrors ``_local_state``'s last-wins ordering, but over ALL review
    states: a metric counts as rejected only when its latest row is
    rejected, so a corrected re-pull (a new unreviewed row) revives it.
    """
    rows = session.execute(select(MeasurementRow).where(
        MeasurementRow.external_ref.like(_external_ref_prefix(connector, external_id), escape="\\"),
    ).order_by(MeasurementRow.created_at, MeasurementRow.id)).scalars().all()
    latest = {r.metric: r.review_status for r in rows}
    return {m for m, s in latest.items() if s == "rejected"}


def _local_state(session: Session, connector: str, external_id: str) -> dict[str, float]:
    """Latest non-rejected measurement per metric, for EXTERNAL-ref rows only.

    P3-C15 (documented limitation, no behavior change): a manually entered
    row for the same metric carries no external_ref, so it is invisible to
    this digest — a remote change does not raise a conflict against the
    manual value. That is not a loss: the manual value is protected by the
    review mirror (record_external never overwrites a reviewed row), it is
    just not part of conflict detection. Widening the digest to manual rows
    would turn every manual edit into a conflict signal and needs a
    product decision, so it stays out of this P3 item.
    """
    rows = session.execute(select(MeasurementRow).where(
        MeasurementRow.external_ref.like(_external_ref_prefix(connector, external_id), escape="\\"),
        MeasurementRow.review_status != "rejected").order_by(
            MeasurementRow.created_at, MeasurementRow.id)).scalars().all()
    return {r.metric: r.value for r in rows}


def _local_units(session: Session, connector: str, external_id: str) -> dict[str, str]:
    """Units of the latest non-rejected external measurement per metric.

    W1-11: the value digest below cannot see a unit change (same number, new
    unit), so units are compared live on every pull instead of hashed into
    ``content_hash`` — no hash-format migration, no conflict storm on upgrade.
    """
    rows = session.execute(select(MeasurementRow).where(
        MeasurementRow.external_ref.like(_external_ref_prefix(connector, external_id), escape="\\"),
        MeasurementRow.review_status != "rejected").order_by(
            MeasurementRow.created_at, MeasurementRow.id)).scalars().all()
    return {r.metric: r.unit or "" for r in rows}


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
                # P3-C16: order_by(code), mirroring lab/measurements
                # _method_for — without it, several active methods for one
                # metric made method_code/unit nondeterministic on push.
                m = session.execute(select(TestMethodRow).where(TestMethodRow.metric == r["metric"],
                                                                TestMethodRow.active.is_(True))
                                    .order_by(TestMethodRow.code)).scalars().first()
                if m is not None:
                    r = {**r, "method_code": m.code, "unit": r.get("unit") or m.unit}
            reqs.append(r)
        payload["requested_metrics"] = reqs
        try:
            rec = connector.push_plan(payload)  # 补齐计划 2-15: 规范方法 push_plan
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
           local: dict[str, float], remote_units: dict[str, str] | None = None,
           local_units: dict[str, str] | None = None) -> tuple[int, list[str]]:
    remote_units = remote_units or {}
    local_units = local_units or {}
    changed = []
    for k, v in remote.items():
        # A unit-only change (same value, new unit) is a real change: it must
        # be recorded, not swallowed by the value digest.
        unit_changed = k in local_units and remote_units.get(k, "") != local_units[k]
        if local.get(k) != v or unit_changed:
            entry: dict[str, object] = {"metric": k, "value": v}
            # Stage 1 / SR-018: always carry the remote unit when known.
            # The old code attached it only on a unit *change* vs local,
            # so a first pull (no local row yet) silently dropped the
            # remote unit and record_external stored the raw number in
            # the method unit — a 500 mg/L pull into a g/L method stored
            # 500 g/L. Now record_external converts (or fails visibly).
            if remote_units.get(k, ""):
                entry["unit"] = remote_units[k]
            changed.append(entry)
    rows, errors = meas.record_external(session, task, changed, connector=link.connector,
                                        external_id=link.external_id)
    # Stage 1 / SR-017: advance the remote watermark only past metrics
    # that actually landed. The old code hashed the full remote dict
    # even when record_external failed some metrics, so the next pull
    # took the "unchanged" fast path and the failed metrics were never
    # retried. A failed metric stays out of the watermark digest, so
    # the next pull sees a digest mismatch and retries exactly that
    # metric (succeeded ones already match local and are skipped by the
    # changed computation). No hash-format change: the watermark is
    # still digest(remote-successful) + digest(local).
    recorded_metrics = {r.metric for r in rows}
    failed_metrics = {e["metric"] for e in changed} - recorded_metrics
    successful_remote = {k: v for k, v in remote.items() if k not in failed_metrics}
    new_local = _local_state(session, link.connector, link.external_id)
    link.content_hash = _digest(successful_remote) + _digest(new_local)
    link.last_synced_at = utcnow()
    link.sync_state = "ok" if not errors else "error"
    return len(rows), errors


def pull(session: Session, connector: ElnConnector, *, task_ids: list[str] | None = None,
         since: str | None = None) -> dict[str, Any]:
    """Pull measurements. ``since`` (ISO timestamp) 透传给连接器；无法服务端过滤的
    连接器忽略它，content_hash 去重兜底（见 connectors/base.py）。"""
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
            values = connector.pull_measurements(link.external_id, since=since)
        except ConnectorError as exc:
            link.sync_state = "error"
            report["errors"].append({"task_no": task.task_no, "error": str(exc)})
            continue
        # P2-C22: per-field skips recorded by the connector surface here.
        for err in getattr(connector, "last_pull_errors", None) or []:
            report["errors"].append({"task_no": task.task_no, "error": err})
        # Stage 1 / SR-052: a remote payload that repeats a metric keeps
        # the last value (dict construction below) — but that must be a
        # visible warning, not a silent choice, so an ELN double-report
        # is noticed instead of quietly overwriting.
        seen_metrics: set[str] = set()
        for v in values:
            if v.metric in seen_metrics:
                logger.warning(
                    "eln pull: duplicate remote metric %r for external_id %s; keeping the last value",
                    v.metric, link.external_id)
            seen_metrics.add(v.metric)
        remote = {v.metric: v.value for v in values}
        remote_units = {v.metric: v.unit or "" for v in values}
        local = _local_state(session, link.connector, link.external_id)
        local_units = _local_units(session, link.connector, link.external_id)
        last_remote, last_local = (link.content_hash or "")[:32], (link.content_hash or "")[32:]
        # W1-11: units are not in the value digest, so a unit-only change
        # must not take the "unchanged" fast path below.
        unit_diff = any(k in local_units and remote_units[k] != local_units[k] for k in remote_units)
        if _digest(remote) == last_remote and not unit_diff:
            report["unchanged"] += 1
            continue
        if _digest(local) != last_local:
            # P2-C21: the conflict snapshot must not carry metrics the lab
            # already rejected — take_remote would otherwise resurrect them.
            rejected = _rejected_metrics(session, link.connector, link.external_id)
            remote_snapshot = {k: v for k, v in remote.items() if k not in rejected}
            remote_units_snapshot = {k: v for k, v in remote_units.items() if k not in rejected}
            open_conflict = session.execute(select(ElnSyncConflictRow).where(
                ElnSyncConflictRow.link_id == link.id, ElnSyncConflictRow.status == "open")).scalars().first()
            if not open_conflict:
                # Stage 1 / SR-051: snapshot the remote units alongside
                # the values, so take_remote can convert before storing.
                session.add(ElnSyncConflictRow(id=str(uuid.uuid4()), link_id=link.id, local_snapshot=local,
                                               remote_snapshot=remote_snapshot, remote_units=remote_units_snapshot))
            else:
                # P2-C20: refresh the open conflict with the latest remote
                # state instead of leaving the first-seen snapshot stale.
                open_conflict.remote_snapshot = remote_snapshot
                open_conflict.remote_units = remote_units_snapshot
            link.sync_state = "conflict"
            report["conflicts"] += 1
            continue
        n, errors = _apply(session, link, task, remote, local, remote_units, local_units)
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
        # P2-C21: never resurrect locally-rejected metrics — including
        # ones rejected after the snapshot was taken, or carried by a
        # pre-fix snapshot. Skips are explicit in the log and the audit
        # detail, not silent.
        rejected = _rejected_metrics(session, link.connector, link.external_id)
        skipped = sorted(m for m in remote if m in rejected)
        if skipped:
            logger.warning("eln conflict %s take_remote: skipping locally-rejected metrics %s",
                           conflict_id, skipped)
        remote = {k: v for k, v in remote.items() if k not in rejected}
        remote_units = {k: v for k, v in (c.remote_units or {}).items() if k not in rejected}
        # Stage 1 / SR-051: pass the snapshotted remote units through so
        # values convert to the method unit before storing (1-10). A
        # pre-stage-1 snapshot has remote_units NULL: _apply then gets
        # no units and falls back to the method unit, exactly as before.
        _apply(session, link, task, remote, local,
               remote_units=remote_units,
               local_units=_local_units(session, link.connector, link.external_id))
    else:  # FormuNex values stand; this remote state is acknowledged and will not conflict again
        link.content_hash = _digest(remote) + _digest(local)
        link.sync_state = "ok"
        link.last_synced_at = utcnow()
    c.status, c.resolution, c.resolved_by, c.resolved_at = "resolved", resolution, user_id, utcnow()
    hash_chain.append(session, actor=user_id, action="eln.conflict.resolve", entity_type="eln_link", entity_id=link.id,
                      detail={"resolution": resolution, "local": local, "remote": remote,
                              "skipped_rejected": skipped if resolution == "take_remote" else []})
    session.flush()
    return c
