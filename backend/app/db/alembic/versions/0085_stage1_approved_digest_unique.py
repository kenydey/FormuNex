"""Partial unique index on approved release digests (stage 1 / SR-030).

At most one APPROVED ``regulatory_data_releases`` row per
``input_digest``: backfill reuses the prior release id instead of
stacking approved rows, and this index makes a second approved row
with the same digest impossible. PARTIAL on purpose — draft / import /
superseded rows with the same digest stay possible, and a plain unique
on input_digest would also block supplier re-imports.

Dirty-data guard (same rule as 0081): if approved duplicates already
exist, the migration REFUSES with their digests and counts and deletes
nothing. The dev database was inventoried before this migration
(zero release rows, zero duplicates); a production database with
duplicates needs its cleanup decided before upgrading. Downgrade drops
the index (the same migration's own undo, excluded from the
migration-discipline gate like 0079's downgrade).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0085"
down_revision: str | None = "0084"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "regulatory_data_releases"
_INDEX = "uq_approved_release_per_digest"
_DDL = (
    f"CREATE UNIQUE INDEX {_INDEX} ON {_TABLE} (input_digest) "
    f"WHERE status = 'approved'"
)


def _duplicates(bind):
    if _TABLE not in sa.inspect(bind).get_table_names():
        return []
    return bind.execute(sa.text(
        f"SELECT input_digest, COUNT(*) AS n FROM {_TABLE} "
        f"WHERE status = 'approved' GROUP BY input_digest HAVING COUNT(*) > 1"
    )).all()


def _has_index(bind) -> bool:
    return any(
        ix["name"] == _INDEX
        for ix in sa.inspect(bind).get_indexes(_TABLE)
    ) if _TABLE in sa.inspect(bind).get_table_names() else False


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names():
        return
    dupes = _duplicates(bind)
    if dupes:
        detail = ", ".join(f"{d[0] or '(empty)'} x{d[1]}" for d in dupes)
        raise RuntimeError(
            "0085 refused: duplicate APPROVED release digests already exist "
            f"({detail}). Decide their cleanup first; nothing was deleted."
        )
    if _has_index(bind):
        return
    op.execute(_DDL)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_index(bind):
        return
    op.execute(f"DROP INDEX {_INDEX}")
