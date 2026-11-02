"""Stage 5 / SR-079 + SR-080: release status vocabulary.

- ``regulatory_data_releases.status`` gains a CHECK over the closed
  vocabulary (draft / approved / rejected / superseded / import), the
  same constraint the ORM model carries for new databases.

Dirty-data guard (same rule as 0081/0085): a row outside the status
vocabulary makes the migration REFUSE with counts and deletes nothing.

SR-080④ (classification composite unique key) was REMOVED from this
migration by decision (2026-11-02): classification rows are
per-computation records — persist() inserts on every classify and
recompute deliberately stacks new rows while old rows stay untouched
— so a (revision_id, inputs_hash) unique key breaks the write path.
Stacking is the accepted design; the dev DB's 3x3 duplicate rows are
normal data under that semantics, not dirt to clean.

SQLite note: ADD CONSTRAINT is not supported, so the CHECK goes through
a batch rebuild. The rebuild re-creates the table's indexes from
reflection; the 0085 partial unique index has a WHERE clause that
reflection may drop, so upgrade() re-ensures it afterwards (and
downgrade() does the same — 0092's own undo must not take 0085's
index down with it).

Known accepted limitations, stage 5 / SR-080 (NOT fixed here, on
purpose — the migrations below are frozen history and must not be
rewritten):
- 0077's backfill scans each row three times (slow on large libraries,
  correct results); accepted as-is.
- 0078's duplicate guard message counts duplicate GROUPS, not rows.
- 0081's SET NULL FKs leave no audit row when a revision is deleted,
  and no revision-delete entry point was found to attach one to.
  If such an entry point appears, it must write the audit row with it.
- 080④ classification stacking: same (revision_id, inputs_hash) rows
  legitimately coexist (one per computation); no unique key, by decision.

Check-first, safe to repeat. Downgrade drops the CHECK constraint.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0092"
down_revision: str | None = "0091"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_RELEASES = "regulatory_data_releases"
_CHECK = "ck_release_status"
_PARTIAL_INDEX = "uq_approved_release_per_digest"
_PARTIAL_DDL = (
    f"CREATE UNIQUE INDEX {_PARTIAL_INDEX} ON {_RELEASES} (input_digest) "
    f"WHERE status = 'approved'"
)
_STATUS_VOCABULARY = ("draft", "approved", "rejected", "superseded", "import")


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _bad_status_rows(bind):
    if _RELEASES not in _tables(bind):
        return []
    placeholders = ", ".join(f"'{s}'" for s in _STATUS_VOCABULARY)
    return bind.execute(sa.text(
        f"SELECT status, COUNT(*) AS n FROM {_RELEASES} "
        f"WHERE status NOT IN ({placeholders}) GROUP BY status"
    )).all()


def _has_check(bind) -> bool:
    if _RELEASES not in _tables(bind):
        return False
    return any(
        c["name"] == _CHECK
        for c in sa.inspect(bind).get_check_constraints(_RELEASES)
    )


def _has_partial_index(bind) -> bool:
    if _RELEASES not in _tables(bind):
        return False
    return any(
        ix["name"] == _PARTIAL_INDEX
        for ix in sa.inspect(bind).get_indexes(_RELEASES)
    )


def _ensure_partial_index(bind) -> None:
    if _RELEASES in _tables(bind) and not _has_partial_index(bind):
        op.execute(_PARTIAL_DDL)


def upgrade() -> None:
    bind = op.get_bind()
    bad = _bad_status_rows(bind)
    if bad:
        detail = ", ".join(f"{s or '(null)'} x{n}" for s, n in bad)
        raise RuntimeError(
            "0092 refused: regulatory_data_releases rows outside the status "
            f"vocabulary already exist ({detail}). Decide their cleanup "
            "first; nothing was deleted."
        )
    if _RELEASES in _tables(bind) and not _has_check(bind):
        with op.batch_alter_table(_RELEASES) as batch_op:
            batch_op.create_check_constraint(
                _CHECK,
                "status IN ('draft', 'approved', 'rejected', 'superseded', 'import')",
            )
        # Batch rebuild may drop the 0085 partial index; re-ensure it.
        _ensure_partial_index(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if _RELEASES in _tables(bind) and _has_check(bind):
        with op.batch_alter_table(_RELEASES) as batch_op:
            batch_op.drop_constraint(_CHECK, type_="check")
        _ensure_partial_index(bind)
