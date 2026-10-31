"""G9 product archive / restore, W1-8 state-machine semantics (D9).

Archive maps to the terminal ``withdrawn`` state; restore returns the
product to the state it had before archiving (stored in
``products.archived_from_state``). Rows archived before that column
existed restore to ``rd`` (legacy fallback).

Rules (W1-8, superseding the G9 lightweight variant):
- Archiving a released product is REFUSED: a released product's
  revisions, documents and delivery package are still the published
  truth, and archiving would leave them contradicting the product
  state. Withdraw the release first, then archive.
- The same refusal applies when any revision is still ``released``
  (fail-closed for legacy rows whose product state drifted).
- Archive and restore both write hash-chained audit rows, like the
  withdrawal-grade actions they are.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import hash_chain
from ...db.models_plm import ProductRevisionRow, ProductRow


def _product_or_raise(session: Session, product_id: str) -> ProductRow:
    row = session.get(ProductRow, product_id)
    if row is None:
        raise ValueError("产品不存在")
    return row


def archive_product(session: Session, product_id: str, *, user_id: str) -> ProductRow:
    row = _product_or_raise(session, product_id)
    if row.lifecycle_state == "withdrawn":
        raise ValueError("产品已归档")
    if row.lifecycle_state == "released":
        raise ValueError("已发布产品不能直接归档，请先撤回其发布包")
    released_rev = session.execute(select(ProductRevisionRow.id).where(
        ProductRevisionRow.product_id == row.id, ProductRevisionRow.state == "released")).first()
    if released_rev is not None:
        raise ValueError("产品仍有已发布修订版，不能直接归档，请先撤回其发布包")
    before = row.lifecycle_state
    row.archived_from_state = before
    row.lifecycle_state = "withdrawn"
    hash_chain.append(session, actor=user_id, action="product.archive", entity_type="product",
                      entity_id=row.id, detail={"from": before, "to": "withdrawn"})
    session.flush()
    return row


def restore_product(session: Session, product_id: str, *, user_id: str) -> ProductRow:
    row = _product_or_raise(session, product_id)
    if row.lifecycle_state != "withdrawn":
        raise ValueError("只有已归档产品可以恢复")
    target = row.archived_from_state or "rd"
    row.archived_from_state = None
    row.lifecycle_state = target
    hash_chain.append(session, actor=user_id, action="product.restore", entity_type="product",
                      entity_id=row.id, detail={"from": "withdrawn", "to": target})
    session.flush()
    return row
