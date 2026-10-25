"""G8 generic product coding: company coding rules + product category snapshot.

Revision ID: 0072
Revises: 0071
Create Date: 2026-10-25

1. ``companies.coding_rules`` (JSON, NOT NULL, default ``{}``):
   ``{"categories": [{"name_zh", "name_en", "identifier"}], "code_digits": int}``.
   ``{}`` = not configured (manual codes only). No backfill meaning: every
   pre-G8 company simply has no rules, which preserves manual-code behaviour.
2. ``products.category_identifier`` (VARCHAR(8), nullable): the category
   identifier a code was auto-generated from; NULL = manually coded or
   pre-G8. No index: the serial scan is a small-table scan, and skipping
   the index avoids the 0071 batch-index trap entirely.
3. ``products.legacy_code`` (VARCHAR(64), nullable): manual reference to a
   legacy (e.g. MYH) code. Never auto-mapped (Cheng 2026-10-25).

On a fresh database the baseline already creates the columns from the
current models, so every step checks first and is safe to repeat.
Downgrade refuses while any product carries a category snapshot or a
legacy code, since that information has nowhere to go below 0072.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_COMPANIES = "companies"
_PRODUCTS = "products"
# See 0067: reflect without resolving foreign keys so partial / legacy schemas can be batch-migrated on SQLite.
_BATCH = {"reflect_kwargs": {"resolve_fks": False}}


def _columns(bind, table: str) -> dict[str, object]:
    if table not in sa.inspect(bind).get_table_names():
        return {}
    return {c["name"]: c for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    company_cols = _columns(bind, _COMPANIES)
    if company_cols and "coding_rules" not in company_cols:
        # NOT NULL + server default: existing rows get {} (no rules),
        # matching the models' non-optional Mapped[dict].
        with op.batch_alter_table(_COMPANIES, **_BATCH) as batch:
            batch.add_column(sa.Column("coding_rules", sa.JSON(), nullable=False, server_default="{}"))
    product_cols = _columns(bind, _PRODUCTS)
    if product_cols:
        with op.batch_alter_table(_PRODUCTS, **_BATCH) as batch:
            if "category_identifier" not in product_cols:
                batch.add_column(sa.Column("category_identifier", sa.String(8), nullable=True))
            if "legacy_code" not in product_cols:
                batch.add_column(sa.Column("legacy_code", sa.String(64), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _PRODUCTS in sa.inspect(bind).get_table_names():
        conflicts = bind.execute(text(
            "SELECT COUNT(*) FROM products WHERE category_identifier IS NOT NULL "
            "OR legacy_code IS NOT NULL")).scalar()
        if conflicts:
            raise RuntimeError(
                "0072 downgrade refused: %d products carry a G8 category snapshot or legacy "
                "code; clear them first" % conflicts)
        product_cols = _columns(bind, _PRODUCTS)
        if product_cols and ("category_identifier" in product_cols or "legacy_code" in product_cols):
            with op.batch_alter_table(_PRODUCTS, **_BATCH) as batch:
                if "category_identifier" in product_cols:
                    batch.drop_column("category_identifier")
                if "legacy_code" in product_cols:
                    batch.drop_column("legacy_code")
    if _COMPANIES in sa.inspect(bind).get_table_names():
        rows = bind.execute(text(
            "SELECT COUNT(*) FROM companies WHERE coding_rules IS NOT NULL "
            "AND coding_rules NOT IN ('{}', '[]')")).scalar()
        if rows:
            raise RuntimeError(
                "0072 downgrade refused: %d companies carry coding rules; clear them first" % rows)
        company_cols = _columns(bind, _COMPANIES)
        if company_cols and "coding_rules" in company_cols:
            with op.batch_alter_table(_COMPANIES, **_BATCH) as batch:
                batch.drop_column("coding_rules")
