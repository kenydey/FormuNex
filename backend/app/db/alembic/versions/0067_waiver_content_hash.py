"""Bind release-gate waivers to the failure content they waive (P0-2).

Revision ID: 0067
Revises: 0066
Create Date: 2026-10-22

Adds ``release_gate_waivers.content_hash`` (sha256 of the gate item message
at waiver time) and replaces the unique constraint ``uq_gate_waiver``
(revision_id, check_code, item_key) with ``uq_gate_waiver_content``
(revision_id, check_code, item_key, content_hash), so a waiver stops
matching when the underlying limit or measured value changes the message.
Pre-0067 rows keep NULL content_hash and therefore never match a current
message — historical waivers are intentionally invalidated by the binding
change (EHS must be told); they are kept, not deleted, so the audit trail
survives.

``0001_baseline`` builds a fresh database with ``create_all`` from the
*current* models, so on a fresh database the tables already exist; every
step checks first and is safe to repeat. Downgrade note: re-creating the
old unique constraint fails if two rows for one (revision, check, item)
with different hashes exist — drop the newer waiver rows first.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "release_gate_waivers"
_OLD_UQ = "uq_gate_waiver"
_NEW_UQ = "uq_gate_waiver_content"
_NEW_COLS = ["revision_id", "check_code", "item_key", "content_hash"]
_OLD_COLS = ["revision_id", "check_code", "item_key"]


def _has_table(bind) -> bool:
    return _TABLE in sa.inspect(bind).get_table_names()


def _columns(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def _unique_names(bind) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_unique_constraints(_TABLE)}


# SQLite rebuilds the table in batch mode by reflecting it; don't resolve foreign keys, so a database
# that lacks a referenced table (partial / legacy schemas upgraded step by step) can still be migrated.
_BATCH = {"reflect_kwargs": {"resolve_fks": False}}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    cols = _columns(bind)
    uqs = _unique_names(bind)
    with op.batch_alter_table(_TABLE, **_BATCH) as batch:
        if "content_hash" not in cols:
            batch.add_column(sa.Column("content_hash", sa.String(64), nullable=True))
        if _OLD_UQ in uqs:
            batch.drop_constraint(_OLD_UQ, type_="unique")
        if _NEW_UQ not in uqs:
            batch.create_unique_constraint(_NEW_UQ, _NEW_COLS)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    cols = _columns(bind)
    uqs = _unique_names(bind)
    with op.batch_alter_table(_TABLE, **_BATCH) as batch:
        if _NEW_UQ in uqs:
            batch.drop_constraint(_NEW_UQ, type_="unique")
        if _OLD_UQ not in uqs:
            batch.create_unique_constraint(_OLD_UQ, _OLD_COLS)
        if "content_hash" in cols:
            batch.drop_column("content_hash")
