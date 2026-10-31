"""Wave 3-2: lightweight operational stats.

In-process counters only — no new storage. Counters reset on process
restart; the response says so explicitly. For durable history use the
structured logs (``tier2 evidence compression: ...``).
"""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/ops", tags=["ops"])


@router.get("/outbox")
def outbox_rows(
    status: str | None = None,
    operation: str | None = None,
    limit: int = 100,
) -> dict:
    """Stage 1 / SR-002: outbox rows for operators (oldest first).

    API-only (no screen); guarded by the existing ``/api/ops`` admin.llm
    rule in ``_rbac_guard``. Payloads are not returned (see
    ``outbox_store.list_outbox_rows``); FAILED rows carry their error
    summary here and are retried via the endpoint below.
    """
    from ..db.database import default_session_factory
    from ..db.outbox_store import list_outbox_rows

    with default_session_factory()() as session:
        return list_outbox_rows(session, status=status, operation=operation, limit=limit)


@router.post("/outbox/{row_id}/retry")
def outbox_retry(row_id: str) -> dict:
    """Stage 1 / SR-002: manually retry a FAILED/DEAD outbox row.

    Rearms the stored row (same payload, attempts reset) and publishes
    it under a fresh Celery id. A publish failure leaves the row
    PENDING for the next recovery pass instead of losing the retry.
    """
    from ..db.database import default_session_factory
    from ..db.dispatcher import _dispatch
    from ..db.models import TaskOutbox
    from ..db.outbox_store import rearm_failed_row, record_task_id

    with default_session_factory()() as session:
        row = session.get(TaskOutbox, row_id)
        if row is None:
            raise HTTPException(status_code=404, detail="outbox row not found")
        if row.status not in ("FAILED", "DEAD"):
            raise HTTPException(
                status_code=409,
                detail=f"only FAILED/DEAD rows can be retried (row is {row.status})",
            )
        operation, payload = row.operation, dict(row.payload or {})
        if not rearm_failed_row(session, row):
            raise HTTPException(status_code=409, detail="row could not be rearmed")
        session.commit()
    task_id = str(uuid4())
    try:
        _dispatch(operation, payload, task_id=task_id)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"retry queued but publish failed ({exc}); row stays PENDING for recovery",
        ) from exc
    record_task_id(row_id, task_id)
    return {"outbox_id": row_id, "operation": operation, "task_id": task_id, "status": "PENDING"}


@router.get("/datalab-orphans")
def datalab_orphans(status: str | None = None, limit: int = 100) -> dict:
    """Datalab samples a failed saga rollback could not delete (cleanup queue).

    ``counts`` is per outbox status (PENDING = still to delete, DONE = cleaned,
    DEAD = gave up — delete by hand); ``items`` lists the rows, oldest first.
    """
    from ..db.database import default_session_factory
    from ..db.dispatcher import list_orphans

    with default_session_factory()() as session:
        return list_orphans(session, status=status, limit=limit)


@router.post("/datalab-orphans/cleanup")
def datalab_orphans_cleanup(max_rows: int = 50) -> dict:
    """Retry deleting the pending orphaned Datalab samples now.

    The same pass runs at startup; this is the manual trigger. Nothing is attempted
    (and no retry burned) while Datalab is unconfigured or unreachable — see ``skipped``.
    """
    from ..db.database import default_session_factory
    from ..db.dispatcher import drain_orphans

    with default_session_factory()() as session:
        return drain_orphans(session, max_rows=max(1, min(int(max_rows), 200)))


@router.get("/kb-health")
def kb_health() -> dict:
    """B-9: KB 健康仪表盘 v1。

    parser 分布、embedding 覆盖率（chunk 表实测）、空文档率（零切块文档占比）。
    读失败时返回 ``{"available": False}``（fail-open）。
    """
    from ..services.kb_index import kb_health_snapshot

    return kb_health_snapshot()


@router.get("/evidence-stats")
def evidence_stats() -> dict:
    """Tier-2 (LLM) evidence compression counters for this process.

    Counters are cumulative since process start and reset on restart.
    """
    from ..services.query_aware_compression import get_evidence_stats

    stats = get_evidence_stats()
    attempts = stats.get("tier2_attempts", 0)
    triggers = stats.get("tier2_triggers", 0)
    tokens_before = stats.get("tokens_before", 0)
    tokens_after = stats.get("tokens_after", 0)
    # P3-5: ingest-time embedding coverage (process-local, reset on restart).
    kb_cov = {}
    kb_coverage_rate = None
    try:
        from ..services.kb_index import get_kb_coverage_stats

        kb_cov = get_kb_coverage_stats()
        total = kb_cov.get("kb_chunks_total", 0)
        kb_coverage_rate = (kb_cov.get("kb_chunks_embedded", 0) / total) if total else None
    except Exception:
        pass
    return {
        **stats,
        **kb_cov,
        "kb_embedding_coverage": kb_coverage_rate,
        "tokens_saved": tokens_before - tokens_after,
        "trigger_rate": (triggers / attempts) if attempts else 0.0,
        "process_local": True,
        "note": "counters reset on process restart; see logs for per-query lines",
    }


@router.get("/recommend-stats")
def recommend_stats(
    project_id: str | None = None,
    days: int = 30,
) -> dict:
    """C-8: recommendation adoption telemetry (weak-success layer).

    Totals, adoption rate, and per-signal distribution over the trailing
    ``days`` window. Fail-open: returns empty stats (``available: False``)
    when the ``recommend_outcomes`` table does not exist yet.
    ``validated`` counts rounds with ``experiment_validated=True`` (U-4:
    sync-datalab 成功后按 snapshot 指纹 fail-open 回写；C-5 pending 时恒为
    null 的旧契约已过时）。
    """
    from ..db import recommend_outcome_store
    from ..db.database import default_session_factory

    days = max(1, min(int(days), 365))
    factory = default_session_factory()
    with factory() as session:
        return recommend_outcome_store.outcome_stats(
            session, project_id=project_id or None, days=days
        )
