"""Add task_outbox.error (stage 0 / SR-002 outbox FAILED terminal state).

Revision ID: 0083
Revises: 0082
Create Date: 2026-10-31

Before this, ``_OutboxTask.on_failure`` marked failed tasks DONE, so
operations lost all visibility into failures and recovery could never
replay them. The stopgap records failures as FAILED with a short summary
(exception class + truncated message) in the new nullable ``error``
column. No data is rewritten; existing rows keep NULL.

On a fresh database the baseline already creates the column, so the
step checks first and is safe to repeat. Downgrade drops the column
(the same migration's own undo, excluded from the migration-discipline
gate like 0079's downgrade).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0083"
down_revision: str | None = "0082"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "task_outbox"
_COLUMN = "error"


def _has_column(bind) -> bool:
    insp = sa.inspect(bind)
    if _TABLE not in insp.get_table_names():
        return False
    return any(c["name"] == _COLUMN for c in insp.get_columns(_TABLE))


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        return
    if _TABLE not in sa.inspect(bind).get_table_names():
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_COLUMN)
