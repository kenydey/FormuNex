"""P1-A7 (2026-11-04 deep review): DOE-cycle replay idempotency key.

Adds a nullable ``experiments.doe_task_id`` column so a replayed or
autoretried DOE cycle can find and drop its own previous unmeasured
batch before persisting a new one, instead of stacking duplicates.

Additive only (no drops, no constraint changes); existing rows keep
NULL and are untouched. Check-first, safe to repeat.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0093"
down_revision: str | None = "0092"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "experiments"
_COLUMN = "doe_task_id"
_INDEX = "ix_experiments_doe_task_id"


def _has_table(bind) -> bool:
    # Partial-schema upgrade tests (e.g. the 0081 batch-FK
    # fixture) build only a subset of tables; reflecting a
    # missing table raises NoSuchTableError. Real databases
    # always carry these tables, so skipping is a no-op there
    # (same spirit as the check-first guards below; 0096
    # already guards this way).
    return _TABLE in sa.inspect(bind).get_table_names()


def _has_column(bind) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def _has_index(bind) -> bool:
    return _INDEX in {i["name"] for i in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    if not _has_column(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.add_column(sa.Column(_COLUMN, sa.String(64), nullable=True))
    if not _has_index(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.create_index(_INDEX, [_COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    if _has_index(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_index(_INDEX)
    if _has_column(bind):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
