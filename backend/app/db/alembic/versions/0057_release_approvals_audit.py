"""Release packages, approval flows, e-signatures and the append-only audit log.

Revision ID: 0057
Revises: 0056
Create Date: 2026-10-16

``audit_log`` is hash-chained by the application and made append-only by a database trigger that
rejects UPDATE and DELETE (SQLite and PostgreSQL). The trigger is installed even when the table came
from the baseline ``create_all``.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the tables already exist; every step checks first and is safe to repeat. The table definitions
below are frozen copies of the models at this revision.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


_SQLITE_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log "
    "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log "
    "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END",
)
_PG_TRIGGER = (
    "CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$ "
    "BEGIN RAISE EXCEPTION 'audit_log is append-only'; END; $$ LANGUAGE plpgsql",
    "DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log",
    "CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log "
    "FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()",
)


def _install_append_only_guard() -> None:
    dialect = op.get_bind().dialect.name
    statements = _SQLITE_TRIGGERS if dialect == "sqlite" else _PG_TRIGGER if dialect == "postgresql" else ()
    for stmt in statements:
        op.execute(stmt)


def _create_tables() -> None:
    if not _has_table('approval_flows'):
        op.create_table('approval_flows',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('subject_type', sa.String(length=32), nullable=False),
        sa.Column('subject_id', sa.String(length=36), nullable=False),
        sa.Column('template', sa.String(length=40), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('current_step', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_approval_flows_status'), 'approval_flows', ['status'], unique=False)
        op.create_index(op.f('ix_approval_flows_subject_id'), 'approval_flows', ['subject_id'], unique=False)
    if not _has_table('audit_log'):
        op.create_table('audit_log',
        sa.Column('seq', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('ts', sa.DateTime(), nullable=False),
        sa.Column('actor', sa.String(length=64), nullable=False),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('entity_type', sa.String(length=32), nullable=False),
        sa.Column('entity_id', sa.String(length=64), nullable=False),
        sa.Column('before_hash', sa.String(length=64), nullable=True),
        sa.Column('after_hash', sa.String(length=64), nullable=True),
        sa.Column('detail', sa.JSON(), nullable=False),
        sa.Column('prev_hash', sa.String(length=64), nullable=False),
        sa.Column('hash', sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint('seq'),
        sa.UniqueConstraint('hash')
        )
        op.create_index(op.f('ix_audit_log_action'), 'audit_log', ['action'], unique=False)
        op.create_index(op.f('ix_audit_log_actor'), 'audit_log', ['actor'], unique=False)
        op.create_index(op.f('ix_audit_log_entity_id'), 'audit_log', ['entity_id'], unique=False)
        op.create_index(op.f('ix_audit_log_entity_type'), 'audit_log', ['entity_type'], unique=False)
        op.create_index(op.f('ix_audit_log_ts'), 'audit_log', ['ts'], unique=False)
    if not _has_table('e_signatures'):
        op.create_table('e_signatures',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('step_id', sa.String(length=36), nullable=True),
        sa.Column('subject_type', sa.String(length=32), nullable=False),
        sa.Column('subject_id', sa.String(length=36), nullable=False),
        sa.Column('meaning', sa.String(length=16), nullable=False),
        sa.Column('signed_hash', sa.String(length=64), nullable=False),
        sa.Column('reauth_method', sa.String(length=16), nullable=False),
        sa.Column('signed_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_e_signatures_step_id'), 'e_signatures', ['step_id'], unique=False)
        op.create_index(op.f('ix_e_signatures_subject_id'), 'e_signatures', ['subject_id'], unique=False)
        op.create_index(op.f('ix_e_signatures_user_id'), 'e_signatures', ['user_id'], unique=False)
    if not _has_table('approval_steps'):
        op.create_table('approval_steps',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('flow_id', sa.String(length=36), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('role_code', sa.String(length=40), nullable=False),
        sa.Column('assignee_id', sa.String(length=36), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('comment', sa.Text(), nullable=False),
        sa.Column('decided_by', sa.String(length=36), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['flow_id'], ['approval_flows.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('flow_id', 'seq', name='uq_approval_step')
        )
        op.create_index(op.f('ix_approval_steps_flow_id'), 'approval_steps', ['flow_id'], unique=False)
    if not _has_table('release_packages'):
        op.create_table('release_packages',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('revision_id', sa.String(length=36), nullable=False),
        sa.Column('gate_result', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('document_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
        sa.Column('package_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_by', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('released_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['revision_id'], ['product_revisions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_release_packages_revision_id'), 'release_packages', ['revision_id'], unique=False)
        op.create_index(op.f('ix_release_packages_status'), 'release_packages', ['status'], unique=False)


def _drop_tables() -> None:
    if _has_table('release_packages'):
        op.drop_table('release_packages')
    if _has_table('approval_steps'):
        op.drop_table('approval_steps')
    if _has_table('e_signatures'):
        op.drop_table('e_signatures')
    if _has_table('audit_log'):
        op.drop_table('audit_log')
    if _has_table('approval_flows'):
        op.drop_table('approval_flows')


def upgrade() -> None:
    _create_tables()
    _install_append_only_guard()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS audit_log_append_only() CASCADE")
    _drop_tables()
