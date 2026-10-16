"""PageIndex document table-of-contents trees.

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-16

One JSON tree per source document (heading hierarchy, page ranges, table anchors and cross references),
built at ingest time by ``services/pageindex/tree_builder.py`` and walked by the stage-2 navigator.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the tables already exist; every step checks first and is safe to repeat. The table definitions
below are frozen copies of the models at this revision.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()

def _create_tables() -> None:
    if not _has_table('document_toc_trees'):
        op.create_table('document_toc_trees',
        sa.Column('source_id', sa.String(length=36), nullable=False),
        sa.Column('tree', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('builder_version', sa.String(length=16), nullable=False),
        sa.Column('doc_kind', sa.String(length=16), nullable=False),
        sa.Column('node_count', sa.Integer(), nullable=False),
        sa.Column('max_depth', sa.Integer(), nullable=False),
        sa.Column('page_count', sa.Integer(), nullable=False),
        sa.Column('summaries_status', sa.String(length=16), nullable=False),
        sa.Column('built_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('source_id')
        )


def _drop_tables() -> None:
    if _has_table('document_toc_trees'):
        op.drop_table('document_toc_trees')


def upgrade() -> None:
    _create_tables()


def downgrade() -> None:
    _drop_tables()
