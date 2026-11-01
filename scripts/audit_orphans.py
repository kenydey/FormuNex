#!/usr/bin/env python3
"""Stage 2 / SR-033: orphan inventory for weak / polymorphic references.

These columns deliberately have no FK (polymorphic target, or a parent
that is referenced by convention): instead of a constraint, this script
counts dangling values so they cannot accumulate silently. Read-only:
it never deletes or rewrites data. Exit 1 when any orphans exist.

Checks (child column -> parent table):
* regulatory_documents.template_id -> doc_templates.id
* regulatory_documents.supersedes_id -> regulatory_documents.id (self)
* classification_results.formulation_version_id -> formulation_versions.id
* classification_results.data_release_id -> regulatory_data_releases.id
* lab_tasks.formulation_version_id -> formulation_versions.id
* samples.batch_id -> batches.id
* product_revisions.winning_experiment_id -> experiments.id
* measurements.method_id -> test_methods.id
* measurements.instrument_id -> instruments.id
* substances / substance_oel / substance_transport /
  substance_classifications / substance_generic_entries /
  regulatory_review_queue .release_id -> regulatory_data_releases.id

NOT checked: measurements.operator_id has no operators table — it is a
free-form account reference, not a verifiable parent.

Usage (from the repo root):
    backend/.venv/bin/python scripts/audit_orphans.py
    backend/.venv/bin/python scripts/audit_orphans.py --db-url sqlite:////tmp/copy.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

DEFAULT_DB = "backend/data/formunex.db"

CHECKS: tuple[tuple[str, str, str, str], ...] = (
    ("regulatory_documents", "template_id", "doc_templates", "id"),
    ("regulatory_documents", "supersedes_id", "regulatory_documents", "id"),
    ("classification_results", "formulation_version_id", "formulation_versions", "id"),
    ("classification_results", "data_release_id", "regulatory_data_releases", "id"),
    ("lab_tasks", "formulation_version_id", "formulation_versions", "id"),
    ("samples", "batch_id", "batches", "id"),
    ("product_revisions", "winning_experiment_id", "experiments", "id"),
    ("measurements", "method_id", "test_methods", "id"),
    ("measurements", "instrument_id", "instruments", "id"),
    ("substances", "release_id", "regulatory_data_releases", "id"),
    ("substance_oel", "release_id", "regulatory_data_releases", "id"),
    ("substance_transport", "release_id", "regulatory_data_releases", "id"),
    ("substance_classifications", "release_id", "regulatory_data_releases", "id"),
    ("substance_generic_entries", "release_id", "regulatory_data_releases", "id"),
    ("regulatory_review_queue", "release_id", "regulatory_data_releases", "id"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", default=None,
                        help="SQLAlchemy URL (sqlite only); default: dev DB read-only")
    args = parser.parse_args()
    if args.db_url:
        if not args.db_url.startswith("sqlite:///"):
            print("only sqlite URLs are supported", file=sys.stderr)
            return 2
        path = args.db_url[len("sqlite:///"):]
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(f"file:{DEFAULT_DB}?mode=ro", uri=True)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    total = 0
    for child, column, parent, parent_col in CHECKS:
        if child not in tables or parent not in tables:
            print(f"SKIP {child}.{column} -> {parent}: table missing")
            continue
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({child})")}
        if column not in cols:
            print(f"SKIP {child}.{column} -> {parent}: column missing")
            continue
        n = con.execute(
            f"SELECT COUNT(*) FROM {child} c LEFT JOIN {parent} p "
            f"ON p.{parent_col} = c.{column} "
            f"WHERE c.{column} IS NOT NULL AND c.{column} != '' AND p.{parent_col} IS NULL"
        ).fetchone()[0]
        total += n
        print(f"{child}.{column} -> {parent}: {n} orphan(s)")
    print(f"TOTAL: {total} orphan(s)")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
