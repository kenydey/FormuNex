"""Unique index on transport rows (stage 1 / SR-034).

One row per (un_number, proper_shipping_name_zh, substance_id) for
``substance_transport``. Enforced as a unique index rather than a
constraint object because SQLite cannot ADD CONSTRAINT to an existing
table; the enforcement is identical on both dialects, and NULL
substance_id rows stay distinct under both dialects' NULL semantics.

Dirty-data guard (same rule as 0081/0085): if duplicate linked rows
already exist, the migration REFUSES with their keys and counts and
deletes nothing. The dev database was inventoried before this migration
(zero transport rows, zero duplicates); a production database with
duplicates needs its cleanup decided before upgrading. Downgrade drops
the index (the same migration's own undo, excluded from the
migration-discipline gate like 0079's downgrade).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0086"
down_revision: str | None = "0085"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "substance_transport"
_INDEX = "uq_transport_un_name_substance"
_DDL = (
    f"CREATE UNIQUE INDEX {_INDEX} ON {_TABLE} "
    f"(un_number, proper_shipping_name_zh, substance_id)"
)


def _duplicates(bind):
    if _TABLE not in sa.inspect(bind).get_table_names():
        return []
    return bind.execute(sa.text(
        f"SELECT un_number, proper_shipping_name_zh, substance_id, COUNT(*) AS n "
        f"FROM {_TABLE} WHERE substance_id IS NOT NULL "
        f"GROUP BY un_number, proper_shipping_name_zh, substance_id HAVING COUNT(*) > 1"
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
        detail = ", ".join(f"{d[0]} {d[1]} sid={d[2]} x{d[3]}" for d in dupes)
        raise RuntimeError(
            "0086 refused: duplicate transport rows already exist "
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
