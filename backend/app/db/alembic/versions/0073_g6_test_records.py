"""G6 toxicology / ecotoxicity test records linked to substances.

Revision ID: 0073
Revises: 0072
Create Date: 2026-10-25

Three new tables, all following the SubstanceOELRow pattern (FK to
substances.id with CASCADE, one row per measured fact):

1. ``substance_tox_records``: one measured endpoint per row (LD50/LC50 x
   oral/dermal/inhalation/other), value kept as text as in MSDS (may carry
   ">" or ranges).
2. ``substance_eco_records``: one measured ecotoxicity endpoint per row
   (fish/daphnia/algae x LC50/EC50/ErC50/NOEC/LOEC).
3. ``substance_eco_profiles``: one fate-description row per substance
   (persistence / bioaccumulation / mobility), the non-test part of MSDS
   EcoDataDetailed.

Column nullability mirrors the models exactly (non-optional Mapped fields
are NOT NULL); the drift test compares migrated schema against models.
Downgrade drops all three tables.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0073"
down_revision: str | None = "0072"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _dt():
    return sa.DateTime()


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "substance_tox_records" not in tables:
        op.create_table(
            "substance_tox_records",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("substance_id", sa.String(36),
                      sa.ForeignKey("substances.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("endpoint", sa.String(16), nullable=False),
            sa.Column("route", sa.String(16), nullable=False),
            sa.Column("value", sa.String(64), nullable=False),
            sa.Column("unit", sa.String(16), nullable=False),
            sa.Column("species", sa.String(64), nullable=False),
            sa.Column("method", sa.String(200), nullable=False),
            sa.Column("source", sa.String(16), nullable=False),
            sa.Column("source_ref", sa.String(300), nullable=False),
            sa.Column("created_at", _dt(), nullable=False),
            sa.Column("updated_at", _dt(), nullable=False),
            sa.UniqueConstraint("substance_id", "endpoint", "route", "species", "source",
                                name="uq_substance_tox"),
        )
    if "substance_eco_records" not in tables:
        op.create_table(
            "substance_eco_records",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("substance_id", sa.String(36),
                      sa.ForeignKey("substances.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("organism_group", sa.String(16), nullable=False),
            sa.Column("endpoint", sa.String(16), nullable=False),
            sa.Column("value", sa.String(64), nullable=False),
            sa.Column("unit", sa.String(16), nullable=False),
            sa.Column("duration", sa.String(32), nullable=False),
            sa.Column("species", sa.String(64), nullable=False),
            sa.Column("method", sa.String(200), nullable=False),
            sa.Column("source", sa.String(16), nullable=False),
            sa.Column("source_ref", sa.String(300), nullable=False),
            sa.Column("created_at", _dt(), nullable=False),
            sa.Column("updated_at", _dt(), nullable=False),
            sa.UniqueConstraint("substance_id", "organism_group", "endpoint", "species", "source",
                                name="uq_substance_eco"),
        )
    if "substance_eco_profiles" not in tables:
        op.create_table(
            "substance_eco_profiles",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("substance_id", sa.String(36),
                      sa.ForeignKey("substances.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("persistence", sa.String(300), nullable=False),
            sa.Column("bioaccum", sa.String(300), nullable=False),
            sa.Column("mobility", sa.String(300), nullable=False),
            sa.Column("created_at", _dt(), nullable=False),
            sa.Column("updated_at", _dt(), nullable=False),
            sa.UniqueConstraint("substance_id", name="uq_substance_eco_profile"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    for table in ("substance_eco_profiles", "substance_eco_records", "substance_tox_records"):
        if table in tables:
            op.drop_table(table)
