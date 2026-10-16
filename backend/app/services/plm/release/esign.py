"""Electronic signatures (方案 §4.4): password re-authentication, signature meaning, object hash.

``signed_hash`` binds who signed, what it means, when, and the SHA-256 of the
object signed (a document's ``content_hash`` or a release package's
``package_hash``). :func:`verify` recomputes it, so a signature row that was
edited, or an object that changed after signing, is detected.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from ....clock import utcnow
from ....db.models_plm import ESignatureRow
from ...audit import hash_chain
from ...identity import accounts

MEANINGS = {
    "review": "审核", "approve": "批准", "release": "发布", "reject": "驳回", "waive": "豁免", "withdraw": "撤回",
}


def _digest(*, user_id: str, meaning: str, subject_type: str, subject_id: str, object_hash: str,
            step_id: str | None, signed_at: datetime) -> str:
    payload = hash_chain.canonical_json({
        "user_id": user_id, "meaning": meaning, "subject_type": subject_type, "subject_id": subject_id,
        "object_hash": object_hash, "step_id": step_id, "signed_at": signed_at.isoformat(timespec="microseconds"),
    })
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sign(session: Session, *, user_id: str, password: str, meaning: str, subject_type: str, subject_id: str,
         object_hash: str, step_id: str | None = None, detail: dict | None = None) -> ESignatureRow:
    """Re-authenticate and record a signature. Raises :class:`accounts.AuthError` on a wrong password
    (the caller should commit so the failed attempt and lock-out counter persist)."""
    if meaning not in MEANINGS:
        raise ValueError(f"unknown signature meaning {meaning!r}")
    accounts.reauthenticate(session, user_id, password)
    signed_at = utcnow()
    row = ESignatureRow(
        id=str(uuid.uuid4()), user_id=user_id, step_id=step_id, subject_type=subject_type, subject_id=subject_id,
        meaning=meaning, reauth_method="password", signed_at=signed_at,
        signed_hash=_digest(user_id=user_id, meaning=meaning, subject_type=subject_type, subject_id=subject_id,
                            object_hash=object_hash, step_id=step_id, signed_at=signed_at),
    )
    session.add(row)
    session.flush()
    hash_chain.append(session, actor=user_id, action=f"esign.{meaning}", entity_type=subject_type,
                      entity_id=subject_id, detail={"signature_id": row.id, "object_hash": object_hash,
                                                    "step_id": step_id, **(detail or {})})
    return row


def verify(row: ESignatureRow, object_hash: str) -> bool:
    return row.signed_hash == _digest(user_id=row.user_id, meaning=row.meaning, subject_type=row.subject_type,
                                      subject_id=row.subject_id, object_hash=object_hash, step_id=row.step_id,
                                      signed_at=row.signed_at)
