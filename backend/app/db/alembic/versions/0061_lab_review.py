"""Lab execution review trail: measurement amendments, manual value picks, ELN sync conflicts.

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-16

Plan v1.1 §5.3.1 (M7): amendments keep the original value and the reason; repeated measurements are all
kept and one may be picked by hand; records changed on both sides of an ELN sync wait in a queue.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the tables already exist; every step checks first and is safe to repeat. The table definitions
below are frozen copies of the models at this revision.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("measurement_amendments"):
        op.create_table(
            "measurement_amendments",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("measurement_id", sa.String(length=36), nullable=False),
            sa.Column("field", sa.String(length=32), nullable=False),
            sa.Column("old_value", sa.String(length=200), nullable=False),
            sa.Column("new_value", sa.String(length=200), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("amended_by", sa.String(length=36), nullable=False),
            sa.Column("amended_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["measurement_id"], ["measurements.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_measurement_amendments_measurement_id"), "measurement_amendments",
                        ["measurement_id"], unique=False)
    if not _has_table("measurement_picks"):
        op.create_table(
            "measurement_picks",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("experiment_id", sa.Integer(), nullable=False),
            sa.Column("metric", sa.String(length=80), nullable=False),
            sa.Column("measurement_id", sa.String(length=36), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("picked_by", sa.String(length=36), nullable=False),
            sa.Column("picked_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["measurement_id"], ["measurements.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("experiment_id", "metric", name="uq_measurement_pick"),
        )
        op.create_index(op.f("ix_measurement_picks_experiment_id"), "measurement_picks", ["experiment_id"],
                        unique=False)
    if not _has_table("eln_sync_conflicts"):
        op.create_table(
            "eln_sync_conflicts",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("link_id", sa.String(length=36), nullable=False),
            sa.Column("local_snapshot", sa.JSON(), nullable=False),
            sa.Column("remote_snapshot", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("resolution", sa.String(length=16), nullable=False),
            sa.Column("resolved_by", sa.String(length=36), nullable=True),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["link_id"], ["eln_links.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_eln_sync_conflicts_link_id"), "eln_sync_conflicts", ["link_id"], unique=False)
        op.create_index(op.f("ix_eln_sync_conflicts_status"), "eln_sync_conflicts", ["status"], unique=False)


def downgrade() -> None:
    for table in ("eln_sync_conflicts", "measurement_picks", "measurement_amendments"):
        if _has_table(table):
            op.drop_table(table)
