"""Append-only, hash-chained audit log.

Each row's ``hash`` is SHA-256 over the previous row's hash and a canonical
serialisation of the row's own content, so editing, deleting or reordering any
row breaks every hash after it — :func:`verify_chain` finds the first break.
The database trigger from migration 0057 rejects UPDATE / DELETE outright; the
chain additionally detects tampering done by someone who could drop the
trigger.

Appends are serialised: on PostgreSQL with a transaction-scoped advisory lock,
elsewhere with a process lock (SQLite already serialises writers).
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ...db.models_plm import AuditLogRow

GENESIS = "0" * 64
_ADVISORY_KEY = 0x464E5841  # "FNXA"
_process_lock = threading.Lock()


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
    """Append one audit record in *session*'s transaction (the caller commits)."""
    ts = ts or datetime.now(timezone.utc).replace(tzinfo=None)
    detail = dict(detail or {})
    before_hash = content_hash(before) if before is not None else None
    after_hash = content_hash(after) if after is not None else None
    payload = _row_payload(ts, actor, action, entity_type, str(entity_id), before_hash, after_hash, detail)
    with _process_lock:
        if session.get_bind().dialect.name == "postgresql":
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
