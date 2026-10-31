"""Widen OEL notes to 2000 characters (W1-9).

Revision ID: 0080
Revises: 0079
Create Date: 2026-10-31

W1-9 stores EH40 multi-variant notes one variant per line instead of joining
with "；" and truncating at 200 characters — the old cap cut later variants'
limits (worst bundled case: 203 characters, so the cut was real, not
theoretical). ``substance_oel.notes`` goes from VARCHAR(200) to VARCHAR(2000).

Widening never loses data and needs no backfill. On a fresh database the
baseline already creates the wider column, so the step checks the current
length first and is safe to repeat. Downgrade shortens the column back and
refuses while a longer value is stored.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0080"
down_revision: str | None = "0079"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "substance_oel"
_COL = "notes"
# See 0067: reflect without resolving foreign keys so partial / legacy schemas can be batch-migrated on SQLite.
_BATCH = {"reflect_kwargs": {"resolve_fks": False}}


def _length(bind) -> int | None:
    if _TABLE not in sa.inspect(bind).get_table_names():
        return None
    for c in sa.inspect(bind).get_columns(_TABLE):
        if c["name"] == _COL:
            return getattr(c["type"], "length", None)
    return None


def _alter(length: int) -> None:
    bind = op.get_bind()
    current = _length(bind)
    if current is None or current == length:
        return
    with op.batch_alter_table(_TABLE, **_BATCH) as batch:
        batch.alter_column(_COL, type_=sa.String(length), existing_type=sa.String(current),
                           existing_nullable=False)


def upgrade() -> None:
    _alter(2000)


def downgrade() -> None:
    bind = op.get_bind()
    if _length(bind) is None:
        return
    longest = bind.execute(text("SELECT MAX(LENGTH(notes)) FROM substance_oel")).scalar()
    if (longest or 0) > 200:
        raise RuntimeError("0080 downgrade refused: OEL notes longer than 200 characters are stored")
    _alter(200)
