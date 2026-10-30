"""Project-attribution backfill for legacy NULL ``project_id`` rows (P1-2).

Extends migration 0033's attribution rules to the other tables that have a
well-defined parent link. The rules are deliberately conservative, mirroring
0033 exactly:

- Only rows with ``project_id IS NULL`` are touched (idempotent re-runs
  are no-ops), and only when the parent resolves to exactly one non-empty
  project (empty-string parents are the "unscoped" sentinel used by
  ``experiments`` / ``doe_cycle_runs`` and never count as attribution).
- Rows that cannot be attributed keep NULL and are reported as the
  unattributable list — they are never silently defaulted to a project.
  That list is the P1-2 audit artefact: the tightening migration (nullable
  -> NOT NULL) must wait until it is empty or Cheng has confirmed the
  remainder, because several tables have *legitimate* NULL semantics
  (global corpus, global wiki pages, company-level PLM imports, global
  chat) that no backfill may erase.

Tables NOT backfilled here, and why (do not "fix" these by backfilling):
- ``source_documents`` / ``wiki_pages`` global rows: NULL = shared corpus
  by design (project_store documents this). Wiki rows whose sources all
  sit in one project ARE attributed (0033 rule, kept here).
- ``formulation_versions``: PLM imports create versions with
  ``project_id=None`` on purpose (company-level, not project-level);
  attributing them to a project would mis-scope company data.
- ``recommend_outcomes`` / ``acquisition_snapshots`` / ``kb_ingest_audit``:
  no parent link to resolve (standalone keys / audit log).
- ``campaigns`` / ``chat_sessions``: top-level, no parent to resolve.
- ``products`` (PLM): company-level by design.
- ``task_outbox.owner_id`` / ``experiments.owner_id`` /
  ``campaigns.owner_id``: owner is not read for access control anywhere
  today, so tightening it would be cosmetic, not isolation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlalchemy as sa

logger = logging.getLogger(__name__)

# A parent project counts as attribution only when non-NULL and non-empty.
_PARENT_OK = "IS NOT NULL AND {col} != ''"

# (table, parent table, parent key, child FK, parent project column)
# Order matters: doe_plans first, lab_tasks resolves through it.
_SQL_RULES: tuple[tuple[str, str, str, str], ...] = (
    # doe_plans <- campaigns (0033 rule 1)
    ("doe_plans", "campaigns", "id", "campaign_id"),
    # doe_plans <- experiments (0033 rule 2, only still-NULL rows)
    ("doe_plans", "experiments", "id", "experiment_id"),
    # lab_tasks <- doe_plans
    ("lab_tasks", "doe_plans", "id", "doe_plan_id"),
    # chat_messages <- chat_sessions
    ("chat_messages", "chat_sessions", "id", "session_id"),
    # mcp_approval_requests <- chat_sessions
    ("mcp_approval_requests", "chat_sessions", "id", "session_id"),
)

# Max unattributable ids carried inline in a report (the full list is
# always in the JSON report file / dry-run output when requested).
_INLINE_IDS_LIMIT = 200


def _tables(conn: sa.Connection) -> set[str]:
    return set(sa.inspect(conn).get_table_names())


def _columns(conn: sa.Connection, table: str) -> set[str]:
    if table not in _tables(conn):
        return set()
    return {c["name"] for c in sa.inspect(conn).get_columns(table)}


def _sql_fill(conn: sa.Connection, table: str, parent: str, parent_key: str, fk: str) -> int:
    """UPDATE NULL rows from one parent link. Returns rows filled."""
    if table not in _tables(conn) or parent not in _tables(conn):
        return 0
    cols = _columns(conn, table)
    pcols = _columns(conn, parent)
    if "project_id" not in cols or fk not in cols or "project_id" not in pcols or parent_key not in pcols:
        return 0
    result = conn.execute(
        sa.text(
            f"UPDATE {table} SET project_id = ("
            f"SELECT project_id FROM {parent} "
            f"WHERE {parent}.{parent_key} = {table}.{fk}) "
            f"WHERE project_id IS NULL AND {fk} IS NOT NULL "
            f"AND (SELECT project_id FROM {parent} "
            f"WHERE {parent}.{parent_key} = {table}.{fk}) IS NOT NULL "
            f"AND (SELECT project_id FROM {parent} "
            f"WHERE {parent}.{parent_key} = {table}.{fk}) != ''"
        )
    )
    return result.rowcount or 0


def _wiki_attribution(conn: sa.Connection) -> dict[str, str]:
    """Map wiki page id -> project for NULL pages whose sources agree.

    0033 rule, verbatim: exactly one distinct non-empty project across
    the page's source_ids (multi-project or unknown sources -> unattributed).
    """
    if "wiki_pages" not in _tables(conn) or "source_documents" not in _tables(conn):
        return {}
    cols = _columns(conn, "wiki_pages")
    if "project_id" not in cols or "source_ids" not in cols:
        return {}
    src_rows = conn.execute(sa.text("SELECT id, project_id FROM source_documents")).all()
    src_project = {str(r[0]): str(r[1]) for r in src_rows if r[1] and str(r[1]).strip()}
    out: dict[str, str] = {}
    if not src_project:
        return out
    for wid, sids_raw in conn.execute(
        sa.text("SELECT id, source_ids FROM wiki_pages WHERE project_id IS NULL")
    ).all():
        # Raw SQL returns the stored JSON text on SQLite but a decoded
        # list on Postgres jsonb — accept both (0033 assumed text only).
        try:
            sids = json.loads(sids_raw) if isinstance(sids_raw, str) else (sids_raw or [])
        except Exception:
            continue
        if not isinstance(sids, list):
            continue
        pids = {src_project[s] for s in sids if s in src_project}
        if len(pids) == 1:
            out[str(wid)] = next(iter(pids))
    return out


def _null_ids(conn: sa.Connection, table: str) -> list[str]:
    if table not in _tables(conn) or "project_id" not in _columns(conn, table):
        return []
    return [str(r[0]) for r in conn.execute(
        sa.text(f"SELECT id FROM {table} WHERE project_id IS NULL")
    ).all()]


def audit(conn: sa.Connection, *, tables: list[str] | None = None) -> dict[str, Any]:
    """Dry-run report: NULL counts, attributable counts, unattributable ids.

    Nothing is written. ``tables`` limits the report to those tables.
    """
    wanted = set(tables) if tables else None
    report: dict[str, Any] = {}
    watched = {t for t, _, _, _ in _SQL_RULES}
    if wanted is None or "wiki_pages" in wanted:
        watched.add("wiki_pages")
    for table in sorted(watched):
        if wanted is not None and table not in wanted:
            continue
        null_before = len(_null_ids(conn, table))
        ids = _unattributable_ids(conn, table)
        report[table] = {
            "null_rows": null_before,
            # Multi-rule tables (doe_plans: campaign, then experiment)
            # fill sequentially, so attributable is the union, derived
            # from the unattributable simulation, not summed per rule.
            "attributable": null_before - len(ids),
            "unattributable_ids": ids[:_INLINE_IDS_LIMIT],
            "unattributable_count": len(ids),
        }
    return report


def _doe_plans_fill_map(conn: sa.Connection) -> dict[str, str]:
    """Simulated doe_plans attribution: plan id -> project (dry-run).

    Campaign rule first, then experiment rule for still-unresolved plans
    — the same order ``apply_backfill`` fills them in.
    """
    out: dict[str, str] = {}
    if "doe_plans" not in _tables(conn):
        return out
    for parent, parent_key, fk in (("campaigns", "id", "campaign_id"),
                                    ("experiments", "id", "experiment_id")):
        if parent not in _tables(conn):
            continue
        cols = _columns(conn, "doe_plans")
        pcols = _columns(conn, parent)
        if "project_id" not in cols or fk not in cols or "project_id" not in pcols or parent_key not in pcols:
            continue
        for pid, proj in conn.execute(
            sa.text(
                f"SELECT doe_plans.id, {parent}.project_id FROM doe_plans "
                f"JOIN {parent} ON {parent}.{parent_key} = doe_plans.{fk} "
                f"WHERE doe_plans.project_id IS NULL AND doe_plans.{fk} IS NOT NULL "
                f"AND {parent}.project_id IS NOT NULL AND {parent}.project_id != ''"
            )
        ).all():
            out.setdefault(str(pid), str(proj))
    return out


def _unattributable_ids(conn: sa.Connection, table: str) -> list[str]:
    """NULL-row ids that no rule can attribute (dry-run simulation)."""
    null_ids = _null_ids(conn, table)
    if not null_ids:
        return []
    if table == "wiki_pages":
        return [i for i in null_ids if i not in _wiki_attribution(conn)]
    if table == "lab_tasks":
        # Chain-aware: a task is attributable when its plan already has a
        # project OR the plan itself would be attributed by this run.
        # (Audit simulates the ordered apply; a plain per-table query
        # would understate lab_tasks because its parent is still NULL.)
        plan_map = _doe_plans_fill_map(conn)
        stored = {}
        if "doe_plans" in _tables(conn):
            stored = {str(r[0]): r[1] for r in conn.execute(
                sa.text("SELECT id, project_id FROM doe_plans")).all()}
        task_plans = {str(r[0]): str(r[1]) for r in conn.execute(
            sa.text("SELECT id, doe_plan_id FROM lab_tasks WHERE project_id IS NULL")).all()
            if r[1]}
        attributable = {
            tid for tid, plan_id in task_plans.items()
            if (stored.get(plan_id) or plan_map.get(plan_id))
        }
        return [i for i in null_ids if i not in attributable]
    attributable: set[str] = set()
    for t, parent, parent_key, fk in _SQL_RULES:
        if t != table:
            continue
        if table not in _tables(conn) or parent not in _tables(conn):
            continue
        cols = _columns(conn, table)
        pcols = _columns(conn, parent)
        if "project_id" not in cols or fk not in cols or "project_id" not in pcols or parent_key not in pcols:
            continue
        rows = conn.execute(
            sa.text(
                f"SELECT {table}.id FROM {table} "
                f"WHERE {table}.project_id IS NULL AND {table}.{fk} IS NOT NULL "
                f"AND (SELECT project_id FROM {parent} "
                f"WHERE {parent}.{parent_key} = {table}.{fk}) IS NOT NULL "
                f"AND (SELECT project_id FROM {parent} "
                f"WHERE {parent}.{parent_key} = {table}.{fk}) != ''"
            )
        ).all()
        attributable.update(str(r[0]) for r in rows)
    return [i for i in null_ids if i not in attributable]


def apply_backfill(conn: sa.Connection, *, tables: list[str] | None = None) -> dict[str, Any]:
    """Fill every attributable NULL row (idempotent). Returns the report."""
    wanted = set(tables) if tables else None
    report: dict[str, Any] = {}
    for table in dict.fromkeys(t for t, _, _, _ in _SQL_RULES):
        if wanted is not None and table not in wanted:
            continue
        before = len(_null_ids(conn, table))
        filled = 0
        for t, parent, parent_key, fk in _SQL_RULES:
            if t == table:
                filled += _sql_fill(conn, table, parent, parent_key, fk)
        after = len(_null_ids(conn, table))
        report[table] = {"filled": filled, "null_before": before, "null_after": after}
    if wanted is None or "wiki_pages" in wanted:
        wiki_map = _wiki_attribution(conn)
        before = len(_null_ids(conn, "wiki_pages"))
        for wid, pid in wiki_map.items():
            conn.execute(
                sa.text("UPDATE wiki_pages SET project_id = :pid WHERE id = :wid"),
                {"pid": pid, "wid": wid},
            )
        after = len(_null_ids(conn, "wiki_pages"))
        report["wiki_pages"] = {"filled": len(wiki_map), "null_before": before, "null_after": after}
    for table in list(report):
        ids = _unattributable_ids(conn, table)
        report[table]["unattributable_ids"] = ids[:_INLINE_IDS_LIMIT]
        report[table]["unattributable_count"] = len(ids)
    return report
