"""Add eln_sync_conflicts.remote_units (stage 1 / SR-051).

take_remote needs the remote units the conflict snapshot was taken
with, so it can convert to the method unit before storing (1-10).
Nullable JSON, no backfill: NULL means a pre-stage-1 snapshot, and
resolve falls back to the method unit for those rows exactly as
before. On a fresh database the baseline already creates the column,
so the step checks first and is safe to repeat. Downgrade drops the
column (the same migration's own undo, excluded from the
migration-discipline gate like 0079's downgrade).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0084"
down_revision: str | None = "0083"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "eln_sync_conflicts"
_COLUMN = "remote_units"


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
        batch.add_column(sa.Column(_COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_COLUMN)
