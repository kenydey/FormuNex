"""PLM products, revisions, classification results and lineage edges.

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-16

``product_revisions`` pin a frozen formulation version and winning experiment; ``classification_results``
pin a GB 30000 result to the rule-set version and data release that produced it; ``lineage_edges``
record the R&D → PLM data chain.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the tables already exist; every step checks first and is safe to repeat. The table definitions
below are frozen copies of the models at this revision.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()

def _create_tables() -> None:
    if not _has_table('lineage_edges'):
        op.create_table('lineage_edges',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('from_type', sa.String(length=32), nullable=False),
        sa.Column('from_id', sa.String(length=64), nullable=False),
        sa.Column('to_type', sa.String(length=32), nullable=False),
        sa.Column('to_id', sa.String(length=64), nullable=False),
        sa.Column('relation', sa.String(length=32), nullable=False),
        sa.Column('created_by', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('from_type', 'from_id', 'to_type', 'to_id', 'relation', name='uq_lineage_edge')
        )
        op.create_index('ix_lineage_from', 'lineage_edges', ['from_type', 'from_id'], unique=False)
        op.create_index('ix_lineage_to', 'lineage_edges', ['to_type', 'to_id'], unique=False)
    if not _has_table('products'):
        op.create_table('products',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('name_zh', sa.String(length=200), nullable=False),
        sa.Column('name_en', sa.String(length=200), nullable=False),
        sa.Column('project_id', sa.String(length=64), nullable=True),
        sa.Column('lifecycle_state', sa.String(length=16), nullable=False),
        sa.Column('created_by', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_products_code'), 'products', ['code'], unique=True)
        op.create_index(op.f('ix_products_lifecycle_state'), 'products', ['lifecycle_state'], unique=False)
        op.create_index(op.f('ix_products_project_id'), 'products', ['project_id'], unique=False)
    if not _has_table('product_revisions'):
        op.create_table('product_revisions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('product_id', sa.String(length=36), nullable=False),
        sa.Column('rev', sa.String(length=8), nullable=False),
        sa.Column('formulation_version_id', sa.String(length=36), nullable=True),
        sa.Column('winning_experiment_id', sa.Integer(), nullable=True),
        sa.Column('spec_snapshot', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('frozen_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['formulation_version_id'], ['formulation_versions.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('product_id', 'rev', name='uq_product_revision')
        )
        op.create_index(op.f('ix_product_revisions_formulation_version_id'), 'product_revisions', ['formulation_version_id'], unique=False)
        op.create_index(op.f('ix_product_revisions_product_id'), 'product_revisions', ['product_id'], unique=False)
        op.create_index(op.f('ix_product_revisions_state'), 'product_revisions', ['state'], unique=False)
    if not _has_table('classification_results'):
        op.create_table('classification_results',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('revision_id', sa.String(length=36), nullable=True),
        sa.Column('formulation_version_id', sa.String(length=36), nullable=True),
        sa.Column('rule_set_version', sa.String(length=40), nullable=False),
        sa.Column('data_release_id', sa.String(length=36), nullable=True),
        sa.Column('inputs_hash', sa.String(length=64), nullable=False),
        sa.Column('result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('trace', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('computed_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['revision_id'], ['product_revisions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_classification_results_formulation_version_id'), 'classification_results', ['formulation_version_id'], unique=False)
        op.create_index(op.f('ix_classification_results_inputs_hash'), 'classification_results', ['inputs_hash'], unique=False)
        op.create_index(op.f('ix_classification_results_revision_id'), 'classification_results', ['revision_id'], unique=False)


def _drop_tables() -> None:
    if _has_table('classification_results'):
        op.drop_table('classification_results')
    if _has_table('product_revisions'):
        op.drop_table('product_revisions')
    if _has_table('products'):
        op.drop_table('products')
    if _has_table('lineage_edges'):
        op.drop_table('lineage_edges')


def upgrade() -> None:
    _create_tables()


def downgrade() -> None:
    _drop_tables()
