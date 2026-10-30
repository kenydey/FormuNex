"""Tighten ``doe_plans`` / ``lab_tasks`` project scope to NOT NULL (P1-2).

Cheng confirmed the treatment on 2026-10-31: orphan rows are deleted or
manually attributed, and the write paths now refuse project-less rows
(``doe_plan_store.save`` raises, ``tasks.create_task`` raises LabError),
so the schema can finally enforce what the stores already guarantee.

Upgrade order (atomic per database transaction where the backend
supports it):
1. Backfill what is attributable, reusing the 0033 rules verbatim
   (doe_plans <- campaigns, then experiments; lab_tasks <- doe_plans).
2. Guard: if NULL rows remain, fail with their counts — the operator
   deletes or attributes them and re-runs. The migration never invents
   a project and never deletes data on its own.
3. Alter both columns to NOT NULL (batch mode, so SQLite rebuilds the
   tables instead of failing on ALTER limitations).

Only these two tables are tightened. ``wiki_pages`` stays nullable on
purpose: its NULL rows are the global materials library, not orphans.
``chat_sessions`` / ``chat_messages`` stay nullable: global chat is a
legitimate scope. ``campaigns`` stays nullable: it is top-level.

Follows the 0075 convention: every step checks first and is safe to repeat.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from alembic import op

revision: str = "0077"
down_revision: str | None = "0076"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

# (table, column type) — batch mode needs existing_type to rebuild on SQLite.
_TABLES: tuple[tuple[str, sa.String], ...] = (
    ("doe_plans", sa.String(36)),
    ("lab_tasks", sa.String(64)),
)


def _nullable(table: str) -> bool | None:
    for col in sa_inspect(op.get_bind()).get_columns(table):
        if col["name"] == "project_id":
            return bool(col["nullable"])
    return None


def _null_count(table: str) -> int:
    row = op.get_bind().execute(text(f"SELECT COUNT(*) FROM {table} WHERE project_id IS NULL")).first()
    return int(row[0]) if row else 0


def _backfill() -> None:
    bind = op.get_bind()
    # doe_plans <- campaigns, then experiments (0033 rules: parent project
    # must be non-NULL and non-empty; empty string is the unscoped sentinel).
    for parent, key in (("campaigns", "id"), ("experiments", "id")):
        fk = "campaign_id" if parent == "campaigns" else "experiment_id"
        bind.execute(
            text(
                f"UPDATE doe_plans SET project_id = ("
                f"SELECT project_id FROM {parent} "
                f"WHERE {parent}.{key} = doe_plans.{fk}) "
                f"WHERE project_id IS NULL AND {fk} IS NOT NULL "
                f"AND (SELECT project_id FROM {parent} "
                f"WHERE {parent}.{key} = doe_plans.{fk}) IS NOT NULL "
                f"AND (SELECT project_id FROM {parent} "
                f"WHERE {parent}.{key} = doe_plans.{fk}) != ''"
            )
        )
    # lab_tasks <- doe_plans (runs after doe_plans so chained attribution works).
    bind.execute(
        text(
            "UPDATE lab_tasks SET project_id = ("
            "SELECT project_id FROM doe_plans "
            "WHERE doe_plans.id = lab_tasks.doe_plan_id) "
            "WHERE project_id IS NULL AND doe_plan_id IS NOT NULL "
            "AND (SELECT project_id FROM doe_plans "
            "WHERE doe_plans.id = lab_tasks.doe_plan_id) IS NOT NULL "
            "AND (SELECT project_id FROM doe_plans "
            "WHERE doe_plans.id = lab_tasks.doe_plan_id) != ''"
        )
    )


def upgrade() -> None:
    _backfill()
    remaining = {t: _null_count(t) for t, _ in _TABLES}
    stuck = {t: n for t, n in remaining.items() if n > 0}
    if stuck:
        raise RuntimeError(
            "0077 refuses to tighten: project-less rows remain "
            f"(delete them or attribute them, then re-run): {stuck}"
        )
    for table, col_type in _TABLES:
        if _nullable(table) is False:
            continue
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "project_id", nullable=False,
                existing_type=col_type, existing_nullable=True,
            )


def downgrade() -> None:
    for table, col_type in _TABLES:
        if _nullable(table) is True:
            continue
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                "project_id", nullable=True,
                existing_type=col_type, existing_nullable=False,
            )
