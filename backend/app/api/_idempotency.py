"""Content-addressed idempotency keys for async job submission.

Async endpoints record a durable outbox row before dispatching to Celery so a
retried submission maps to the same *row* (the key is a hash of the operation
plus the canonicalised payload). That is row identity, not execution
deduplication: under the at-least-once outbox semantics (D-4 decision,
stage 1 / SR-015-SR-016), a retry can still execute twice — e.g. a timed-out
publish that lands late plus a recovery replay — so handlers must stay
replay-safe and no caller may read this module as an exactly-once promise.

Extracted from ``api/research.py`` when the third endpoint needed it. The key
function itself lives in ``db.outbox_store`` (the worker recomputes it to mark
its own row ``DONE``); it is re-exported here for the existing call sites.
"""
from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import outbox_store
from ..db.database import default_session_factory
from ..db.models import TaskOutbox
from ..db.outbox_store import canonicalize, idempotency_key  # noqa: F401  (re-export)
from ..db.session_utils import commit_session


def _enqueue_once(operation: str, key: str, payload: dict,
                  owner_id: str | None) -> str:
    """One attempt at the durable write; raises on any DB failure."""
    factory = default_session_factory()
    with commit_session(factory) as session:
        row_id, _ = outbox_store.enqueue(session, operation, key, payload, owner_id=owner_id)
    return row_id


def enqueue_outbox(operation: str, payload: dict, *, owner_id: str | None = None) -> str | None:
    """Record the job durably. Returns the outbox id, or None on failure.

    Non-fatal by design: losing the audit row must not stop the job from
    running — but it must not lose the row silently either (P2-4,
    2026-11-04 deep review). Two failure modes, handled:

    * Concurrent duplicate (unique-key race): the loser's INSERT raises
      IntegrityError. Roll back and re-select the winner's row by
      (operation, key) so the caller still gets a durable row id; if
      the re-select misses, the retry below re-runs the idempotent
      enqueue, which returns the existing row.
    * Transient DB faults: one immediate retry (SQLite lock contention
      is the usual suspect). Only a second failure degrades to None,
      logged, as before.
    """
    try:
        key = idempotency_key(operation, payload)
    except Exception:
        logger.exception("outbox enqueue failed computing idempotency key (non-fatal)")
        return None
    for attempt in (1, 2):
        try:
            return _enqueue_once(operation, key, payload, owner_id)
        except IntegrityError:
            logger.warning("outbox enqueue key race (attempt %d), re-selecting", attempt)
            try:
                with default_session_factory()() as session:
                    row = session.execute(
                        select(TaskOutbox).where(
                            TaskOutbox.operation == operation,
                            TaskOutbox.idempotency_key == key,
                        )
                    ).scalar_one_or_none()
                if row is not None:
                    return row.id
            except Exception:
                logger.exception("outbox re-select failed")
        except Exception:
            logger.exception("outbox enqueue failed (attempt %d, non-fatal)", attempt)
    return None

