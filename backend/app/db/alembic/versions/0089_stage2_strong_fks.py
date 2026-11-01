"""Stage 2 / SR-032/033: strong-reference foreign keys + ondelete unification.

Five columns gain a real FK (SET NULL — deleting a parent must not
delete results, documents or lab work that cite it), and
``material_compositions.substance_id`` gains the CASCADE its sibling
composition FKs already declare (deleting a substance deletes its
composition rows, instead of erroring on PG while other tables cascade).

* regulatory_documents.batch_id -> batches.id (COA only, nullable)
* batch_qc_results.measurement_id -> measurements.id (nullable)
* lab_tasks.doe_plan_id -> doe_plans.id (nullable)
* measurements.sample_id -> samples.id (nullable)
* material_compositions.substance_id: FK ondelete NO ACTION -> CASCADE

NOT here: measurements.lab_task_id already has an FK (migration 0059
created it with SET NULL; the model just never declared it). Adding a
second one would duplicate the constraint, so 0089 only fixes the model
declaration for that column.

Dirty-data gate (0081 pattern): each upgrade counts orphan rows
(non-NULL values with no parent) and refuses while any exist — it never
deletes or rewrites data. The development audit found zero orphans on
all five. Every step checks first and is safe to repeat. Downgrade drops
the four new FKs; for material_compositions it restores a plain
(no-ondelete) FK under a new name — the original was unnamed, and
alembic cannot recreate an unnamed constraint, but the behavior is
identical.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0089"
down_revision: str | None = "0088"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

# Batch rebuilds use DEFAULT reflection here, deliberately unlike
# 0081's resolve_fks=False: these tables carry pre-existing unnamed FKs
# (measurements alone has three), and the SQL-parse fallback that
# resolve_fks=False relies on demonstrably drops or duplicates them
# (a fresh 0089 run lost measurements.experiment_id once and duplicated
# lab_task_id once). Default reflection preserves every existing FK.
_BATCH = {}

# (table, column, parent table, fk name, ondelete)
_NEW_FKS = (
    ("regulatory_documents", "batch_id", "batches", "fk_regulatory_documents_batch_id", "SET NULL"),
    ("batch_qc_results", "measurement_id", "measurements", "fk_batch_qc_results_measurement_id", "SET NULL"),
    ("lab_tasks", "doe_plan_id", "doe_plans", "fk_lab_tasks_doe_plan_id", "SET NULL"),
    ("measurements", "sample_id", "samples", "fk_measurements_sample_id", "SET NULL"),
    # lab_task_id's FK is NOT from 0059 (that migration only added the
    # column; the lab_task_id FK it carries is on samples). The model
    # declares it, so 0089 creates it on upgrade-path databases; on fresh
    # databases the model FK already matches and this is skipped.
    ("measurements", "lab_task_id", "lab_tasks", "fk_measurements_lab_task_id", "SET NULL"),
)
_MC_TABLE = "material_compositions"
_MC_FK = "fk_material_compositions_substance_cascade"
_MC_PLAIN_FK = "fk_material_compositions_substance_plain"


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _has_fk(bind, table: str, fk_name: str) -> bool:
    if table not in _tables(bind):
        return False
    return any(fk["name"] == fk_name for fk in sa.inspect(bind).get_foreign_keys(table))


def _has_matching_fk(bind, table: str, column: str, parent: str, ondelete: str | None) -> bool:
    """True when an FK on the same column, parent and ondelete already
    exists — named or not. The 0001 baseline builds fresh databases from
    the current models, so a fresh DB already carries these FKs
    (unnamed); creating a named second one would only duplicate the
    constraint. Legacy databases lack them and get the named FK."""
    if table not in _tables(bind):
        return False
    for fk in sa.inspect(bind).get_foreign_keys(table):
        if (fk["constrained_columns"] == [column] and fk["referred_table"] == parent
                and (fk["options"] or {}).get("ondelete") == ondelete):
            return True
    return False


def _orphans(bind, table: str, column: str, parent: str) -> int:
    if table not in _tables(bind) or parent not in _tables(bind):
        return 0
    return bind.execute(text(
        f"SELECT COUNT(*) FROM {table} c LEFT JOIN {parent} p ON p.id = c.{column} "
        f"WHERE c.{column} IS NOT NULL AND p.id IS NULL")).scalar() or 0


def upgrade() -> None:
    bind = op.get_bind()
    for table, column, parent, fk_name, ondelete in _NEW_FKS:
        if _has_matching_fk(bind, table, column, parent, ondelete):
            continue
        dirty = _orphans(bind, table, column, parent)
        if dirty:
            raise RuntimeError(
                f"0089 upgrade refused: {dirty} {table}.{column} row(s) dangle "
                f"(no {parent} parent); clean them up first, never auto-delete")
        if table not in _tables(bind) or parent not in _tables(bind):
            continue
        with op.batch_alter_table(table, **_BATCH) as batch:
            batch.create_foreign_key(fk_name, parent, [column], ["id"], ondelete=ondelete)
    # material_compositions.substance_id: replace the no-ondelete FK with
    # CASCADE. Batch mode replaces the FK on the same columns (verified
    # on SQLite: the old unnamed FK is superseded, sibling FKs survive).
    if not _has_matching_fk(bind, _MC_TABLE, "substance_id", "substances", "CASCADE"):
        dirty = _orphans(bind, _MC_TABLE, "substance_id", "substances")
        if dirty:
            raise RuntimeError(
                f"0089 upgrade refused: {dirty} material_compositions.substance_id row(s) "
                "dangle (no substances parent); clean them up first, never auto-delete")
        if _MC_TABLE in _tables(bind) and "substances" in _tables(bind):
            with op.batch_alter_table(_MC_TABLE, **_BATCH) as batch:
                batch.create_foreign_key(_MC_FK, "substances", ["substance_id"], ["id"],
                                         ondelete="CASCADE")


def downgrade() -> None:
    # Only the named FKs created by this migration are dropped. On a
    # fresh database the upgrade skipped creation (the baseline already
    # carries the model-declared, unnamed FKs), so there is nothing to
    # drop there — the model FKs stay, which is the baseline state.
    bind = op.get_bind()
    for table, _column, _parent, fk_name, _ondelete in _NEW_FKS:
        if _has_fk(bind, table, fk_name):
            with op.batch_alter_table(table, **_BATCH) as batch:
                batch.drop_constraint(fk_name, type_="foreignkey")
    if _has_fk(bind, _MC_TABLE, _MC_FK):
        with op.batch_alter_table(_MC_TABLE, **_BATCH) as batch:
            batch.drop_constraint(_MC_FK, type_="foreignkey")
            batch.create_foreign_key(_MC_PLAIN_FK, "substances", ["substance_id"], ["id"])
