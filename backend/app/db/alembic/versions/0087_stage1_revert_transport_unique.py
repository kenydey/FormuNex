"""Revert the 0086 transport unique index (stage 1 / SR-034 correction).

0086 added a unique index on (un_number, proper_shipping_name_zh,
substance_id). It cannot ship: the key contradicts the shipped data.

* Packing-group variants of one (UN, name, substance) are legitimate
  distinct rows — an existing seed test asserts both pg II and pg III
  rows for UN 1170 ethanol, and the bundled GB 12268 file holds 348
  repeated (UN, name) groups (851 rows).
* Four of those groups project to byte-identical stored rows (they
  differ only in packing/tank provision fields the seed does not
  store), so no unique key on the stored columns can hold at all.

The index therefore broke the core seed import instead of protecting
it. This migration drops it; the 1-14 drift report and EH40 variant
notes from the same item stay in place. Downgrade re-adds the index
and will fail while duplicate rows exist — that failure is the point:
re-adding the constraint requires deciding what the duplicates mean
first, not silently dropping rows.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0087"
down_revision: str | None = "0086"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "substance_transport"
_INDEX = "uq_transport_un_name_substance"
_DDL = (
    f"CREATE UNIQUE INDEX {_INDEX} ON {_TABLE} "
    f"(un_number, proper_shipping_name_zh, substance_id)"
)


def _has_index(bind) -> bool:
    return any(
        ix["name"] == _INDEX
        for ix in sa.inspect(bind).get_indexes(_TABLE)
    ) if _TABLE in sa.inspect(bind).get_table_names() else False


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_index(bind):
        return
    op.execute(f"DROP INDEX {_INDEX}")


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names() or _has_index(bind):
        return
    op.execute(_DDL)
