"""Startup outbox stall recovery — re-enqueue stalled async jobs (Task 1.3).

Scans ``task_outbox`` for rows that are still PENDING or CLAIMED beyond a
configurable cutoff (default 30 min) and re-dispatches them via the
Celery ``.delay()`` path so no durable outbox row is left behind after a
crash / redeploy while jobs are in-flight.

A row is only ever replayed while it is *unfinished*: the worker flips it to
``DONE`` when the task reaches a final state (``outbox_store.record_done``),
so recovery covers crash / redeploy / lost-broker-message cases only. "Stalled"
means *nothing has touched the row* for the cutoff: a running task keeps
``updated_at`` fresh through ``outbox_store.heartbeat``, so a job that simply runs
longer than the cutoff is not replayed underneath itself. A replay runs under the
Celery id the client was given (``task_id``) when it is known. Rows whose operation
has no Celery handler (audit records such as ``ingest_complete``) are never
replayed, and rows untouched for ``MAX_RECOVER_AGE_HOURS`` are expired instead.

W1-10 ③ coverage: EVERY async submission kind now writes an outbox row and
has a handler in ``_HANDLERS`` (research_recommend, research_deep,
inverse_design, doe_cycle, loop, optimize, kb_reparse, kg_relations_rebuild,
toc_summaries, pubchem_enrich, deps, search, topic_sweep, wiki_storm_report)
— each was checked replay-safe before wiring (same-slot task persistence,
upsert, or fill-missing; never append-a-new-record), and a test pins that
any ``enqueue_outbox`` call site in ``api/*`` or ``services/*`` has a handler.
Recovery parameters: one pass replays at most ``DEFAULT_MAX_ROWS`` (20) rows
(or ``FORMUNEX_OUTBOX_RECOVER_MAX_ROWS`` when set), a row is dropped to DEAD
after ``MAX_ATTEMPTS`` (5) failed replays, and rows untouched for 24 h expire
instead of replaying. Recovery runs once at startup in a daemon thread
(``schedule_recover_stalled`` from lifespan) AND periodically
(``schedule_periodic_recover_stalled``, default every 900 s, disabled by
``FORMUNEX_OUTBOX_RECOVER_INTERVAL_S=0``), so a broker outage that strands a
job mid-run is healed without a restart; under ``celery_eager`` replay is
skipped outright (see below) and only the warning path runs.

Lifespan MUST schedule recovery in a daemon thread (see
``schedule_recover_stalled``): with ``FORMUNEX_CELERY_EAGER=true``, a
synchronous ``.delay()`` would run whole research/inverse tasks on the
startup thread and keep :8000 closed — the middle-column ``Load failed`` /
``/api/projects -> 500`` failure mode.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import TaskOutbox

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
# Recovery is for jobs interrupted by a crash / redeploy, not archaeology: a
# row still unfinished after this long is expired (DEAD) instead of replayed,
# so legacy rows written before the completion mark existed cannot trigger a
# replay storm of long-finished jobs.
MAX_RECOVER_AGE_HOURS = 24
# Cap how many stalled rows one recovery pass will touch (startup budget).
DEFAULT_MAX_ROWS = 20
# Broker publish timeout for non-eager ``.delay()`` (seconds).
DEFAULT_DISPATCH_TIMEOUT_S = 5.0

_worker_id = f"{socket.gethostname()}-{os.getpid()}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _celery_is_eager() -> bool:
    """Read effective eager flag without importing Celery app at module load."""
    try:
        from ..config import get_settings

        return get_settings().celery_eager
    except Exception:
        logger.exception("recover_stalled: failed to read celery_eager; assuming False")
        return False


# ── operation → Celery task mapping ─────────────────────────────────────────

def _publish(task, payload: dict, task_id: str | None) -> None:
    """Publish *task*; under the client's original Celery id when we have it."""
    if task_id:
        task.apply_async(args=(payload,), task_id=task_id)
    else:
        task.delay(payload)


