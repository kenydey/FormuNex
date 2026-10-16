"""Substance-level regulatory master data.

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-16

Adds ``substances`` (CAS-level chemicals), per-source ``substance_classifications``, GBZ 2.1 exposure
limits, GB 12268 transport entries, H/P statement texts, generic (no-CAS) catalogue entries, the
human review queue, and ``regulatory_data_releases`` (one row per import run). Data is loaded by
``backend/scripts/seed_regulatory_db.py``, not by this migration.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the tables already exist; every step checks first and is safe to repeat. The table definitions
below are frozen copies of the models at this revision.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()

def _create_tables() -> None:
    if not _has_table('ghs_statements'):
        op.create_table('ghs_statements',
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('kind', sa.String(length=1), nullable=False),
        sa.Column('text_zh', sa.Text(), nullable=False),
        sa.Column('text_en', sa.Text(), nullable=False),
        sa.Column('hazard_type', sa.String(length=80), nullable=False),
        sa.Column('hazard_category', sa.String(length=120), nullable=False),
        sa.Column('precaution_category', sa.String(length=40), nullable=False),
        sa.Column('usage_condition', sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint('code')
        )
        op.create_index(op.f('ix_ghs_statements_kind'), 'ghs_statements', ['kind'], unique=False)
    if not _has_table('regulatory_data_releases'):
        op.create_table('regulatory_data_releases',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('label', sa.String(length=80), nullable=False),
        sa.Column('source', sa.String(length=40), nullable=False),
        sa.Column('stats', sa.JSON(), nullable=False),
        sa.Column('input_digest', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_regulatory_data_releases_label'), 'regulatory_data_releases', ['label'], unique=False)
    if not _has_table('regulatory_review_queue'):
        op.create_table('regulatory_review_queue',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('substance_id', sa.String(length=36), nullable=True),
        sa.Column('cas_no', sa.String(length=32), nullable=True),
        sa.Column('name', sa.String(length=300), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=False),
        sa.Column('release_id', sa.String(length=36), nullable=True),
        sa.Column('resolved_by', sa.String(length=36), nullable=True),
        sa.Column('resolved_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_regulatory_review_queue_kind'), 'regulatory_review_queue', ['kind'], unique=False)
        op.create_index(op.f('ix_regulatory_review_queue_release_id'), 'regulatory_review_queue', ['release_id'], unique=False)
        op.create_index(op.f('ix_regulatory_review_queue_status'), 'regulatory_review_queue', ['status'], unique=False)
        op.create_index(op.f('ix_regulatory_review_queue_substance_id'), 'regulatory_review_queue', ['substance_id'], unique=False)
    if not _has_table('substance_generic_entries'):
        op.create_table('substance_generic_entries',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('catalog_no', sa.Integer(), nullable=True),
        sa.Column('name_zh', sa.String(length=300), nullable=False),
        sa.Column('name_en', sa.String(length=300), nullable=False),
        sa.Column('aliases', sa.JSON(), nullable=False),
        sa.Column('hazard_text', sa.Text(), nullable=False),
        sa.Column('parsed', sa.JSON(), nullable=False),
        sa.Column('match_rule', sa.JSON(), nullable=False),
        sa.Column('release_id', sa.String(length=36), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_substance_generic_entries_catalog_no'), 'substance_generic_entries', ['catalog_no'], unique=False)
    if not _has_table('substances'):
        op.create_table('substances',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('cas_no', sa.String(length=32), nullable=True),
        sa.Column('ec_no', sa.String(length=32), nullable=True),
        sa.Column('name_zh', sa.String(length=300), nullable=False),
        sa.Column('name_en', sa.String(length=300), nullable=False),
        sa.Column('aliases', sa.JSON(), nullable=False),
        sa.Column('mf', sa.String(length=120), nullable=True),
        sa.Column('smiles', sa.Text(), nullable=True),
        sa.Column('iecsc_listed', sa.Boolean(), nullable=True),
        sa.Column('hazardous_catalog_no', sa.Integer(), nullable=True),
        sa.Column('highly_toxic', sa.Boolean(), nullable=True),
        sa.Column('key_supervised', sa.Boolean(), nullable=True),
        sa.Column('precursor_control', sa.Boolean(), nullable=True),
        sa.Column('explosive_precursor', sa.Boolean(), nullable=True),
        sa.Column('release_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_substances_cas_no'), 'substances', ['cas_no'], unique=True)
        op.create_index(op.f('ix_substances_hazardous_catalog_no'), 'substances', ['hazardous_catalog_no'], unique=False)
        op.create_index(op.f('ix_substances_name_en'), 'substances', ['name_en'], unique=False)
        op.create_index('ix_substances_name_zh', 'substances', ['name_zh'], unique=False)
        op.create_index(op.f('ix_substances_release_id'), 'substances', ['release_id'], unique=False)
    if not _has_table('substance_classifications'):
        op.create_table('substance_classifications',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('substance_id', sa.String(length=36), nullable=False),
        sa.Column('hazard_class', sa.String(length=48), nullable=False),
        sa.Column('category', sa.String(length=16), nullable=False),
        sa.Column('h_code', sa.String(length=16), nullable=True),
        sa.Column('is_minimum', sa.Boolean(), nullable=False),
        sa.Column('qualifier', sa.String(length=200), nullable=False),
        sa.Column('scl', sa.JSON(), nullable=True),
        sa.Column('m_factor_acute', sa.Integer(), nullable=True),
        sa.Column('m_factor_chronic', sa.Integer(), nullable=True),
        sa.Column('ate_value', sa.Float(), nullable=True),
        sa.Column('ate_unit', sa.String(length=16), nullable=True),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('source_ref', sa.String(length=300), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('locked_by', sa.String(length=36), nullable=True),
        sa.Column('locked_at', sa.DateTime(), nullable=True),
        sa.Column('release_id', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['substance_id'], ['substances.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('substance_id', 'hazard_class', 'source', 'category', name='uq_substance_classification')
        )
        op.create_index(op.f('ix_substance_classifications_hazard_class'), 'substance_classifications', ['hazard_class'], unique=False)
        op.create_index(op.f('ix_substance_classifications_release_id'), 'substance_classifications', ['release_id'], unique=False)
        op.create_index(op.f('ix_substance_classifications_source'), 'substance_classifications', ['source'], unique=False)
        op.create_index(op.f('ix_substance_classifications_substance_id'), 'substance_classifications', ['substance_id'], unique=False)
        op.create_index('ix_substance_cls_substance_source', 'substance_classifications', ['substance_id', 'source'], unique=False)
    if not _has_table('substance_oel'):
        op.create_table('substance_oel',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('substance_id', sa.String(length=36), nullable=False),
        sa.Column('standard', sa.String(length=40), nullable=False),
        sa.Column('mac', sa.Float(), nullable=True),
        sa.Column('pc_twa', sa.Float(), nullable=True),
        sa.Column('pc_stel', sa.Float(), nullable=True),
        sa.Column('critical_effect', sa.String(length=300), nullable=False),
        sa.Column('notes', sa.String(length=200), nullable=False),
        sa.Column('release_id', sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(['substance_id'], ['substances.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('substance_id', 'standard', name='uq_substance_oel')
        )
        op.create_index(op.f('ix_substance_oel_substance_id'), 'substance_oel', ['substance_id'], unique=False)
    if not _has_table('substance_transport'):
        op.create_table('substance_transport',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('un_number', sa.String(length=8), nullable=False),
        sa.Column('substance_id', sa.String(length=36), nullable=True),
        sa.Column('proper_shipping_name_zh', sa.String(length=300), nullable=False),
        sa.Column('proper_shipping_name_en', sa.String(length=300), nullable=False),
        sa.Column('transport_class', sa.String(length=8), nullable=False),
        sa.Column('subsidiary_risk', sa.String(length=32), nullable=False),
        sa.Column('packing_group', sa.String(length=4), nullable=True),
        sa.Column('marine_pollutant', sa.Boolean(), nullable=True),
        sa.Column('special_provisions', sa.String(length=200), nullable=False),
        sa.Column('standard', sa.String(length=40), nullable=False),
        sa.Column('release_id', sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(['substance_id'], ['substances.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_substance_transport_substance_id'), 'substance_transport', ['substance_id'], unique=False)
        op.create_index(op.f('ix_substance_transport_un_number'), 'substance_transport', ['un_number'], unique=False)


def _drop_tables() -> None:
    if _has_table('substance_transport'):
        op.drop_table('substance_transport')
    if _has_table('substance_oel'):
        op.drop_table('substance_oel')
    if _has_table('substance_classifications'):
        op.drop_table('substance_classifications')
    if _has_table('substances'):
        op.drop_table('substances')
    if _has_table('substance_generic_entries'):
        op.drop_table('substance_generic_entries')
    if _has_table('regulatory_review_queue'):
        op.drop_table('regulatory_review_queue')
    if _has_table('regulatory_data_releases'):
        op.drop_table('regulatory_data_releases')
    if _has_table('ghs_statements'):
        op.drop_table('ghs_statements')


def upgrade() -> None:
    _create_tables()


def downgrade() -> None:
    _drop_tables()
