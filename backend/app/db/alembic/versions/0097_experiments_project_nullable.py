"""D6-9 (2026-11-26 review): experiments.project_id "" -> NULL.

The column was NOT NULL with a "" default, so unassigned experiments
were stored as "" — which equals no project id and satisfies no IS NULL
query either, making them invisible in every view (the same failure
mode as P3-D1 for doe_cycle_runs, fixed by 0095). This migration makes
the column nullable and normalises existing "" rows to NULL, matching
the doe_cycle_service.persist_experiments `project_id or None`
convention. The write side switches with it.

Check-first, safe to repeat. Downgrade restores NOT NULL after
converting NULL back to "" (the old marker).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0097"
down_revision: str | None = "0096"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "experiments"
_COLUMN = "project_id"


def _has_table(bind) -> bool:
    # Partial-schema upgrade tests (e.g. the 0081 batch-FK
    # fixture) build only a subset of tables; reflecting a
    # missing table raises NoSuchTableError. Real databases
    # always carry these tables, so skipping is a no-op there
    # (same spirit as the check-first guards below; 0096
    # already guards this way).
    return _TABLE in sa.inspect(bind).get_table_names()


def _is_nullable(bind) -> bool:
    return next(
        (c["nullable"] for c in sa.inspect(bind).get_columns(_TABLE) if c["name"] == _COLUMN),
        True,
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    # Alter first, then normalise: on SQLite the UPDATE would fail
    # against a genuinely NOT NULL column (0095's UPDATE-first order
    # only worked because that table had already been created nullable
    # by create_all_metadata). After the column allows NULL, the same
    # UPDATE is a no-op-safe repeat on PG.
    if not _is_nullable(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.alter_column(_COLUMN, nullable=True)
    op.execute(f"UPDATE {_TABLE} SET {_COLUMN}=NULL WHERE {_COLUMN}=''")


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    op.execute(f"UPDATE {_TABLE} SET {_COLUMN}='' WHERE {_COLUMN} IS NULL")
    if _is_nullable(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.alter_column(_COLUMN, nullable=False)
