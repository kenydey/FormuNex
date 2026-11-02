#!/usr/bin/env python3
"""One-time Datalab -> native import (Datalab removal, batch 1).

Materialises Datalab-pointer rows in ``Campaign.sample_refs`` into native
refs (full row data embedded), with one ``workbench_row.import`` audit
entry per row. See ``backend/app/services/datalab_import.py`` for scope —
notably, Datalab version history imports as provenance (refcode, count,
ids), not per-version snapshots: the versions API returns metadata only.

Usage (from the repo root):

    backend/.venv/bin/python scripts/import_datalab_to_native.py
        # dry run: report only, writes nothing
    backend/.venv/bin/python scripts/import_datalab_to_native.py --apply
        # writes; re-runnable (imported refs are skipped on re-run)
    backend/.venv/bin/python scripts/import_datalab_to_native.py --apply --campaign 3
    backend/.venv/bin/python scripts/import_datalab_to_native.py --db-url sqlite:////tmp/copy.db --report /tmp/import.json

Exit code: 0 when every Datalab-pointer row imported (or none exist),
1 when any row failed, 2 on fatal errors (bad args, DB unreachable).
The real import against a production Datalab runs on the operator's side;
this script never touches production data by itself.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, "backend")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write; without it, dry run only")
    parser.add_argument("--campaign", type=int, action="append", default=None, help="Limit to campaign id (repeatable)")
    parser.add_argument("--db-url", default=None, help="Override FORMUNEX_DB_URL / settings db_url")
    parser.add_argument("--api-url", default=None, help="Override settings datalab_api_url")
    parser.add_argument("--actor", default="system", help="Audit actor for import entries")
    parser.add_argument("--report", default=None, help="Write the full JSON report to this file")
    args = parser.parse_args()

    if args.db_url:
        # Must precede any app import: default_session_factory resolves
        # settings.db_url, and the audit entries must land in the same DB
        # as the refs this script writes.
        os.environ["FORMUNEX_DB_URL"] = args.db_url

    from app.config import get_settings
    from app.db.database import default_session_factory
    from app.services import datalab_import

    settings = get_settings()
    api_url = args.api_url or settings.datalab_api_url
    try:
        report = datalab_import.run_import(
            session_factory=default_session_factory,
            api_url=api_url,
            apply=args.apply,
            actor=args.actor,
            campaign_ids=args.campaign,
        )
    except Exception as exc:  # noqa: BLE001 - fatal, report and exit 2
        print(f"import failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
    return 1 if report["totals"]["rows_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
