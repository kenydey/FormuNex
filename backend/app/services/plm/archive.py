"""G9 lightweight product archive / restore.

Archive maps to the existing terminal ``withdrawn`` state (the same state
the release flow uses for withdrawal); restore maps back to ``rd``. The
pre-archive state is deliberately NOT stored: this is the lightweight
variant, and a restored product re-enters development as ``rd``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ...db.models_plm import ProductRow


def _product_or_raise(session: Session, product_id: str) -> ProductRow:
    row = session.get(ProductRow, product_id)
    if row is None:
        raise ValueError("产品不存在")
    return row


def archive_product(session: Session, product_id: str) -> ProductRow:
    row = _product_or_raise(session, product_id)
    if row.lifecycle_state == "withdrawn":
        raise ValueError("产品已归档")
    row.lifecycle_state = "withdrawn"
    session.flush()
    return row


def restore_product(session: Session, product_id: str) -> ProductRow:
    row = _product_or_raise(session, product_id)
    if row.lifecycle_state != "withdrawn":
        raise ValueError("只有已归档产品可以恢复")
    row.lifecycle_state = "rd"
    session.flush()
    return row
