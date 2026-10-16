"""Regulatory documents, templates, batches and batch QC results.

Revision ID: 0056
Revises: 0055
Create Date: 2026-10-16

``regulatory_documents`` hold SDS / TDS / COA / label content as structured JSON (the source of truth;
PDF and Word are renderings); ``doc_templates`` version the rendering templates; ``batches`` and
``batch_qc_results`` feed COA generation.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the tables already exist; every step checks first and is safe to repeat. The table definitions
below are frozen copies of the models at this revision.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()

def _create_tables() -> None:
    if not _has_table('doc_templates'):
        op.create_table('doc_templates',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('doc_type', sa.String(length=16), nullable=False),
        sa.Column('lang', sa.String(length=8), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('engine', sa.String(length=16), nullable=False),
        sa.Column('content_key', sa.String(length=300), nullable=False),
        sa.Column('is_default', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('doc_type', 'lang', 'name', 'version', name='uq_doc_template')
        )
        op.create_index(op.f('ix_doc_templates_doc_type'), 'doc_templates', ['doc_type'], unique=False)
    if not _has_table('batches'):
        op.create_table('batches',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('product_id', sa.String(length=36), nullable=False),
        sa.Column('revision_id', sa.String(length=36), nullable=True),
        sa.Column('batch_no', sa.String(length=64), nullable=False),
        sa.Column('produced_at', sa.DateTime(), nullable=True),
        sa.Column('quantity_kg', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('product_id', 'batch_no', name='uq_batch')
        )
        op.create_index(op.f('ix_batches_product_id'), 'batches', ['product_id'], unique=False)
        op.create_index(op.f('ix_batches_revision_id'), 'batches', ['revision_id'], unique=False)
    if not _has_table('batch_qc_results'):
        op.create_table('batch_qc_results',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('batch_id', sa.String(length=36), nullable=False),
        sa.Column('metric', sa.String(length=80), nullable=False),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('value_text', sa.String(length=120), nullable=False),
        sa.Column('unit', sa.String(length=32), nullable=False),
        sa.Column('test_method', sa.String(length=80), nullable=False),
        sa.Column('spec_min', sa.Float(), nullable=True),
        sa.Column('spec_max', sa.Float(), nullable=True),
        sa.Column('verdict', sa.String(length=8), nullable=True),
        sa.Column('measurement_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_batch_qc_results_batch_id'), 'batch_qc_results', ['batch_id'], unique=False)
    if not _has_table('regulatory_documents'):
        op.create_table('regulatory_documents',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('revision_id', sa.String(length=36), nullable=False),
        sa.Column('batch_id', sa.String(length=36), nullable=True),
        sa.Column('doc_type', sa.String(length=16), nullable=False),
        sa.Column('lang', sa.String(length=8), nullable=False),
        sa.Column('version_no', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('template_id', sa.String(length=36), nullable=True),
        sa.Column('content', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('field_sources', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('rendered_pdf_key', sa.String(length=300), nullable=True),
        sa.Column('rendered_docx_key', sa.String(length=300), nullable=True),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('supersedes_id', sa.String(length=36), nullable=True),
        sa.Column('created_by', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['revision_id'], ['product_revisions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('revision_id', 'doc_type', 'lang', 'version_no', name='uq_regulatory_document')
        )
        op.create_index(op.f('ix_regulatory_documents_batch_id'), 'regulatory_documents', ['batch_id'], unique=False)
        op.create_index(op.f('ix_regulatory_documents_doc_type'), 'regulatory_documents', ['doc_type'], unique=False)
        op.create_index(op.f('ix_regulatory_documents_revision_id'), 'regulatory_documents', ['revision_id'], unique=False)
        op.create_index(op.f('ix_regulatory_documents_status'), 'regulatory_documents', ['status'], unique=False)


def _drop_tables() -> None:
    if _has_table('regulatory_documents'):
        op.drop_table('regulatory_documents')
    if _has_table('batch_qc_results'):
        op.drop_table('batch_qc_results')
    if _has_table('batches'):
        op.drop_table('batches')
    if _has_table('doc_templates'):
        op.drop_table('doc_templates')


def upgrade() -> None:
    _create_tables()


def downgrade() -> None:
    _drop_tables()
