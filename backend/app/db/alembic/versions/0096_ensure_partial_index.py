"""P3-D3 (2026-11-04 deep review): ensure the 0085 partial unique index
unconditionally.

0092 re-ensured ``uq_approved_release_per_digest`` only inside its
"CHECK missing" branch, so a database where the CHECK already existed
but the index was missing (hand-deleted, or a partial earlier run)
kept the gap forever while the upgrade reported success. 0092 itself
is frozen history and is NOT rewritten; this follow-up migration
ensures the index on every upgrade, guarded by an existence check so
it is a no-op on healthy databases (the normal 0085 -> 0092 path).

Downgrade is intentionally a no-op: the index belongs to 0085, and
removing it here would break 0085's contract.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0096"
down_revision: str | None = "0095"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_RELEASES = "regulatory_data_releases"
_PARTIAL_INDEX = "uq_approved_release_per_digest"
_PARTIAL_DDL = (
    f"CREATE UNIQUE INDEX {_PARTIAL_INDEX} ON {_RELEASES} (input_digest) "
    f"WHERE status = 'approved'"
)


def _has_partial_index(bind) -> bool:
    if _RELEASES not in sa.inspect(bind).get_table_names():
        return True  # nothing to ensure on a DB without the table
    return any(
        ix["name"] == _PARTIAL_INDEX for ix in sa.inspect(bind).get_indexes(_RELEASES)
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_partial_index(bind):
        op.execute(_PARTIAL_DDL)


def downgrade() -> None:
    pass  # index owned by 0085; see module docstring
