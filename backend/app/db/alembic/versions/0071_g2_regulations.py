"""G2 applicable regulations: company config + per-regulation document versions.

Revision ID: 0071
Revises: 0070
Create Date: 2026-10-25

1. ``companies.applicable_regulations`` (JSON list of regulation codes):
   the company's default selection for new documents. Existing rows are
   backfilled ``["CN"]`` — the pre-G2 behaviour was GB-only, so this
   preserves behaviour exactly instead of silently changing it. New rows
   default to ``[]`` (no default: generation must select explicitly).
2. ``regulatory_documents.regulation`` (VARCHAR(16), NOT NULL, default
   ``"CN"``) plus its model index ``ix_regulatory_documents_regulation``:
   the regulation a document version was generated for. Existing rows are
   backfilled ``"CN"`` (they were all GB-generated).
3. The document unique key gains ``regulation`` (same name
   ``uq_regulatory_document``), so a CN SDS v1 and an EU SDS v1 coexist.
   This only *relaxes* the old key (adds one column) and the backfill
   assigns every pre-0071 row the same value, so no two rows can share
   the new key — the upgrade cannot violate the new constraint.

On a fresh database the baseline already creates the columns, the index
and the new key from the current models, so every step checks first and
is safe to repeat. Downgrade drops the regulation index *before* the
column: SQLite batch table recreation re-creates reflected indexes, so
dropping the column first fails with "no such column: regulation".
Downgrade refuses while any row carries a non-CN regulation (the
old constraint forbids those duplicates) or any company has a non-CN /
non-empty regulation selection.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_DOCS = "regulatory_documents"
_COMPANIES = "companies"
_UQ = "uq_regulatory_document"
_NEW_COLS = ["revision_id", "doc_type", "lang", "batch_id", "regulation", "version_no"]
_OLD_COLS = ["revision_id", "doc_type", "lang", "batch_id", "version_no"]
# See 0067: reflect without resolving foreign keys so partial / legacy schemas can be batch-migrated on SQLite.
_BATCH = {"reflect_kwargs": {"resolve_fks": False}}


def _columns(bind, table: str) -> dict[str, object]:
    if table not in sa.inspect(bind).get_table_names():
        return {}
    return {c["name"]: c for c in sa.inspect(bind).get_columns(table)}


def _unique_columns(bind) -> dict[str, list[str]]:
    return {c["name"]: list(c["column_names"])
            for c in sa.inspect(bind).get_unique_constraints(_DOCS)}


def upgrade() -> None:
    bind = op.get_bind()
    company_cols = _columns(bind, _COMPANIES)
    if company_cols and "applicable_regulations" not in company_cols:
        # NOT NULL + server default: existing rows are backfilled "CN" by
        # the ALTER itself (SQLite fills a new NOT NULL column from its
        # default), matching the models' non-optional Mapped[list].
        with op.batch_alter_table(_COMPANIES, **_BATCH) as batch:
            batch.add_column(sa.Column("applicable_regulations", sa.JSON(), nullable=False,
                                       server_default='["CN"]'))
    doc_cols = _columns(bind, _DOCS)
    if doc_cols and "regulation" not in doc_cols:
        with op.batch_alter_table(_DOCS, **_BATCH) as batch:
            batch.add_column(sa.Column("regulation", sa.String(16), nullable=False, server_default="CN"))
    if doc_cols:
        idx_names = {ix["name"] for ix in sa.inspect(bind).get_indexes(_DOCS)}
        if "ix_regulatory_documents_regulation" not in idx_names and "regulation" in _columns(bind, _DOCS):
            op.create_index("ix_regulatory_documents_regulation", _DOCS, ["regulation"])
    if _DOCS in sa.inspect(bind).get_table_names():
        uqs = _unique_columns(bind)
        if _UQ not in uqs or uqs[_UQ] != _NEW_COLS:
            with op.batch_alter_table(_DOCS, **_BATCH) as batch:
                if _UQ in uqs:
                    batch.drop_constraint(_UQ, type_="unique")
                batch.create_unique_constraint(_UQ, _NEW_COLS)


def downgrade() -> None:
    bind = op.get_bind()
    if _DOCS in sa.inspect(bind).get_table_names():
        conflicts = bind.execute(text(
            "SELECT COUNT(*) FROM regulatory_documents WHERE regulation IS NOT NULL AND regulation != 'CN'")).scalar()
        if conflicts:
            raise RuntimeError(
                "0071 downgrade refused: %d regulatory_documents carry a non-CN regulation "
                "that the old constraint forbids; delete them first" % conflicts)
        uqs = _unique_columns(bind)
        if _UQ in uqs and uqs[_UQ] != _OLD_COLS:
            with op.batch_alter_table(_DOCS, **_BATCH) as batch:
                batch.drop_constraint(_UQ, type_="unique")
                batch.create_unique_constraint(_UQ, _OLD_COLS)
        doc_cols = _columns(bind, _DOCS)
        if doc_cols and "regulation" in doc_cols:
            # Drop the model index first: batch table recreation re-creates
            # reflected indexes, so dropping the column first fails with
            # "no such column: regulation" on databases where the baseline
            # created the index from the models.
            bind.execute(text("DROP INDEX IF EXISTS ix_regulatory_documents_regulation"))
            with op.batch_alter_table(_DOCS, **_BATCH) as batch:
                batch.drop_column("regulation")
    if _COMPANIES in sa.inspect(bind).get_table_names():
        rows = bind.execute(text(
            "SELECT COUNT(*) FROM companies WHERE applicable_regulations IS NOT NULL "
            "AND applicable_regulations NOT IN ('[]', '[\"CN\"]')")).scalar()
        if rows:
            raise RuntimeError(
                "0071 downgrade refused: %d companies carry a non-CN / non-empty regulation "
                "selection; clear it first" % rows)
        company_cols = _columns(bind, _COMPANIES)
        if company_cols and "applicable_regulations" in company_cols:
            with op.batch_alter_table(_COMPANIES, **_BATCH) as batch:
                batch.drop_column("applicable_regulations")
