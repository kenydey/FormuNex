"""PLM 整合 P0 (companies / ts_templates / batch_exports / formula_mappings).

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-19

整合方案（FormuMind 整合 MSDS-Generator PLM 方案 §3~6）P0 四项的数据模型：
公司配置、TS 模板、批量导出记录、配方数据库映射。TS 文档本身复用
``regulatory_documents``（doc_type="ts"，与 SDS/TDS 同模式：版本化 / Diff /
门禁 / 签批全部沿用既有链路），不另建 technical_standards 表。

``0001_baseline`` builds a fresh database with ``create_all`` from the *current* models, so on a fresh
database the tables already exist; every step checks first and is safe to repeat. The table definitions
below are frozen copies of the models at this revision.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("companies"):
        op.create_table(
            "companies",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("name_en", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("address", sa.String(length=300), nullable=False, server_default=""),
            sa.Column("address_en", sa.String(length=300), nullable=False, server_default=""),
            sa.Column("phone", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("fax", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("email", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("emergency_phone", sa.String(length=64), nullable=False),
            sa.Column("unified_credit_code", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("logo_key", sa.String(length=300), nullable=True),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_by", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_companies_is_default"), "companies", ["is_default"], unique=False)
    if not _has_table("ts_templates"):
        op.create_table(
            "ts_templates",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("category", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("sections_json", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("indicators_json", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_by", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("category", "name", "version", name="uq_ts_template"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_ts_templates_category"), "ts_templates", ["category"], unique=False)
    if not _has_table("batch_exports"):
        op.create_table(
            "batch_exports",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("product_ids", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("doc_types", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("lang", sa.String(length=8), nullable=False, server_default="zh-CN"),
            sa.Column("filename_template", sa.String(length=300), nullable=False, server_default=""),
            sa.Column("include_docx", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("result_key", sa.String(length=300), nullable=True),
            sa.Column("file_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("missing", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("error", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_by", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_batch_exports_status"), "batch_exports", ["status"], unique=False)
    if not _has_table("formula_mappings"):
        op.create_table(
            "formula_mappings",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("company_id", sa.String(length=36), nullable=True),
            sa.Column("product_id", sa.String(length=36), nullable=True),
            sa.Column("formulation_version_id", sa.String(length=36), nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="msds_generator"),
            sa.Column("source_ref", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("source_payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_by", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["formulation_version_id"], ["formulation_versions.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_formula_mappings_lookup"), "formula_mappings",
                        ["product_id", "source", "source_ref"], unique=False)


def downgrade() -> None:
    for table, idx in (("formula_mappings", "ix_formula_mappings_lookup"),
                       ("batch_exports", "ix_batch_exports_status"),
                       ("ts_templates", "ix_ts_templates_category"),
                       ("companies", "ix_companies_is_default")):
        if _has_table(table):
            op.drop_index(op.f(idx), table_name=table)
            op.drop_table(table)
