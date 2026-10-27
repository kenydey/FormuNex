"""Append-only, hash-chained audit log.

Each row's ``hash`` is SHA-256 over the previous row's hash and a canonical
serialisation of the row's own content, so editing, deleting or reordering any
row breaks every hash after it — :func:`verify_chain` finds the first break.
The database trigger from migration 0057 rejects UPDATE / DELETE outright; the
chain additionally detects tampering done by someone who could drop the
trigger.

Appends are serialised across the whole "read prev -> write row -> commit"
sequence, not just up to the flush. SQLite serialises individual writes, but
two transactions that both *read* the previous hash before either commits
still fork the chain (both rows carry the same ``prev_hash``; the later commit
lands one seq after a row whose hash it does not extend). The old code held
the process lock only around read -> flush and released it before the caller
committed, so exactly that interleave was possible.

The fix keeps :func:`append`'s contract — it still appends inside the caller's
transaction and the caller still commits, so an audit row still rolls back
with the business write it records (committing inside ``append`` would break
that atomicity, e.g. ``create_user`` writes user + roles + audit row in one
session). Instead the serialisation lock is held until the transaction ends:

- PostgreSQL: unchanged — the transaction-scoped advisory lock
  (``pg_advisory_xact_lock``) already covers read -> commit, and is released
  by the database when the transaction ends.
- Other dialects: a transaction-scoped lock held from the first ``append``
  in a transaction until that transaction ends (commit, rollback, or close).
  Within one process that is :data:`_process_lock`; for a file-backed SQLite
  database it is additionally a blocking file lock on a
  ``<db>.audit-chain.lock`` sibling file (``flock`` on POSIX,
  ``msvcrt.locking`` on Windows, via :mod:`app.services._filelock`), so
  separate processes (uvicorn workers, celery, a second request thread's
  session) serialise too. The lock is released from the
  ``after_transaction_end`` event, guarded by ``session.in_transaction()``:
  that event also fires for flushes and for nested (SAVEPOINT) transaction
  ends, where the outer transaction is still active and the lock must stay
  held. A session that appends twice in one transaction reuses the lock
  (tracked in ``session.info``); a session that appends, commits, and appends
  again re-acquires it for the new transaction.

Caveat, shared with the PostgreSQL advisory lock: a thread that appends in
one session and, before committing it, appends in a *different* session on
the same thread will block until the first transaction ends. All in-repo
call sites append in the session they were handed, so no such pattern exists;
do not introduce one.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from ...db.models_plm import AuditLogRow
from .._filelock import lock_exclusive, unlock

logger = logging.getLogger(__name__)

GENESIS = "0" * 64
_ADVISORY_KEY = 0x464E5841  # "FNXA"
# A waiter fails loudly after this long rather than forking the chain: it
# covers the 60 s SQLite busy window with room to spare, and a live holder
# longer than this is pathological (a dead holder releases via fd close).
_FILE_LOCK_TIMEOUT_S = 120.0
_process_lock = threading.Lock()
_INFO_KEY = "_audit_chain_lock"


@dataclass
class _ChainLock:
    """Lock state held in ``session.info`` for one transaction's audit appends."""

    lock_file: Any = None  # open file object holding the flock, if any


def _lock_file_path(session: Session) -> Path | None:
    """Sibling lock file for a file-backed SQLite database, else ``None``.

    In-memory databases cannot be shared across processes, so no cross-process
    lock is needed (or possible) for them.
    """
    try:
        db_path = session.get_bind().engine.url.database
    except Exception:
        return None
    if not db_path or db_path == ":memory:" or str(db_path).startswith("file:"):
        return None
    # Normalise the spelling so engines built from equivalent URLs share one
    # lock file (SQLite itself resolves a relative path against the cwd too).
    import os

    return Path(os.path.abspath(str(db_path)) + ".audit-chain.lock")


def _acquire_chain_lock(session: Session) -> None:
    """Hold the serialisation lock until this session's transaction ends.

    No-op when the session already holds it (a later append in the same
    transaction, including one inside a SAVEPOINT nested transaction).
    The matching release runs from ``after_transaction_end``; see
    :func:`_release_chain_lock`.
    """
    if _INFO_KEY in session.info:
        return
    _process_lock.acquire()
    token = _ChainLock()
    try:
        path = _lock_file_path(session)
        if path is not None:
            try:
                token.lock_file = path.open("a+")
                # Portable: flock on POSIX, msvcrt on Windows. A bare
                # ``import fcntl`` used to skip the cross-process lock
                # entirely on Windows, so a second process read the chain
                # tip before the first committed and forked the chain
                # (prev_hash GENESIS instead of the parent's hash).
                lock_exclusive(token.lock_file, timeout=_FILE_LOCK_TIMEOUT_S)
            except Exception as exc:
                # Degrade to the process lock only (same policy as the Redis
                # SQLite write lock): a missing/unlockable file must not take
                # audit writes down, but the cross-process guarantee is lost.
                logger.warning("audit chain file lock unavailable (%s) — process lock only", exc)
                if token.lock_file is not None:
                    try:
                        token.lock_file.close()
                    except Exception:
                        pass
                    token.lock_file = None
    except Exception:
        _process_lock.release()
        raise
    session.info[_INFO_KEY] = token
    event.listen(session, "after_transaction_end", _release_chain_lock)


