"""P3-D1 (2026-11-04 deep review): doe_cycle_runs.project_id "" -> NULL.

The column was NOT NULL with a "" default, so unassigned cycle runs
were stored as "" — which equals no project id and satisfies no IS NULL
query either, making them invisible in every view (the cycle-runs
endpoint requires a non-empty project_id and filters ==). This
migration makes the column nullable and normalises existing "" rows
to NULL, matching the source_store.create `project_id or None`
convention. The write side (doe_cycle_service) switches with it.

Check-first, safe to repeat. Downgrade restores NOT NULL after
converting NULL back to "" (the old marker).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0095"
down_revision: str | None = "0094"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "doe_cycle_runs"
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
    # Normalise first: the batch copy preserves data, so "" rows must
    # become NULL before the column allows NULL (and after, on PG, the
    # same UPDATE is a no-op-safe repeat).
    op.execute(f"UPDATE {_TABLE} SET {_COLUMN}=NULL WHERE {_COLUMN}=''")
    if not _is_nullable(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.alter_column(_COLUMN, nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    op.execute(f"UPDATE {_TABLE} SET {_COLUMN}='' WHERE {_COLUMN} IS NULL")
    if _is_nullable(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.alter_column(_COLUMN, nullable=False)
