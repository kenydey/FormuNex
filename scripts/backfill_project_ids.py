#!/usr/bin/env python3
"""P1-2: project-attribution backfill for legacy NULL ``project_id`` rows.

Extends migration 0033's rules (doe_plans <- campaign/experiment,
wiki_pages <- source_ids) to lab_tasks, chat_messages and
mcp_approval_requests. Only attributable rows are filled; the rest are
reported as the unattributable list and left NULL — never silently
defaulted. The tightening migration (nullable -> NOT NULL) must wait
until that list is empty or Cheng has confirmed the remainder.

Usage (from the repo root):
    backend/.venv/bin/python scripts/backfill_project_ids.py --dry-run
    backend/.venv/bin/python scripts/backfill_project_ids.py --report /tmp/p12.json
    backend/.venv/bin/python scripts/backfill_project_ids.py --table doe_plans --dry-run
    backend/.venv/bin/python scripts/backfill_project_ids.py --db-url sqlite:////tmp/copy.db
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, "backend")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report only, write nothing")
    parser.add_argument("--table", action="append", default=None, help="Limit to one table (repeatable)")
    parser.add_argument("--db-url", default=None, help="Override FORMUNEX_DB_URL / settings db_url")
    parser.add_argument("--report", default=None, help="Write the full JSON report to this file")
    args = parser.parse_args()

    from app.config import get_settings
    from app.db.database import make_engine
    from app.services import project_backfill

    db_url = args.db_url or os.environ.get("FORMUNEX_DB_URL") or get_settings().db_url
    engine = make_engine(db_url)
    with engine.begin() as conn:
        if args.dry_run:
            report = project_backfill.audit(conn, tables=args.table)
        else:
            report = project_backfill.apply_backfill(conn, tables=args.table)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"report written to {args.report}")
    unattributed = sum(v.get("unattributable_count", 0) for v in report.values())
    print(f"unattributable rows remaining: {unattributed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