def _release_chain_lock(session: Session, _transaction: Any) -> None:
    """Release the serialisation lock when the transaction actually ends.

    ``after_transaction_end`` also fires on flushes and on nested-transaction
    ends; in both cases the outer transaction is still active, so the lock
    must stay held — hence the ``in_transaction()`` guard. It fires with
    ``in_transaction()`` false on outer commit, outer rollback, and on
    ``session.close()`` without commit, which are exactly the ends that free
    the lock. The handler touches no database state.
    """
    if session.in_transaction():
        return
    token = session.info.pop(_INFO_KEY, None)
    if token is None:
        return
    try:
        if token.lock_file is not None:
            try:
                unlock(token.lock_file)
            except Exception:
                pass
            try:
                token.lock_file.close()
            except Exception:
                pass
    finally:
        _process_lock.release()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def content_hash(value: Any) -> str:
    """Stable digest of an entity snapshot, for ``before_hash`` / ``after_hash``."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _row_payload(ts: datetime, actor: str, action: str, entity_type: str, entity_id: str,
                 before_hash: str | None, after_hash: str | None, detail: dict) -> str:
    return canonical_json({
        "ts": ts.isoformat(timespec="microseconds"), "actor": actor, "action": action,
        "entity_type": entity_type, "entity_id": entity_id, "before_hash": before_hash,
        "after_hash": after_hash, "detail": detail,
    })


def chain_hash(prev_hash: str, payload: str) -> str:
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


def append(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    before: Any = None,
    after: Any = None,
    detail: dict | None = None,
    ts: datetime | None = None,
) -> AuditLogRow:
    """Append one audit record in *session*'s transaction (the caller commits).

    The read-prev -> flush -> caller-commit sequence is serialised across
    sessions: on PostgreSQL by the transaction-scoped advisory lock, elsewhere
    by :func:`_acquire_chain_lock`, which is held until the transaction ends
    (see the module docstring). A second append in the same transaction reuses
    the lock and simply chains onto the row this session already flushed.
    """
    ts = ts or datetime.now(timezone.utc).replace(tzinfo=None)
    detail = dict(detail or {})
    before_hash = content_hash(before) if before is not None else None
    after_hash = content_hash(after) if after is not None else None
    payload = _row_payload(ts, actor, action, entity_type, str(entity_id), before_hash, after_hash, detail)
    if session.get_bind().dialect.name == "postgresql":
        # Advisory lock is transaction-scoped: it already covers read -> commit.
        with _process_lock:
            session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ADVISORY_KEY})
            prev = session.execute(
                select(AuditLogRow.hash).order_by(AuditLogRow.seq.desc()).limit(1)
            ).scalar_one_or_none()
            row = AuditLogRow(
                ts=ts, actor=actor, action=action, entity_type=entity_type, entity_id=str(entity_id),
                before_hash=before_hash, after_hash=after_hash, detail=detail,
                prev_hash=prev or GENESIS, hash=chain_hash(prev or GENESIS, payload),
            )
            session.add(row)
            session.flush()
        return row
    _acquire_chain_lock(session)
    prev = session.execute(
        select(AuditLogRow.hash).order_by(AuditLogRow.seq.desc()).limit(1)
    ).scalar_one_or_none()
    row = AuditLogRow(
        ts=ts, actor=actor, action=action, entity_type=entity_type, entity_id=str(entity_id),
        before_hash=before_hash, after_hash=after_hash, detail=detail,
        prev_hash=prev or GENESIS, hash=chain_hash(prev or GENESIS, payload),
    )
    session.add(row)
    session.flush()
    return row


@dataclass
class ChainCheck:
    ok: bool
    rows: int
    first_bad_seq: int | None = None
    reason: str = ""


def verify_chain(session: Session, batch: int = 5000) -> ChainCheck:
    prev = GENESIS
    count = 0
    last_seq = 0
    while True:
        rows = session.execute(
            select(AuditLogRow).where(AuditLogRow.seq > last_seq).order_by(AuditLogRow.seq).limit(batch)
        ).scalars().all()
        if not rows:
            return ChainCheck(ok=True, rows=count)
        for r in rows:
            if r.prev_hash != prev:
                return ChainCheck(False, count, r.seq, "prev_hash does not match the preceding row")
            payload = _row_payload(r.ts, r.actor, r.action, r.entity_type, r.entity_id,
                                   r.before_hash, r.after_hash, r.detail or {})
            if chain_hash(prev, payload) != r.hash:
                return ChainCheck(False, count, r.seq, "row content does not match its hash")
            prev = r.hash
            count += 1
            last_seq = r.seq
