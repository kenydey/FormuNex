"""Add the transient ``parse_shards`` table for sharded PDF parsing (batch 1).

Revision ID: 0075
Revises: 0074
Create Date: 2026-10-30

Batch 1 shards any PDF over ``pdf_shard_pages`` into per-page-range parses
and caches each completed shard's markdown keyed by content SHA-256 + shard
index, so a crashed or retried parse resumes instead of re-OCRing finished
shards. Rows are deleted after a successful merge; the sweep in
``app/services/parse_shards.py`` removes crash residue older than
``pdf_shard_stale_days``.

``0001_baseline`` builds a fresh database with ``create_all`` from the
*current* models, so on a fresh database the table already exists; this
migration carries it to databases that already ran earlier revisions.
Every step checks first and is safe to repeat.
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, PrimaryKeyConstraint, String, Text
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision: str = "0075"
down_revision: str | None = "0074"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "parse_shards"


def _has_table() -> bool:
    return _TABLE in sa_inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table():
        return
    op.create_table(
        _TABLE,
        Column("content_sha256", String(64), nullable=False),
        Column("shard_index", Integer, nullable=False),
        Column("page_start", Integer, nullable=False),
        Column("page_end", Integer, nullable=False),
        Column("parser", String(32), nullable=False, server_default=""),
        Column("markdown", Text, nullable=False, server_default=""),
        Column("extras_json", Text, nullable=False, server_default=""),
        Column("updated_at", DateTime, nullable=False),
        PrimaryKeyConstraint("content_sha256", "shard_index"),
    )


def downgrade() -> None:
    if _has_table():
        op.drop_table(_TABLE)
