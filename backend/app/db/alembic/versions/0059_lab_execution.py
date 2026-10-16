"""Built-in lab execution: tasks, samples, methods, instruments, ELN links.

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-16

Plan v1.1 §5.3.1: FormuNex's own database becomes the system of record for experiment execution.
``measurements`` gains sample / task / method / instrument / operator references and a review status;
only reviewed values may feed optimisation, SDS section 9 or a COA.

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the tables already exist; every step checks first and is safe to repeat. The table definitions
below are frozen copies of the models at this revision.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _measurement_columns() -> list[sa.Column]:
    """Fresh Column objects each call (a Column can only be attached to one table)."""
    return [
        sa.Column("sample_id", sa.String(length=36), nullable=True),
        sa.Column("lab_task_id", sa.String(length=36), nullable=True),
        sa.Column("method_id", sa.String(length=36), nullable=True),
        sa.Column("instrument_id", sa.String(length=36), nullable=True),
        sa.Column("operator_id", sa.String(length=36), nullable=True),
        sa.Column("raw_file_key", sa.String(length=300), nullable=True),
        sa.Column("review_status", sa.String(length=16), nullable=False, server_default="unreviewed"),
        sa.Column("reviewed_by", sa.String(length=36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("external_ref", sa.String(length=200), nullable=True),
    ]
_MEASUREMENT_INDEXES = (
    ("ix_measurements_sample_id", "sample_id"),
    ("ix_measurements_lab_task_id", "lab_task_id"),
    ("ix_measurements_review_status", "review_status"),
)


def _create_tables() -> None:
    if not _has_table('eln_links'):
        op.create_table('eln_links',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('connector', sa.String(length=24), nullable=False),
        sa.Column('external_id', sa.String(length=200), nullable=False),
        sa.Column('external_url', sa.String(length=500), nullable=False),
        sa.Column('local_type', sa.String(length=24), nullable=False),
        sa.Column('local_id', sa.String(length=64), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('last_synced_at', sa.DateTime(), nullable=True),
        sa.Column('sync_state', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('connector', 'external_id', 'local_type', name='uq_eln_link')
        )
        op.create_index('ix_eln_links_local', 'eln_links', ['local_type', 'local_id'], unique=False)
    if not _has_table('instruments'):
        op.create_table('instruments',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('asset_no', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('model', sa.String(length=120), nullable=False),
        sa.Column('location', sa.String(length=120), nullable=False),
        sa.Column('calibrated_at', sa.DateTime(), nullable=True),
        sa.Column('calibration_due', sa.DateTime(), nullable=True),
        sa.Column('parser', sa.String(length=40), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_instruments_asset_no'), 'instruments', ['asset_no'], unique=True)
    if not _has_table('lab_tasks'):
        op.create_table('lab_tasks',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('task_no', sa.String(length=40), nullable=False),
        sa.Column('project_id', sa.String(length=64), nullable=True),
        sa.Column('doe_plan_id', sa.String(length=64), nullable=True),
        sa.Column('run_index', sa.Integer(), nullable=True),
        sa.Column('formulation_version_id', sa.String(length=36), nullable=True),
        sa.Column('factors', sa.JSON(), nullable=False),
        sa.Column('requested_metrics', sa.JSON(), nullable=False),
        sa.Column('assignee_id', sa.String(length=36), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('due_at', sa.DateTime(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=False),
        sa.Column('created_by', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_lab_tasks_assignee_id'), 'lab_tasks', ['assignee_id'], unique=False)
        op.create_index(op.f('ix_lab_tasks_doe_plan_id'), 'lab_tasks', ['doe_plan_id'], unique=False)
        op.create_index('ix_lab_tasks_project_status', 'lab_tasks', ['project_id', 'status'], unique=False)
        op.create_index(op.f('ix_lab_tasks_status'), 'lab_tasks', ['status'], unique=False)
        op.create_index(op.f('ix_lab_tasks_task_no'), 'lab_tasks', ['task_no'], unique=True)
    if not _has_table('test_methods'):
        op.create_table('test_methods',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('code', sa.String(length=80), nullable=False),
        sa.Column('name_zh', sa.String(length=200), nullable=False),
        sa.Column('name_en', sa.String(length=200), nullable=False),
        sa.Column('metric', sa.String(length=80), nullable=False),
        sa.Column('unit', sa.String(length=32), nullable=False),
        sa.Column('rounding', sa.JSON(), nullable=False),
        sa.Column('valid_min', sa.Float(), nullable=True),
        sa.Column('valid_max', sa.Float(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_test_methods_code'), 'test_methods', ['code'], unique=True)
        op.create_index(op.f('ix_test_methods_metric'), 'test_methods', ['metric'], unique=False)
    if not _has_table('samples'):
        op.create_table('samples',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('sample_no', sa.String(length=64), nullable=False),
        sa.Column('lab_task_id', sa.String(length=36), nullable=True),
        sa.Column('experiment_id', sa.Integer(), nullable=True),
        sa.Column('batch_id', sa.String(length=36), nullable=True),
        sa.Column('substrate', sa.String(length=80), nullable=False),
        sa.Column('preparation', sa.JSON(), nullable=False),
        sa.Column('prepared_by', sa.String(length=36), nullable=True),
        sa.Column('prepared_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['lab_task_id'], ['lab_tasks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_samples_batch_id'), 'samples', ['batch_id'], unique=False)
        op.create_index(op.f('ix_samples_experiment_id'), 'samples', ['experiment_id'], unique=False)
        op.create_index(op.f('ix_samples_lab_task_id'), 'samples', ['lab_task_id'], unique=False)
        op.create_index(op.f('ix_samples_sample_no'), 'samples', ['sample_no'], unique=True)


def _drop_tables() -> None:
    if _has_table('samples'):
        op.drop_table('samples')
    if _has_table('test_methods'):
        op.drop_table('test_methods')
    if _has_table('lab_tasks'):
        op.drop_table('lab_tasks')
    if _has_table('instruments'):
        op.drop_table('instruments')
    if _has_table('eln_links'):
        op.drop_table('eln_links')


def upgrade() -> None:
    _create_tables()
    if not _has_table("measurements"):
        return
    insp = sa.inspect(op.get_bind())
    existing = {c["name"] for c in insp.get_columns("measurements")}
    for col in _measurement_columns():
        if col.name not in existing:
            op.add_column("measurements", col)
    indexes = {ix["name"] for ix in insp.get_indexes("measurements")}
    for name, col in _MEASUREMENT_INDEXES:
        if name not in indexes:
            op.create_index(name, "measurements", [col])


def downgrade() -> None:
    if _has_table("measurements"):
        insp = sa.inspect(op.get_bind())
        existing = {c["name"] for c in insp.get_columns("measurements")}
        indexes = {ix["name"] for ix in insp.get_indexes("measurements")}
        with op.batch_alter_table("measurements") as batch:
            for name, _col in _MEASUREMENT_INDEXES:
                if name in indexes:
                    batch.drop_index(name)
            for col in reversed(_measurement_columns()):
                if col.name in existing:
                    batch.drop_column(col.name)
    _drop_tables()
