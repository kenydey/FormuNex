"""Stage 3 / SR-065 + SR-077: indexes for the dashboard and list hot paths.

Verified with EXPLAIN QUERY PLAN on a copy of the dev DB before writing
this migration — every index below is actually used by the planner:

- experiments(created_at): the org-dashboard latest-50 window
  (ORDER BY created_at DESC LIMIT 50) previously sorted via temp b-tree.
- experiments(project_id, created_at): project-scoped recent-50 lists
  (dossier_pack: WHERE project_id = ? ORDER BY created_at DESC).
- projects(is_archived, updated_at): list_summaries
  (WHERE is_archived = 0 ORDER BY updated_at DESC).

Deliberately NOT included: experiments(label). The campaign-rounds
ledger scan filters ``label LIKE 'wb:{id}:%'``; SQLite's LIKE
optimization does not apply to case-insensitive LIKE on a BINARY
column, and EXPLAIN confirms the planner still full-scans with the
index present — it would only cost writes. That scan is scoped to a
single campaign's rows, not a global list.

Check-first, safe to repeat. Downgrade drops the three indexes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0091"
down_revision: str | None = "0090"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("experiments", "ix_experiments_created_at", ["created_at"]),
    ("experiments", "ix_experiments_project_created", ["project_id", "created_at"]),
    ("projects", "ix_projects_archived_updated", ["is_archived", "updated_at"]),
)


def _existing(table: str) -> set[str]:
    bind = op.get_bind()
    if table not in sa.inspect(bind).get_table_names():
        return set()
    return {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def _columns(table: str) -> set[str] | None:
    bind = op.get_bind()
    if table not in sa.inspect(bind).get_table_names():
        return None
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    # A missing table — or a table missing an indexed column — means
    # "nothing to index", not "index missing": hand-built legacy schemas
    # (the 0080 batches-only fixture; the 0077 fixture whose experiments
    # table predates created_at) have no such column, and CREATE INDEX on
    # it fails. The first version of this migration read the empty index
    # set as "create it" and broke every legacy-schema upgrade-to-head
    # test; the column check is the second half of that fix. On any real
    # DB at head 0090 all three columns exist, so nothing is skipped
    # there.
    for table, name, cols in _INDEXES:
        existing_cols = _columns(table)
        if existing_cols is not None and set(cols) <= existing_cols and name not in _existing(table):
            op.create_index(name, table, cols)


def downgrade() -> None:
    for table, name, _cols in _INDEXES:
        if name in _existing(table):
            op.drop_index(name, table_name=table)
