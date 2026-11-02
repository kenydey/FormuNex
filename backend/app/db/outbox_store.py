"""Durable outbox store for async task dispatch (idempotency foundation).

``enqueue`` is the single write-path: it guarantees exactly one row per
``(operation, idempotency_key)`` pair. Repeat calls with the same key
return the existing row without inserting a duplicate. ``select_pending``
feeds the dispatcher with unclaimed rows in FIFO (created_at) order.

Row lifecycle::

    PENDING ──claim──▶ CLAIMED ──dispatch ok──▶ PENDING (attempt_count + 1)
       │                                            │
       ├────────── worker succeeds (mark_done) ─────┴──▶ DONE
       ├────────── worker fails (mark_failed) ──────┴──▶ FAILED
       └────────── recovery gives up ──────────────▶ DEAD

``DONE`` is the success mark and ``FAILED`` the failure mark: crash
recovery replays neither (a FAILED row is visible to operators instead
of silently vanishing into DONE). ``CONFIRMED`` is reserved for the legacy
reconciliation phase (removed with the Datalab backend; no longer produced).

At-least-once (D-4 decision): the outbox does NOT deduplicate execution.
A row can run more than once — a crash between dispatch and the
completion mark, a recovery replay, or a resubmission all re-execute the
payload. Handlers must stay replay-safe (same slot / upsert); the
``claim-by-id`` wording elsewhere must not be read as exactly-once.

Liveness: a running job keeps ``updated_at`` fresh through :func:`heartbeat`, and
recovery only treats a row as stalled once *nothing has touched it* for the cutoff —
so a legitimately long job (a multi-hour KB ingest) is never replayed underneath
itself. ``task_id`` remembers the Celery id handed to the client so a replay can
run under the same one.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import TaskOutbox

logger = logging.getLogger(__name__)

STATUS_PENDING = "PENDING"
STATUS_CLAIMED = "CLAIMED"
STATUS_DONE = "DONE"
STATUS_FAILED = "FAILED"
STATUS_DEAD = "DEAD"

# Statuses a finished job may be re-armed from when the same payload is
# submitted again (a new submission is a new unit of work). FAILED joins
# DONE/DEAD: resubmitting a failed payload is the stage-0 retry path
# (a FAILED query/retry entry point is stage 1).
_REARMABLE = (STATUS_DONE, STATUS_DEAD, STATUS_FAILED)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Stage 5 / SR-087: (operation -> dotted field paths) whose lists are
# order-insensitive — the ONLY lists canonicalize sorts. Inventory
# (2026-11-02, 23 enqueue sites / 19 ops): every other list field is
# order-sensitive (time series, ranked lists, DOE factor/run order,
# evidence order, perspectives with [:5] truncation, ...) or free-form /
# UNKNOWN (document_generate's nested dicts), so it keeps its order in
# the hash. Scoped by operation, not by bare field name: a free-form doc
# payload could contain a same-named list with different semantics.
_ORDER_INSENSITIVE_LISTS: dict[str, frozenset[tuple[str, ...]]] = {
    # deps installs a package SET (pip resolves the set); the only order
    # trace is the human-readable summary text, not the job result.
    "deps": frozenset({("names",)}),
}


def canonicalize(obj: Any, *, sort_paths: frozenset[tuple[str, ...]] = frozenset()) -> Any:
    """Recursively canonicalize for the idempotency hash.

    Dict key order never matters (``json.dumps(sort_keys=True)`` at the
    hash site). Lists keep their order EXCEPT lists whose dotted field
    path is in ``sort_paths`` — sorting any other list would collapse
    distinct jobs into one key (e.g. two loop submissions whose
    ``prior_rmse_history`` differs only in order, or two searches whose
    ``source_types`` order differs, which the search cache itself treats
    as distinct). Callers must pass the operation's allowlist, not a
    blanket sort: see :func:`idempotency_key`.
    """
    sort = frozenset(sort_paths)

    def walk(node: Any, path: tuple[str, ...]) -> Any:
        if isinstance(node, dict):
            return {k: walk(v, path + (k,)) for k, v in node.items()}
        if isinstance(node, list):
            items = [walk(i, path) for i in node]
            if path in sort:
                try:
                    return sorted(items, key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
                except TypeError:
                    return items
            return items
        return node

    return walk(obj, ())


def idempotency_key(operation: str, payload: dict) -> str:
    """Content-addressed key: sha256 of the operation plus canonical payload.

    Deterministic from ``(operation, payload)`` alone, so the worker can
    recompute it from the payload it received to find its own outbox row.

    Only the operation's declared order-insensitive lists are sorted
    (SR-087); everything else keeps its order. One-time consequence:
    rows keyed before this change from an unsorted list no longer match
    a resubmission — those old keys were the collision bug itself, so
    the new row is the correct outcome, not a duplicate.
    """
    data = {
        "op": operation,
        "payload": canonicalize(payload, sort_paths=_ORDER_INSENSITIVE_LISTS.get(operation, frozenset())),
    }
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def enqueue(
    session: Session,
    operation: str,
    idempotency_key: str,
    payload: dict,
    *,
    status: str = STATUS_PENDING,
) -> tuple[str, str]:
    """Ensure one outbox row for *operation* × *idempotency_key*.

    If a matching row already exists, return ``(id, existing_status)``
    without inserting.  Otherwise insert a new row (``PENDING`` unless the
    caller records an already-completed event via *status*), flush
    (not commit — the caller owns the transaction boundary), and
    return ``(id, status)``.

    A finished (``DONE`` / ``DEAD``) row that is submitted again with
    ``status=PENDING`` is *re-armed*: the new submission is fresh work, so it
    must be crash-recoverable again and its stall clock restarts.

    Concurrent enqueues for the same key are resolved by the DB unique
    constraint: the loser rolls back the failed INSERT and re-selects the
    now-existing row to preserve idempotency.

    Returns:
        ``(task_id: str, status: str)`` — status may differ from
        ``"PENDING"`` when a de-duplicated stale row has been claimed or
        confirmed by the dispatcher.
    """
    existing = session.execute(
        select(TaskOutbox).filter_by(
            operation=operation, idempotency_key=idempotency_key,
        )
    ).scalar_one_or_none()

    if existing is not None:
        if status == STATUS_PENDING and existing.status in _REARMABLE:
            now = _utcnow()
            existing.status = STATUS_PENDING
            existing.attempt_count = 0
            existing.claimed_by = None
            existing.claimed_at = None
            existing.task_id = None  # a new submission gets a new id
            existing.payload = payload
            existing.created_at = now
            existing.updated_at = now
            session.flush()
            return existing.id, STATUS_PENDING
        return existing.id, existing.status

    row = TaskOutbox(
        id=str(uuid4()),
        operation=operation,
        idempotency_key=idempotency_key,
        payload=payload,
        status=status,
    )
    # No savepoint here: SQLAlchemy's begin_nested() on SQLite/pysqlite releases
    # the savepoint in a way that commits the flushed INSERT, so an outer
    # session.rollback() cannot undo it (breaks caller transaction atomicity).
    # A concurrent-duplicate IntegrityError propagates to the caller, which
    # rolls back its own transaction and re-selects idempotently.
    session.add(row)
    session.flush()
    return row.id, status


def mark_done(session: Session, operation: str, payload: dict) -> bool:
    """Completion mark: flip the row for *operation* × *payload* to ``DONE``.

    Only in-flight rows (``PENDING`` / ``CLAIMED``) change; a row already
    ``DONE`` / ``DEAD`` / ``CONFIRMED`` is left alone. The caller owns the
    commit. Returns True when a row was updated.
    """
    result = session.execute(
        update(TaskOutbox)
        .where(
            TaskOutbox.operation == operation,
            TaskOutbox.idempotency_key == idempotency_key(operation, payload),
            TaskOutbox.status.in_([STATUS_PENDING, STATUS_CLAIMED]),
        )
        .values(
            status=STATUS_DONE,
            claimed_by=None,
            claimed_at=None,
            updated_at=_utcnow(),
        )
    )
    return bool(result.rowcount)


def record_done(operation: str, payload: dict) -> bool:
    """Completion mark in its own transaction. Never raises.

    Called by the worker once a task reached a final state, so crash recovery
    (``dispatcher.recover_stalled``) only replays jobs that did not finish. A
    missing row (the API-side enqueue failed) is not an error.
    """
    try:
        from .database import default_session_factory
        from .session_utils import commit_session

        with commit_session(default_session_factory()) as session:
            return mark_done(session, operation, payload)
    except Exception:
        logger.exception("outbox mark_done failed (non-fatal)")
        return False


def mark_failed(session: Session, operation: str, payload: dict, error: str | None = None) -> bool:
    """Failure mark in the caller's transaction (stage 0 / SR-002 stopgap).

    Mirrors :func:`mark_done` but lands the row in FAILED with a short
    summary instead of DONE, so failed jobs stay visible to operators and
    are never silently replayed as if they had finished. Only in-flight
    rows (``PENDING`` / ``CLAIMED``) change; an already terminal row is
    left alone. The caller owns the commit. Returns True when updated.
    """
    result = session.execute(
        update(TaskOutbox)
        .where(
            TaskOutbox.operation == operation,
            TaskOutbox.idempotency_key == idempotency_key(operation, payload),
            TaskOutbox.status.in_([STATUS_PENDING, STATUS_CLAIMED]),
        )
        .values(
            status=STATUS_FAILED,
            error=(error or "")[:500] or None,
            claimed_by=None,
            claimed_at=None,
            updated_at=_utcnow(),
        )
    )
    return bool(result.rowcount)


def record_failed(operation: str, payload: dict, error: str | None = None) -> bool:
    """Failure mark in its own transaction. Never raises (mirrors record_done)."""
    try:
        from .database import default_session_factory
        from .session_utils import commit_session

        with commit_session(default_session_factory()) as session:
            return mark_failed(session, operation, payload, error)
    except Exception:
        logger.exception("outbox mark_failed failed (non-fatal)")
        return False


# How often a running task refreshes its row. Recovery's cutoff is 30 minutes, so
# one beat per minute leaves a wide margin for a slow database or a paused process.
HEARTBEAT_INTERVAL_S = 60.0


def touch(operation: str, payload: dict) -> bool:
    """Refresh ``updated_at`` of the in-flight row for *operation* × *payload*.

    Own transaction, never raises: a missed beat must not fail the job it is watching.
    Returns True when a row was touched (a finished / unknown row is left alone).
    """
    try:
        from .database import default_session_factory
        from .session_utils import commit_session

        with commit_session(default_session_factory()) as session:
            result = session.execute(
                update(TaskOutbox)
                .where(
                    TaskOutbox.operation == operation,
                    TaskOutbox.idempotency_key == idempotency_key(operation, payload),
                    TaskOutbox.status.in_([STATUS_PENDING, STATUS_CLAIMED]),
                )
                .values(updated_at=_utcnow())
            )
            return bool(result.rowcount)
    except Exception as exc:
        logger.warning("outbox heartbeat failed (non-fatal): %s", exc)
        return False


@contextmanager
def heartbeat(
    operation: str, payload: Any, interval_s: float | None = None
) -> Iterator[None]:
    """Keep the outbox row's ``updated_at`` fresh while the wrapped job runs.

    A beat is written immediately (a row that waited in the queue starts "fresh") and
    then every ``interval_s`` until the block exits. No-op for jobs without an outbox
    operation or a dict payload.
    """
    if not operation or not isinstance(payload, dict):
        yield
        return
    interval = HEARTBEAT_INTERVAL_S if interval_s is None else interval_s
    stop = threading.Event()

    def _beat() -> None:
        touch(operation, payload)
        while not stop.wait(interval):
            touch(operation, payload)

    thread = threading.Thread(target=_beat, name="outbox-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2.0)


def record_task_id(outbox_id: str | None, task_id: str) -> bool:
    """Remember the Celery id handed to the client. Own transaction, never raises."""
    if not outbox_id or not task_id:
        return False
    try:
        from .database import default_session_factory
        from .session_utils import commit_session

        with commit_session(default_session_factory()) as session:
            result = session.execute(
                update(TaskOutbox)
                .where(TaskOutbox.id == outbox_id)
                .values(task_id=task_id, updated_at=_utcnow())
            )
            return bool(result.rowcount)
    except Exception as exc:
        logger.warning("outbox task_id not recorded (non-fatal): %s", exc)
        return False


def list_outbox_rows(
    session: Session,
    *,
    status: str | None = None,
    operation: str | None = None,
    limit: int = 100,
) -> dict:
    """Stage 1 / SR-002: ops view of outbox rows (oldest first).

    ``counts`` covers the same filter as ``items`` (minus the limit).
    Payloads are intentionally NOT returned: they can be large and carry
    user content; the ops-visible fields are status, attempts, task id
    and the FAILED error summary. Retry reuses the stored row itself.
    """
    from sqlalchemy import func

    where = []
    if status:
        where.append(TaskOutbox.status == status)
    if operation:
        where.append(TaskOutbox.operation == operation)
    counts = {
        str(st): int(n)
        for st, n in session.execute(
            select(TaskOutbox.status, func.count())
            .where(*where)
            .group_by(TaskOutbox.status)
        ).all()
    }
    rows = (
        session.execute(
            select(TaskOutbox)
            .where(*where)
            .order_by(TaskOutbox.created_at.asc())
            .limit(max(1, min(int(limit), 500)))
        )
        .scalars()
        .all()
    )
    return {
        "counts": counts,
        "items": [
            {
                "id": r.id,
                "operation": r.operation,
                "status": r.status,
                "attempts": r.attempt_count or 0,
                "task_id": r.task_id,
                "error": r.error,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ],
    }


def rearm_failed_row(session: Session, row: TaskOutbox) -> bool:
    """Stage 1 / SR-002: manual retry of a FAILED/DEAD row.

    Resets attempts/claim/task/error and flips the row back to PENDING,
    keeping the original payload and created_at (unlike ``enqueue``'s
    re-arm, which treats a resubmission as a new unit of work). Returns
    False (leaving the row untouched) for any other status. The caller
    publishes the payload afterwards; a publish failure leaves the row
    PENDING for the next recovery pass, it does not lose the retry.
    """
    if row.status not in (STATUS_FAILED, STATUS_DEAD):
        return False
    row.status = STATUS_PENDING
    row.attempt_count = 0
    row.claimed_by = None
    row.claimed_at = None
    row.task_id = None
    row.error = None
    row.updated_at = _utcnow()
    session.flush()
    return True


def select_pending(session: Session, limit: int = 100) -> list[TaskOutbox]:
    """Return the oldest unclaimed outbox rows, up to *limit*.

    Rows are ordered by ``created_at ASC`` so the dispatcher processes
    the earliest-submitted tasks first.
    """
    return (
        session.execute(
            select(TaskOutbox)
            .where(TaskOutbox.status == STATUS_PENDING)
            .order_by(TaskOutbox.created_at.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
