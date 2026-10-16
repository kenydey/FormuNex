"""Two-layer material model: materials regulatory columns + material_compositions.

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-16

A raw material is often itself a mixture. ``materials`` gains ``material_type``, ``phys_state``,
``regulatory_status`` and ``sds_document_id``; ``material_compositions`` records which substances a
material contains (from its supplier SDS), so GB 30000 classification can explode a formulation down to
substances.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the tables already exist; every step checks first and is safe to repeat. The table definitions
below are frozen copies of the models at this revision.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _material_columns() -> list[sa.Column]:
    """Fresh Column objects each call (a Column can only be attached to one table)."""
    return [
        sa.Column("material_type", sa.String(length=16), nullable=True),
        sa.Column("phys_state", sa.String(length=16), nullable=True),
        sa.Column("regulatory_status", sa.String(length=16), nullable=False, server_default="unreviewed"),
        sa.Column("sds_document_id", sa.String(length=36), nullable=True),
    ]


def _existing_material_columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("materials")}


def _create_tables() -> None:
    if not _has_table('material_compositions'):
        op.create_table('material_compositions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('material_id', sa.String(length=36), nullable=False),
        sa.Column('substance_id', sa.String(length=36), nullable=False),
        sa.Column('pct_min', sa.Float(), nullable=True),
        sa.Column('pct_max', sa.Float(), nullable=True),
        sa.Column('pct_typical', sa.Float(), nullable=True),
        sa.Column('is_trade_secret', sa.Boolean(), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('source_ref', sa.String(length=300), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['material_id'], ['materials.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['substance_id'], ['substances.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('material_id', 'substance_id', name='uq_material_composition')
        )
        op.create_index(op.f('ix_material_compositions_material_id'), 'material_compositions', ['material_id'], unique=False)
        op.create_index(op.f('ix_material_compositions_substance_id'), 'material_compositions', ['substance_id'], unique=False)


def _drop_tables() -> None:
    if _has_table('material_compositions'):
        op.drop_table('material_compositions')


def upgrade() -> None:
    if _has_table("materials"):
        existing = _existing_material_columns()
        for col in _material_columns():
            if col.name not in existing:
                op.add_column("materials", col)
        indexes = {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes("materials")}
        if "ix_materials_regulatory_status" not in indexes:
            op.create_index("ix_materials_regulatory_status", "materials", ["regulatory_status"])
    _create_tables()


def downgrade() -> None:
    _drop_tables()
    if _has_table("materials"):
        existing = _existing_material_columns()
        indexes = {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes("materials")}
        with op.batch_alter_table("materials") as batch:
            if "ix_materials_regulatory_status" in indexes:
                batch.drop_index("ix_materials_regulatory_status")
            for col in reversed(_material_columns()):
                if col.name in existing:
                    batch.drop_column(col.name)
