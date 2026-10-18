"""Phase 2D: MCP approval tables move into the main database + legacy provenance store retired.

Revision ID: 0062
Revises: 0061
Create Date: 2026-10-18

1. ``mcp_tool_policies`` / ``mcp_approval_requests`` (2-24): the MCP approval
   store lived in a standalone SQLite file (``mcp_approval.db``) with its own
   ``sqlite3`` code. On PostgreSQL production that left a stray SQLite file
   beside the main database. The tables now live in the main database; the
   old file is ignored (approval state is ephemeral — pending requests expire
   in 5 minutes, policies can be re-set — so no data migration).

2. Legacy ``provenance_edges`` / ``provenance_meta`` (2-22): the old
   plain-SQLite provenance store is retired in favour of the unified ORM
   ``lineage_edges`` table. Any edges still in the legacy table are copied
   over (``created_at`` ISO text → datetime, ids prefixed ``legacy-``) and
   the legacy tables are dropped, so exactly one edge store remains. On
   PostgreSQL the legacy tables never existed (provenance was silently
   fail-open there), so the copy is a no-op.

``0001_baseline`` builds a fresh database with ``create_all`` from the
*current* models, so on a fresh database the tables already exist; every step
checks first and is safe to repeat.
"""
from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _copy_legacy_provenance_edges() -> None:
    """Copy rows from the retired ``provenance_edges`` table into ``lineage_edges``, then drop it."""
    if not _has_table("provenance_edges") or not _has_table("lineage_edges"):
        return
    conn = op.get_bind()
    legacy = sa.table(
        "provenance_edges",
        sa.column("id"), sa.column("from_type"), sa.column("from_id"),
        sa.column("to_type"), sa.column("to_id"), sa.column("relation"), sa.column("created_at"),
    )
    target = sa.table(
        "lineage_edges",
        sa.column("id"), sa.column("from_type"), sa.column("from_id"),
        sa.column("to_type"), sa.column("to_id"), sa.column("relation"),
        sa.column("created_by"), sa.column("created_at"),
    )
    existing = {
        (r[0], r[1], r[2], r[3], r[4])
        for r in conn.execute(sa.select(
            target.c.from_type, target.c.from_id, target.c.to_type, target.c.to_id, target.c.relation))
    }
    rows = conn.execute(sa.select(legacy)).fetchall()
    for row in rows:
        key = (row.from_type, row.from_id, row.to_type, row.to_id, row.relation or "derived_from")
        if key in existing:
            continue
        try:
            created = datetime.fromisoformat(str(row.created_at)) if row.created_at else None
        except ValueError:
            created = None
        if created is not None and created.tzinfo is not None:
            created = created.astimezone(timezone.utc).replace(tzinfo=None)
        conn.execute(target.insert().values(
            id=f"legacy-{row.id}", from_type=key[0], from_id=key[1], to_type=key[2],
            to_id=key[3], relation=key[4], created_by=None,
            created_at=created or datetime.now(timezone.utc).replace(tzinfo=None),
        ))
        existing.add(key)
    op.drop_table("provenance_edges")
    if _has_table("provenance_meta"):
        op.drop_table("provenance_meta")


def upgrade() -> None:
    if not _has_table("mcp_tool_policies"):
        op.create_table(
            "mcp_tool_policies",
            sa.Column("server_id", sa.String(length=128), nullable=False),
            sa.Column("tool_name", sa.String(length=128), nullable=False),
            sa.Column("scope", sa.String(length=16), nullable=False),
            sa.Column("decision", sa.String(length=16), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("server_id", "tool_name", "scope"),
        )
    if not _has_table("mcp_approval_requests"):
        op.create_table(
            "mcp_approval_requests",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("server_id", sa.String(length=128), nullable=False),
            sa.Column("tool_name", sa.String(length=128), nullable=False),
            sa.Column("session_id", sa.String(length=128), nullable=True),
            sa.Column("project_id", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("requested_at", sa.Float(), nullable=False),
            sa.Column("decided_at", sa.Float(), nullable=True),
            sa.Column("arguments_json", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_mcp_approval_requests_server_id"), "mcp_approval_requests",
                        ["server_id"], unique=False)
        op.create_index(op.f("ix_mcp_approval_requests_tool_name"), "mcp_approval_requests",
                        ["tool_name"], unique=False)
        op.create_index(op.f("ix_mcp_approval_requests_status"), "mcp_approval_requests",
                        ["status"], unique=False)
    _copy_legacy_provenance_edges()


def downgrade() -> None:
    for table in ("mcp_approval_requests", "mcp_tool_policies"):
        if _has_table(table):
            op.drop_table(table)
    # The legacy provenance tables are not recreated: their content was copied into lineage_edges.
