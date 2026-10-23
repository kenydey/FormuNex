#!/usr/bin/env python3
"""Copy Datalab data into FormuNex's own database (方案 v1.1 §5.3.1, M7).

Two parts, both by default:

* training records (``experiments``) — factors / results copied into the row, results kept as
  ``legacy`` measurements;
* workbench (campaign) rows — each row's parameters, measurements, status, note and tags copied
  from its Datalab item into ``campaigns.sample_refs`` (``item_id`` unchanged).

Run once while Datalab is still reachable, then set FORMUNEX_EXPERIMENT_BACKEND=native and
FORMUNEX_CAMPAIGN_BACKEND=native (the defaults). Idempotent; ``--dry-run`` reports what would move
without writing. A workbench row whose Datalab item is gone is reported and kept unless
``--prune-missing-rows``.

用法:
    .venv/bin/python scripts/migrate_datalab_to_native.py --dry-run
    .venv/bin/python scripts/migrate_datalab_to_native.py
    .venv/bin/python scripts/migrate_datalab_to_native.py --only campaigns --prune-missing-rows
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-url", help="SQLAlchemy URL (default: FORMUNEX_DB_URL / settings.db_url)")
    parser.add_argument("--datalab-url", help="Datalab API URL (default: FORMUNEX_DATALAB_API_URL)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", choices=("experiments", "campaigns"), help="Migrate one part only")
    parser.add_argument("--prune-missing-rows", action="store_true",
                        help="Drop workbench rows whose Datalab item no longer exists (default: keep and report)")
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.config import get_settings
    from app.db.datalab_client import datalab_headers
    from app.services.eln.connectors.base import ConnectorError
    from app.services.eln.connectors.datalab import DatalabConnector
    from app.services.lab.datalab_migration import migrate, migrate_campaigns

    s = get_settings()
    conn = DatalabConnector(args.datalab_url or s.datalab_api_url, headers=datalab_headers(),
                            timeout=s.datalab_timeout_seconds)

    def fetch(item_id: str):
        try:
            return conn.item(item_id)
        except ConnectorError as exc:
            if "404" in str(exc):
                return None
            raise

    actor = "cli:migrate_datalab_to_native"
    result: dict = {}
    engine = create_engine(args.db_url or s.db_url, future=True)
    try:
        with Session(engine) as session:
            if args.only in (None, "experiments"):
                result["experiments"] = migrate(session, fetch, dry_run=args.dry_run, actor=actor)
            if args.only in (None, "campaigns"):
                result["campaigns"] = migrate_campaigns(session, fetch, dry_run=args.dry_run,
                                                        prune_missing=args.prune_missing_rows, actor=actor)
            if args.dry_run:
                session.rollback()
            else:
                session.commit()
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if any(part["errors"] for part in result.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
