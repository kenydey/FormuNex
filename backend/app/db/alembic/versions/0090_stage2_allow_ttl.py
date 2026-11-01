"""Stage 2 / SR-041: ``mcp_tool_policies.expires_at`` for allow TTL.

Allow policies stop being forever-grants: session 24h, project 30d,
global 90d (once is consumed on first use and needs no TTL; block/ask
are fail-closed and stay until revoked). The column is nullable — a
NULL means "legacy row, expiry computed from created_at + TTL at
evaluation time", so pre-0090 allows self-expire by age without any
backfill run; ``backfill_allow_expiry`` materializes the value and
drops already-expired rows. Check-first, safe to repeat. Downgrade
drops the column.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0090"
down_revision: str | None = "0089"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "mcp_tool_policies"


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names():
        return
    cols = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if "expires_at" not in cols:
        op.add_column(_TABLE, sa.Column("expires_at", sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names():
        return
    cols = {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}
    if "expires_at" in cols:
        op.drop_column(_TABLE, "expires_at")
