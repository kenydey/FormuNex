"""PostgreSQL-only: pg_trgm and trigram indexes for substance name search.

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-16

131k substance names need fuzzy (LIKE '%…%') search in Chinese and English. ``pg_trgm`` GIN indexes make
that fast on PostgreSQL. SQLite (CI only) has no equivalent; this revision is a no-op there.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the tables already exist; every step checks first and is safe to repeat. The table definitions
below are frozen copies of the models at this revision.
"""
from __future__ import annotations

from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


_INDEXES = (
    ("ix_substances_name_zh_trgm", "name_zh"),
    ("ix_substances_name_en_trgm", "name_en"),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, col in _INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON substances USING gin ({col} gin_trgm_ops)")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for name, _col in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
