"""W1-8 archive state machine (schema half).

``products`` gains ``archived_from_state`` so restore returns the product
to its pre-archive lifecycle state (D9) instead of unconditionally
dropping it back to ``rd``. Nullable: rows archived before this column
existed restore to ``rd`` (legacy fallback, documented in archive.py).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0079"
down_revision: str | None = "0078"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("products")}
    if "archived_from_state" not in cols:
        op.add_column("products", sa.Column("archived_from_state", sa.String(16), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("products")}
    # Stage 2 / SR-038 (0080 pattern): dropping the column loses the
    # restore target for archived products (re-upgrade yields NULL and
    # restore falls back to "rd" instead of the true prior state).
    # Refuse while any product still carries a restore target.
    # Upgrade untouched.
    if "archived_from_state" in cols:
        n = bind.execute(sa.text(
            "SELECT COUNT(*) FROM products WHERE archived_from_state IS NOT NULL "
            "AND archived_from_state != ''")).scalar()
        if n:
            raise RuntimeError(
                f"0079 downgrade refused: {n} product(s) still carry an archived_from_state; "
                "dropping the column would lose their restore target")
        op.drop_column("products", "archived_from_state")
