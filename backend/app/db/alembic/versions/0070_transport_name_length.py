"""Widen GB 12268 proper shipping names to 500 characters.

Revision ID: 0070
Revises: 0069
Create Date: 2026-10-22

The full GB 12268-2025 table has one English name of 342 characters
(the rest fit in 300). Official names are not truncated, so
``substance_transport.proper_shipping_name_zh`` / ``_en`` go from
VARCHAR(300) to VARCHAR(500).

Widening never loses data and needs no backfill. On a fresh database the
baseline already creates the wider columns, so every step checks the
current length first and is safe to repeat. Downgrade shortens the
columns back and refuses while a longer value is stored.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "substance_transport"
_COLS = ("proper_shipping_name_zh", "proper_shipping_name_en")
# See 0067: reflect without resolving foreign keys so partial / legacy schemas can be batch-migrated on SQLite.
_BATCH = {"reflect_kwargs": {"resolve_fks": False}}


def _lengths(bind) -> dict[str, int | None]:
    if _TABLE not in sa.inspect(bind).get_table_names():
        return {}
    return {c["name"]: getattr(c["type"], "length", None)
            for c in sa.inspect(bind).get_columns(_TABLE) if c["name"] in _COLS}


def _alter(length: int) -> None:
    bind = op.get_bind()
    current = _lengths(bind)
    todo = [c for c in _COLS if c in current and current[c] != length]
    if not todo:
        return
    with op.batch_alter_table(_TABLE, **_BATCH) as batch:
        for col in todo:
            batch.alter_column(col, type_=sa.String(length), existing_type=sa.String(current[col]),
                               existing_nullable=False)


def upgrade() -> None:
    _alter(500)


def downgrade() -> None:
    bind = op.get_bind()
    if not _lengths(bind):
        return
    longest = bind.execute(text(
        "SELECT MAX(LENGTH(proper_shipping_name_zh)), MAX(LENGTH(proper_shipping_name_en)) FROM substance_transport"
    )).one()
    if any((n or 0) > 300 for n in longest):
        raise RuntimeError("0070 downgrade refused: proper shipping names longer than 300 characters are stored")
    _alter(300)
