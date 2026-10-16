"""Release gate & sign-off: refresh-token sessions, gate waivers, delivery packages.

Revision ID: 0060
Revises: 0059
Create Date: 2026-10-16

Plan §4.4 / §5.5.10 (M5): accounts sign in with short-lived access tokens backed by revocable
refresh sessions; release-gate waivers are recorded per item; a released package keeps the key of
its delivery ZIP and the outcome of each publish connector.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the tables already exist; every step checks first and is safe to repeat. The table definitions
below are frozen copies of the models at this revision.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _package_columns() -> list[sa.Column]:
    return [
        sa.Column("delivery_key", sa.String(length=300), nullable=True),
        sa.Column("publish_results", sa.JSON(), nullable=False, server_default="[]"),
    ]


def upgrade() -> None:
    if not _has_table("user_sessions"):
        op.create_table(
            "user_sessions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("user_agent", sa.String(length=300), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("token_hash"),
        )
        op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"], unique=False)
    if not _has_table("release_gate_waivers"):
        op.create_table(
            "release_gate_waivers",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("revision_id", sa.String(length=36), nullable=False),
            sa.Column("check_code", sa.String(length=32), nullable=False),
            sa.Column("item_key", sa.String(length=200), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("waived_by", sa.String(length=36), nullable=False),
            sa.Column("signature_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["revision_id"], ["product_revisions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("revision_id", "check_code", "item_key", name="uq_gate_waiver"),
        )
        op.create_index(op.f("ix_release_gate_waivers_revision_id"), "release_gate_waivers", ["revision_id"],
                        unique=False)
    if _has_table("release_packages"):
        existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("release_packages")}
        for col in _package_columns():
            if col.name not in existing:
                op.add_column("release_packages", col)


def downgrade() -> None:
    if _has_table("release_packages"):
        existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("release_packages")}
        # Plain DROP COLUMN (SQLite >= 3.35): batch mode would reflect and rebuild the table, which fails
        # when tables its foreign keys point at were already removed further down a downgrade.
        for col in reversed(_package_columns()):
            if col.name in existing:
                op.drop_column("release_packages", col.name)
    if _has_table("release_gate_waivers"):
        op.drop_table("release_gate_waivers")
    if _has_table("user_sessions"):
        op.drop_table("user_sessions")
