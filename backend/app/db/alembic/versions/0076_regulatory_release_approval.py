"""Regulatory data release approval state (P1-1).

``latest_data_release`` used to return the newest release by creation time,
so a freshly seeded update took effect immediately — before any EHS review.
Releases now carry a ``status``: ``draft`` (staged, invisible to the gate),
``approved`` (the only state ``latest_data_release`` returns), ``rejected``
and ``superseded``. Existing rows are the shipped, reviewed baseline and are
backfilled to ``approved`` so the gate keeps working on databases that
already ran earlier revisions.

Follows the 0075 convention: every step checks first and is safe to repeat.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, String
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision: str = "0076"
down_revision: str | None = "0075"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_TABLE = "regulatory_data_releases"


def _columns() -> set[str]:
    return {
        c["name"]
        for c in sa_inspect(op.get_bind()).get_columns(_TABLE)
    }


def upgrade() -> None:
    existing = _columns()
    if "status" not in existing:
        op.add_column(
            _TABLE,
            Column("status", String(16), nullable=False, server_default="approved"),
        )
    if "approved_by" not in existing:
        op.add_column(_TABLE, Column("approved_by", String(36), nullable=True))
    if "approved_at" not in existing:
        op.add_column(_TABLE, Column("approved_at", DateTime, nullable=True))
    # Backfill: rows created before this migration are the shipped baseline.
    op.execute(
        f"UPDATE {_TABLE} SET status = 'approved' WHERE status IS NULL OR status = ''"
    )


def downgrade() -> None:
    existing = _columns()
    # Stage 2 / SR-038 (0080 pattern): dropping `status` would silently
    # re-approve draft/rejected rows on the next upgrade — the re-added
    # column's server_default is "approved". Refuse while any row is not
    # approved instead of destroying that distinction. Upgrade untouched.
    if "status" in existing:
        non_approved = op.get_bind().execute(sa.text(
            f"SELECT COUNT(*) FROM {_TABLE} WHERE status IS NOT NULL AND status != 'approved'")).scalar()
        if non_approved:
            raise RuntimeError(
                f"0076 downgrade refused: {non_approved} release row(s) are not 'approved'; "
                "dropping the status column would re-approve them on the next upgrade")
    for col in ("approved_at", "approved_by", "status"):
        if col in existing:
            op.drop_column(_TABLE, col)
