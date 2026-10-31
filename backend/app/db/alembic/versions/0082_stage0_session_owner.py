"""Add chat_sessions.owner_id (stage 0 / SR-006 session ownership).

Revision ID: 0082
Revises: 0081
Create Date: 2026-10-31

Chat session endpoints used to take any session id as a capability:
``GET /api/session/list`` enumerated every session, and a stolen id
unlocked another user's MCP write grants through the chat path. The
owner column lets the API filter by owning login. It stays nullable:
pre-upgrade rows have no owner, and because session ids are guessable
(``chat-<timestamp>-<seq>``) a NULL owner must NOT be grandfathered —
for JWT callers such rows are admin-only. No data is rewritten.

On a fresh database the baseline already creates the column, so the
step checks first and is safe to repeat. Downgrade drops the column
(the same migration's own undo, excluded from the migration-discipline
gate like 0079's downgrade).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0082"
down_revision: str | None = "0081"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "chat_sessions"
_COLUMN = "owner_id"


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
        batch.add_column(sa.Column(_COLUMN, sa.String(64), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_COLUMN)
