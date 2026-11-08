"""EHS classification lock (P1-C1).

The release gate requires key components (≥ 10 %, or hazardous at ≥ 1 %) to
have a ``manual`` classification row with ``locked_at`` set — but until now
no production path could create such a row or stamp it, so the gate could
only pass for fixtures that hand-wrote locked rows. This module is that
path: an EHS specialist (``regulatory.review``) confirms the classification,
re-authenticates with their password, and the manual rows are stamped
``locked_at`` / ``locked_by`` with an audit entry.

Already-locked rows are never silently overwritten: submitting a new
classification for a class that already has a locked row is a conflict, so
a lock, once given, can only be superseded by an explicit correction flow
(not by re-submitting through this endpoint).
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ....clock import utcnow
from ....db.models_regulatory import SubstanceClassificationRow, SubstanceRow
from ...audit import hash_chain
from ...identity import accounts
from .hazard_classes import BY_CODE


class LockError(ValueError):
    """Lock request problem; ``status`` is the HTTP status to report."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def lock_classification(session: Session, *, substance_id: str, user_id: str, password: str,
                        classifications: list[dict] | None = None) -> dict:
    """Lock a substance's manual classification. Raises :class:`LockError`
    or :class:`accounts.AuthError` (wrong password). The caller commits."""
    if session.get(SubstanceRow, substance_id) is None:
        raise LockError("物质不存在", status=404)
    accounts.reauthenticate(session, user_id, password)
    if classifications is not None:
        for c in classifications:
            hazard_class = c["hazard_class"]
            if hazard_class not in BY_CODE:
                raise LockError(f"未知危害类别：{hazard_class}")
            rows = list(session.execute(select(SubstanceClassificationRow).where(
                SubstanceClassificationRow.substance_id == substance_id,
                SubstanceClassificationRow.source == "manual",
                SubstanceClassificationRow.hazard_class == hazard_class,
            )).scalars().all())
            if any(r.locked_at is not None for r in rows):
                raise LockError(f"{hazard_class} 已有锁定行，锁定后分类不可经此端点覆盖", status=409)
            for r in rows:
                session.delete(r)
            session.add(SubstanceClassificationRow(
                id=str(uuid.uuid4()), substance_id=substance_id, hazard_class=hazard_class,
                category=c["category"], h_code=c.get("h_code"), scl=c.get("scl"),
                m_factor_acute=c.get("m_factor_acute"), m_factor_chronic=c.get("m_factor_chronic"),
                ate_value=c.get("ate_value"), ate_unit=c.get("ate_unit"),
                source="manual", source_ref="ehs_lock", confidence=1.0,
            ))
        session.flush()
    manual = list(session.execute(select(SubstanceClassificationRow).where(
        SubstanceClassificationRow.substance_id == substance_id,
        SubstanceClassificationRow.source == "manual",
    )).scalars().all())
    if not manual:
        raise LockError("该物质没有 manual 分类行，无法锁定（请先提交确认的分类）")
    now = utcnow()
    for row in manual:
        row.locked_at = now
        row.locked_by = user_id
    hash_chain.append(session, actor=user_id, action="regulatory.lock", entity_type="substance",
                      entity_id=substance_id,
                      detail={"locked_rows": len(manual),
                              "hazard_classes": sorted({r.hazard_class for r in manual})})
    return {"substance_id": substance_id, "locked_rows": len(manual),
            "locked_at": now.isoformat(), "locked_by": user_id}