def _send_recommend(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_recommend_task

    _publish(run_recommend_task, payload, task_id)


def _send_deep_research(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_deep_research_task

    _publish(run_deep_research_task, payload, task_id)


def _send_inverse_design(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_inverse_design_task

    _publish(run_inverse_design_task, payload, task_id)


def _send_doe_cycle(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_doe_cycle_task

    _publish(run_doe_cycle_task, payload, task_id)


def _send_loop(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_loop_task

    _publish(run_loop_task, payload, task_id)


def _send_optimize(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_optimize_task

    _publish(run_optimize_task, payload, task_id)


def _send_kb_reparse(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_kb_reparse_task

    _publish(run_kb_reparse_task, payload, task_id)


def _send_kg_relations_rebuild(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_kg_relations_rebuild

    _publish(run_kg_relations_rebuild, payload, task_id)


def _send_toc_summaries(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_toc_summaries_task

    _publish(run_toc_summaries_task, payload, task_id)


def _send_pubchem_enrich(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_pubchem_enrich_task

    _publish(run_pubchem_enrich_task, payload, task_id)


def _send_deps(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_deps_install_task

    _publish(run_deps_install_task, payload, task_id)


def _send_search(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_search_task

    _publish(run_search_task, payload, task_id)


def _send_topic_sweep(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_topic_sweep

    _publish(run_topic_sweep, payload, task_id)


def _send_wiki_storm_report(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_wiki_storm_report_task

    _publish(run_wiki_storm_report_task, payload, task_id)


def _send_url_ingest(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_url_ingest_task

    _publish(run_url_ingest_task, payload, task_id)


def _send_task_ingest(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_task_ingest_task

    _publish(run_task_ingest_task, payload, task_id)


def _send_evidence_ingest(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_evidence_ingest_task

    _publish(run_evidence_ingest_task, payload, task_id)


def _send_document_generate(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_document_generate_task

    _publish(run_document_generate_task, payload, task_id)


def _send_wiki_lint_run(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_wiki_lint_task

    _publish(run_wiki_lint_task, payload, task_id)


def _send_wiki_lint_sweep(payload: dict, task_id: str | None = None) -> None:
    from ..worker.tasks import run_wiki_lint_sweep_task

    _publish(run_wiki_lint_sweep_task, payload, task_id)


# Single source of truth for what recovery may replay. Every operation that
# ``api/*`` or ``services/*`` enqueues through ``enqueue_outbox`` MUST have an
# entry here (a test pins that); audit-only operations deliberately do not.
# Replay note for "loop" (W1-10 ②): the task re-checks the paused flag from
# the database at run time, so a replayed loop cannot run into a campaign
# paused after submit; its payload snapshot (prior_rmse_history) may be stale,
# the same class of staleness already accepted for doe_cycle replays.
_HANDLERS: dict[str, Callable[..., None]] = {
    "research_recommend": _send_recommend,
    "research_deep": _send_deep_research,
    "inverse_design": _send_inverse_design,
    "doe_cycle": _send_doe_cycle,
    "loop": _send_loop,
    "optimize": _send_optimize,
    "kb_reparse": _send_kb_reparse,
    "kg_relations_rebuild": _send_kg_relations_rebuild,
    "toc_summaries": _send_toc_summaries,
    "pubchem_enrich": _send_pubchem_enrich,
    "deps": _send_deps,
    "search": _send_search,
    "topic_sweep": _send_topic_sweep,
    "wiki_storm_report": _send_wiki_storm_report,
    # Stage 3 batch 2 (SR-026/063): replay-safe — ingests dedup by source,
    # lint recomputes read-only findings, document generation appends a
    # new version (never edits an existing one), so a replay after a
    # crash re-creates the work the client is still polling for.
    "url_ingest": _send_url_ingest,
    "task_ingest": _send_task_ingest,
    "evidence_ingest": _send_evidence_ingest,
    "document_generate": _send_document_generate,
    "wiki_lint_run": _send_wiki_lint_run,
    "wiki_lint_sweep": _send_wiki_lint_sweep,
}

DISPATCHABLE_OPERATIONS: tuple[str, ...] = tuple(_HANDLERS)


def _dispatch(operation: str, payload: dict, task_id: str | None = None) -> None:
    """Map an outbox *operation* to the matching Celery task and publish it.

    With *task_id* the replay runs under the id the client already holds. Raises
    ``ValueError`` for an operation without a handler so the caller never counts a
    no-op as a successful re-enqueue.
    """
    handler = _HANDLERS.get(operation)
    if handler is None:
        raise ValueError(f"no outbox handler for operation {operation!r}")
    if task_id:
        handler(payload, task_id)
    else:
        handler(payload)


def _dispatch_with_timeout(
    operation: str,
    payload: dict,
    timeout_s: float = DEFAULT_DISPATCH_TIMEOUT_S,
    task_id: str | None = None,
) -> None:
    """Publish to the broker with a hard wall-clock limit (non-eager path)."""
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="outbox-delay") as pool:
        fut = (
            pool.submit(_dispatch, operation, payload, task_id)
            if task_id
            else pool.submit(_dispatch, operation, payload)
        )
        try:
            fut.result(timeout=timeout_s)
        except FuturesTimeout as exc:
            raise TimeoutError(
                f"Celery publish timed out after {timeout_s:.1f}s for {operation}"
            ) from exc


def _expire_ancient(session: Session, expiry: datetime) -> int:
    """Mark unfinished dispatchable rows untouched since *expiry* ``DEAD`` (no replay).

    One bulk UPDATE, outside the per-pass ``max_rows`` budget: otherwise the
    oldest (least useful) rows would eat the budget one pass at a time.
    """
    result = session.execute(
        update(TaskOutbox)
        .where(
            TaskOutbox.status.in_(["PENDING", "CLAIMED"]),
            TaskOutbox.operation.in_(DISPATCHABLE_OPERATIONS),
            TaskOutbox.updated_at < expiry,
        )
        .values(status="DEAD")
    )
    n = int(result.rowcount or 0)
    if n:
        session.commit()
        logger.warning(
            "recover_stalled: expired %d unfinished outbox row(s) untouched since %s "
            "(not replayed)",
            n,
            expiry.isoformat(timespec="seconds"),
        )
    return n


# ── public API ──────────────────────────────────────────────────────────────

def recover_stalled(
    session: Session,
    cutoff_minutes: int = 30,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    dispatch_timeout_s: float = DEFAULT_DISPATCH_TIMEOUT_S,
    max_age_hours: float = MAX_RECOVER_AGE_HOURS,
) -> int:
    """Re-enqueue outbox rows that have been stalled for too long.

    Scans ``task_outbox`` for rows with ``status IN ('PENDING', 'CLAIMED')``
    that nothing has touched (``updated_at``: creation, claim, or a running
    task's heartbeat) since *now − cutoff_minutes*.  For each match the payload
    is re-dispatched — under the original Celery id when the row recorded it,
    else via ``.delay()`` — and the row's status is reset to ``'PENDING'``
    (attempt_count incremented).

    Prefer ``recover_stalled_for_startup`` / ``schedule_recover_stalled`` from
    lifespan: those skip re-dispatch when ``celery_eager`` is True so a local
    eager boot cannot block on full task bodies.

    Args:
        session: An active SQLAlchemy ORM session.
        cutoff_minutes: Age threshold in minutes (default 30).
        max_rows: Maximum rows to process in this pass (startup budget).
        dispatch_timeout_s: Broker publish timeout when not eager.
        max_age_hours: Rows older than this are expired (``DEAD``) instead of
            replayed.

    Returns:
        Number of rows that were re-enqueued.

    Raises:
        ValueError: If *cutoff_minutes* is less than 1.
    """
    if cutoff_minutes < 1:
        raise ValueError("cutoff_minutes must be >= 1")
    if max_rows < 1:
        raise ValueError("max_rows must be >= 1")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = now - timedelta(minutes=cutoff_minutes)

    _expire_ancient(session, now - timedelta(hours=max_age_hours))

    stalled_where = (
        TaskOutbox.status.in_(["PENDING", "CLAIMED"]),
        TaskOutbox.operation.in_(DISPATCHABLE_OPERATIONS),
        TaskOutbox.updated_at < cutoff,
    )
    from sqlalchemy import func as _func

    total_stalled = session.execute(
        select(_func.count()).select_from(TaskOutbox).where(*stalled_where)
    ).scalar() or 0
    if total_stalled > max_rows:
        logger.warning(
            "recover_stalled: %d stalled outbox row(s), processing %d this pass, "
            "%d left for later passes",
            total_stalled,
            max_rows,
            total_stalled - max_rows,
        )
    stalled = (
        session.execute(
            select(TaskOutbox)
            .where(*stalled_where)
            .order_by(TaskOutbox.created_at.asc())
            .limit(max_rows)
        )
        .scalars()
        .all()
    )

    if not stalled:
        return 0

    count = 0
    for row in stalled:
        if (row.attempt_count or 0) >= MAX_ATTEMPTS:
            row.status = "DEAD"
            logger.error(
                "task %s exceeded max attempts (%d), marking DEAD",
                row.id,
                MAX_ATTEMPTS,
            )
            session.commit()
            continue

        # Stage 0 / SR-002 stopgap guard: a job that already finished but
        # whose row never got its terminal mark (pre-stopgap rows, or an
        # eager→worker backlog) must never be replayed — a replay would
        # silently overwrite the delivered result with a new non-deterministic
        # run. If the row's Celery id already has a terminal snapshot on
        # disk, close the row from the snapshot instead of re-dispatching.
        # Deferred import: worker.tasks imports db modules at top level.
        if row.task_id:
            try:
                from ..worker.tasks import load_persisted_task

                snap = load_persisted_task(row.task_id)
                state = getattr(snap.state, "value", snap.state) if snap is not None else None
            except Exception:  # noqa: BLE001 — unreadable snapshot means "no guard", replay as before
                snap, state = None, None
            if state in ("completed", "failed"):
                row.status = "DONE" if state == "completed" else "FAILED"
                if state == "failed":
                    row.error = (snap.message or "")[:500] or None
                row.claimed_by = None
                row.claimed_at = None
                session.commit()
                logger.info(
                    "recover_stalled: outbox row %s already terminal (%s); "
                    "closed without replay", row.id, state)
                continue

        # Stage 1 / SR-048: atomic claim. The old code SELECTed then set
        # CLAIMED + committed per row, so two workers starting a pass at
        # the same time could both claim and dispatch the same row. The
        # conditional UPDATE below is the claim: only the worker whose
        # UPDATE touches exactly one row owns it. The updated_at predicate
        # matters as much as the status one — a row a live task just
        # heartbeated is no longer stalled even if another worker already
        # SELECTed it. Losers skip the row instead of dispatching it.
        claimed = session.execute(
            update(TaskOutbox)
            .where(
                TaskOutbox.id == row.id,
                TaskOutbox.status.in_(["PENDING", "CLAIMED"]),
                TaskOutbox.updated_at < cutoff,
            )
            .values(status="CLAIMED", claimed_by=_worker_id, claimed_at=_utcnow())
        ).rowcount
        session.commit()
        if claimed != 1:
            continue
        # Drop the pre-claim ORM state: the Core UPDATE above bypassed it,
        # and the dispatch/failure paths below must see post-claim values.
        session.expire(row)

        try:
            if row.task_id:
                _dispatch_with_timeout(
                    row.operation, row.payload, dispatch_timeout_s, task_id=row.task_id
                )
            else:
                _dispatch_with_timeout(row.operation, row.payload, dispatch_timeout_s)
        except Exception:
            logger.exception(
                "recover_stalled: dispatch failed for outbox row %s "
                "(operation=%s)",
                row.id,
                row.operation,
            )
            try:
                session.rollback()
            except Exception:
                logger.exception("recover_stalled: rollback failed")
            row = session.get(TaskOutbox, row.id)
            if row is None:
                continue
            # Skipped: reset claim metadata without bumping attempt_count —
            # only successful dispatches are counted toward MAX_ATTEMPTS.
            row.status = "PENDING"
            row.claimed_by = None
            row.claimed_at = None
            session.commit()
            continue

        row.status = "PENDING"
        row.attempt_count = (row.attempt_count or 0) + 1
        row.claimed_by = None
        row.claimed_at = None
        session.commit()
        count += 1

    if count:
        logger.info("recover_stalled: re-enqueued %d stalled outbox rows", count)

    return count


def recover_stalled_for_startup(
    session: Session,
    cutoff_minutes: int = 30,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    dispatch_timeout_s: float = DEFAULT_DISPATCH_TIMEOUT_S,
) -> int:
    """Startup-safe wrapper: never synchronously re-dispatch under celery_eager.

    Local/dev often sets ``FORMUNEX_CELERY_EAGER=true``. Calling ``.delay()``
    then runs recommend/deep-research/inverse-design inline and can stall
    uvicorn before it binds :8000. Leave rows PENDING for an explicit retry
    or a real worker instead.
    """
    if _celery_is_eager():
        # Count how many would have been touched so operators see why nothing ran.
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff -= timedelta(minutes=cutoff_minutes)
        n = len(
            session.execute(
                select(TaskOutbox.id)
                .where(
                    TaskOutbox.status.in_(["PENDING", "CLAIMED"]),
                    TaskOutbox.operation.in_(DISPATCHABLE_OPERATIONS),
                    TaskOutbox.updated_at < cutoff,
                )
                .limit(max_rows)
            )
            .scalars()
            .all()
        )
        if n:
            logger.warning(
                "recover_stalled: celery_eager=true — leaving %d stalled outbox "
                "row(s) PENDING (sync .delay() would block API startup)",
                n,
            )
        return 0
    return recover_stalled(
        session,
        cutoff_minutes,
        max_rows=max_rows,
        dispatch_timeout_s=dispatch_timeout_s,
    )


def schedule_recover_stalled() -> threading.Thread:
    """Run startup recovery in a daemon thread so lifespan can ``yield`` immediately.

    Failures are logged and never raised to the caller — matching the old
    best-effort lifespan try/except contract. Broker hangs are bounded by
    ``dispatch_timeout_s`` inside ``recover_stalled``.
    """

    def _run() -> None:
        try:
            from ..config import get_settings
            from .database import default_session_factory
            from .sqlite_lock import sqlite_write_lock

            settings = get_settings()
            factory = default_session_factory()
            with sqlite_write_lock(settings.redis_url):
                with factory() as session:
                    recovered = recover_stalled_for_startup(session)
                    if recovered:
                        logger.info(
                            "lifespan: recovered %d stalled outbox row(s)", recovered
                        )
                    session.commit()
        except Exception:
            logger.exception("lifespan: outbox stall recovery failed (non-fatal)")

    thread = threading.Thread(
        target=_run, name="outbox-recover", daemon=True
    )
    thread.start()
    return thread


def schedule_periodic_recover_stalled(interval_s: float = 900.0) -> threading.Thread:
    """Stage 1 / SR-048: periodic stall sweep in a daemon thread.

    Each pass runs ``recover_stalled_for_startup`` (same eager guard as
    the startup pass) under the SQLite write lock, so a broker outage
    that strands a job mid-run is healed without a restart and without
    a manual retry. Failures are logged and never raised; the loop
    sleeps ``interval_s`` between passes and dies with the process.
    """
    import time

    def _run() -> None:
        while True:
            time.sleep(interval_s)
            try:
                from ..config import get_settings
                from .database import default_session_factory
                from .sqlite_lock import sqlite_write_lock

                settings = get_settings()
                factory = default_session_factory()
                with sqlite_write_lock(settings.redis_url):
                    with factory() as session:
                        recovered = recover_stalled_for_startup(
                            session,
                            max_rows=settings.outbox_recover_max_rows,
                        )
                        if recovered:
                            logger.info(
                                "periodic recover: re-enqueued %d stalled outbox row(s)",
                                recovered,
                            )
                        session.commit()
            except Exception:
                logger.exception("periodic outbox recovery failed (non-fatal)")

    thread = threading.Thread(
        target=_run, name="outbox-recover-periodic", daemon=True
    )
    thread.start()
    return thread
