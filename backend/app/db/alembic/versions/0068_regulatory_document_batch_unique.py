"""Add batch_id to the regulatory-document unique constraint (P1-20).

Revision ID: 0068
Revises: 0067
Create Date: 2026-10-22

COA version numbers increment per batch (``service._next_version`` filters
by batch_id), but the old constraint ``uq_regulatory_document``
(revision_id, doc_type, lang, version_no) did not contain batch_id, so the
second batch's first COA (version 1) collided with the first batch's and
generation failed with IntegrityError. The constraint becomes
(revision_id, doc_type, lang, batch_id, version_no) under the same name.

Historical data: the change only *relaxes* the old key (the new key adds
one column), and the old key has no nullable column, so no two pre-0068
rows can share the new key — the upgrade cannot violate the new
constraint and needs no de-duplication. On a fresh database the baseline
already creates the table from the current models, so the upgrade is a
no-op there; every step checks first and is safe to repeat.

Downgrade note: re-creating the old constraint fails if two rows share
(revision, doc_type, lang, version_no) across batches — the exact rows
0068 legalises. Delete or renumber those per-batch duplicates first; the
downgrade refuses with a clear error instead of surfacing a bare
database constraint failure.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "regulatory_documents"
_UQ = "uq_regulatory_document"
_NEW_COLS = ["revision_id", "doc_type", "lang", "batch_id", "version_no"]
_OLD_COLS = ["revision_id", "doc_type", "lang", "version_no"]


def _has_table(bind) -> bool:
    return _TABLE in sa.inspect(bind).get_table_names()


def _unique_columns(bind) -> dict[str, list[str]]:
    return {c["name"]: list(c["column_names"])
            for c in sa.inspect(bind).get_unique_constraints(_TABLE)}


# See 0067: reflect without resolving foreign keys so partial / legacy schemas can be batch-migrated on SQLite.
_BATCH = {"reflect_kwargs": {"resolve_fks": False}}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    uqs = _unique_columns(bind)
    if _UQ in uqs and uqs[_UQ] == _NEW_COLS:
        return  # fresh database: the baseline already built the new key
    with op.batch_alter_table(_TABLE, **_BATCH) as batch:
        if _UQ in uqs:
            batch.drop_constraint(_UQ, type_="unique")
        batch.create_unique_constraint(_UQ, _NEW_COLS)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    uqs = _unique_columns(bind)
    if _UQ in uqs and uqs[_UQ] == _OLD_COLS:
        return
    conflicts = bind.execute(text(
        "SELECT revision_id, doc_type, lang, version_no, COUNT(*) AS n FROM regulatory_documents "
        "GROUP BY revision_id, doc_type, lang, version_no HAVING COUNT(*) > 1"
    )).fetchall()
    if conflicts:
        raise RuntimeError(
            "0068 downgrade refused: %d (revision, doc_type, lang, version_no) groups have "
            "per-batch duplicates that the old constraint forbids; delete or renumber them first"
            % len(conflicts)
        )
    with op.batch_alter_table(_TABLE, **_BATCH) as batch:
        if _UQ in uqs:
            batch.drop_constraint(_UQ, type_="unique")
        batch.create_unique_constraint(_UQ, _OLD_COLS)
