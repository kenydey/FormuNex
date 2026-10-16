#!/usr/bin/env python3
"""Copy Datalab training records into FormuNex's own database (方案 v1.1 §5.3.1, M7).

Run once while Datalab is still reachable, then set FORMUNEX_EXPERIMENT_BACKEND=native (the default).
Idempotent; ``--dry-run`` reports what would move without writing.

用法:
    .venv/bin/python scripts/migrate_datalab_to_native.py --dry-run
    .venv/bin/python scripts/migrate_datalab_to_native.py
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
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.config import get_settings
    from app.db.datalab_client import datalab_headers
    from app.services.eln.connectors.base import ConnectorError
    from app.services.eln.connectors.datalab import DatalabConnector
    from app.services.lab.datalab_migration import migrate

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

    engine = create_engine(args.db_url or s.db_url, future=True)
    try:
        with Session(engine) as session:
            report = migrate(session, fetch, dry_run=args.dry_run, actor="cli:migrate_datalab_to_native")
            if args.dry_run:
                session.rollback()
            else:
                session.commit()
    finally:
        engine.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
