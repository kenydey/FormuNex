"""Stage 2 / SR-037: parse_shards parser version + updated_at index.

``parser_version`` lets a newer shard format refuse to resume shards
written by an older one (NULL = pre-column legacy row = miss). The
``updated_at`` index keeps the crash-residue sweep from scanning the
whole table. Both steps check first and are safe to repeat; no backfill
— legacy rows are misses by definition, and the sweep removes them by
age. Downgrade drops both.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0088"
down_revision: str | None = "0087"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "parse_shards"
_INDEX = "ix_parse_shards_updated_at"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names():
        return
    cols = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if "parser_version" not in cols:
        op.add_column(_TABLE, sa.Column("parser_version", sa.String(16), nullable=True))
    if not any(ix["name"] == _INDEX for ix in sa.inspect(bind).get_indexes(_TABLE)):
        op.create_index(_INDEX, _TABLE, ["updated_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names():
        return
    if any(ix["name"] == _INDEX for ix in sa.inspect(bind).get_indexes(_TABLE)):
        op.drop_index(_INDEX, table_name=_TABLE)
    cols = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if "parser_version" in cols:
        op.drop_column(_TABLE, "parser_version")
