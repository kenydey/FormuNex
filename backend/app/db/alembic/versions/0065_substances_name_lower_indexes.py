"""Case-insensitive substance name indexes for the local-library lookup tier.

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-19

``chemical_lookup._lookup_local_library`` matches Chinese / English names
case-insensitively via ``lower(name_zh)`` / ``lower(name_en)``. A plain index
does not serve a ``lower(col) = ...`` predicate on SQLite or PostgreSQL, so
without these expression indexes every name lookup would full-scan the 131k
``substances`` table. CAS lookup already rides the ``cas_no`` unique index;
``name_zh`` / ``name_en`` exact matches ride their plain indexes.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the indexes already exist; every step checks first and is safe to repeat.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "substances"
_INDEXES = (
    ("ix_substances_name_zh_lower", "lower(name_zh)"),
    ("ix_substances_name_en_lower", "lower(name_en)"),
)


def _existing_indexes() -> set[str]:
    bind = op.get_bind()
    if _TABLE not in sa.inspect(bind).get_table_names():
        return set()
    # NOTE: ``inspect().get_indexes()`` cannot reflect expression indexes
    # (SQLAlchemy skips them with a warning), so a reflection-based check-first
    # guard would re-create an existing index and fail on the second upgrade.
    # Read the catalog directly instead.
    dialect = bind.dialect.name
    if dialect == "sqlite":
        rows = bind.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=:t"),
            {"t": _TABLE},
        )
        return {r[0] for r in rows}
    if dialect == "postgresql":
        rows = bind.execute(
            sa.text("SELECT indexname FROM pg_indexes WHERE tablename=:t"), {"t": _TABLE}
        )
        return {r[0] for r in rows}
    return {ix["name"] for ix in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    existing = _existing_indexes()
    for name, expr in _INDEXES:
        if name not in existing:
            op.create_index(name, _TABLE, [sa.text(expr)])


def downgrade() -> None:
    existing = _existing_indexes()
    for name, _expr in reversed(_INDEXES):
        if name in existing:
            op.drop_index(name, table_name=_TABLE)
