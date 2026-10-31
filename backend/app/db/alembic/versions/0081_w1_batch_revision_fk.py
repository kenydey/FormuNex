"""Add the batches.revision_id foreign key (W1-11).

Revision ID: 0081
Revises: 0080
Create Date: 2026-10-31

``create_batch`` used to store any string as ``revision_id``; the service now
validates it, and this migration makes the database agree: a non-NULL
``revision_id`` must point at an existing ``product_revisions`` row. The
column stays nullable (batches without a revision are legitimate — the
development database holds 4 such rows). Deleting a revision SETs the
column NULL instead of deleting batch (QC) history.

Dirty-data gate (W1-11 gate 2): the upgrade counts batches whose
``revision_id`` dangles or belongs to another product and refuses while
any exist — it never deletes or rewrites data. The development audit found
zero such rows; production must run the same audit before upgrading.
On a fresh database the baseline already creates the FK, so the step
checks first and is safe to repeat. Downgrade drops the constraint.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0081"
down_revision: str | None = "0080"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "batches"
_FK = "fk_batches_revision_id"
# See 0067: reflect without resolving foreign keys so partial / legacy schemas can be batch-migrated on SQLite.
_BATCH = {"reflect_kwargs": {"resolve_fks": False}}


def _has_fk(bind) -> bool:
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names():
        return False
    return any(fk["name"] == _FK for fk in insp.get_foreign_keys(_TABLE))


def _dirty_count(bind) -> int:
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names() or "product_revisions" not in insp.get_table_names():
        return 0
    dangling = bind.execute(text(
        "SELECT COUNT(*) FROM batches b LEFT JOIN product_revisions r ON r.id = b.revision_id "
        "WHERE b.revision_id IS NOT NULL AND r.id IS NULL")).scalar() or 0
    wrong_product = bind.execute(text(
        "SELECT COUNT(*) FROM batches b JOIN product_revisions r ON r.id = b.revision_id "
        "WHERE b.revision_id IS NOT NULL AND r.product_id != b.product_id")).scalar() or 0
    return dangling + wrong_product


def upgrade() -> None:
    bind = op.get_bind()
    if _has_fk(bind):
        return
    dirty = _dirty_count(bind)
    if dirty:
        raise RuntimeError(
            f"0081 upgrade refused: {dirty} batches carry a dangling or wrong-product revision_id; "
            "clean them up first (see the W1-11 audit), never auto-delete")
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names() or "product_revisions" not in insp.get_table_names():
        return
    with op.batch_alter_table(_TABLE, **_BATCH) as batch:
        batch.create_foreign_key(_FK, "product_revisions", ["revision_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_fk(bind):
        return
    with op.batch_alter_table(_TABLE, **_BATCH) as batch:
        batch.drop_constraint(_FK, type_="foreignkey")
