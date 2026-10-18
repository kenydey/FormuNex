"""Task attachments (lab_task_attachments).

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-18

补齐计划 2-14: 任务单附件（原始记录 / 图谱 / 现场照片），文件落盘
``backend/data/attachments/lab_tasks/<task_id>/``，DB 只存元数据 + 相对路径。

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the tables already exist; every step checks first and is safe to repeat. The table definitions
below are frozen copies of the models at this revision.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("lab_task_attachments"):
        op.create_table(
            "lab_task_attachments",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("task_id", sa.String(length=36), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=128), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("storage_path", sa.String(length=500), nullable=False),
            sa.Column("uploaded_by", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["task_id"], ["lab_tasks.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_lab_task_attachments_task_id"), "lab_task_attachments",
                        ["task_id"], unique=False)


def downgrade() -> None:
    if _has_table("lab_task_attachments"):
        op.drop_index(op.f("ix_lab_task_attachments_task_id"), table_name="lab_task_attachments")
        op.drop_table("lab_task_attachments")
