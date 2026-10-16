#!/usr/bin/env python3
"""Load the bundled China regulatory data (substances, 《危险化学品目录》 classifications,
GBZ 2.1 exposure limits, GB 12268 entries, H/P statements) into the database.

Run after ``alembic upgrade head``. Idempotent: an unchanged data set is skipped
unless ``--force``; rows edited or locked in the application are preserved.

用法:
    .venv/bin/python scripts/seed_regulatory_db.py                 # 使用 FORMUNEX_DB_URL
    .venv/bin/python scripts/seed_regulatory_db.py --db-url sqlite:///./data/formunex.db
    .venv/bin/python scripts/seed_regulatory_db.py --no-common     # 跳过 13 万条物质名录
    .venv/bin/python scripts/seed_regulatory_db.py --force --label 2026Q4
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
    parser.add_argument("--label", help="Release label recorded in regulatory_data_releases")
    parser.add_argument("--no-common", action="store_true", help="Skip the 131k common_chemicals list")
    parser.add_argument("--force", action="store_true", help="Re-import even if the data set is unchanged")
    parser.add_argument("--resources", type=Path, help="Alternative resources directory")
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine, inspect

    from app.config import get_settings
    from app.services.plm.regulatory.seed import seed_regulatory_db

    url = args.db_url or get_settings().db_url
    engine = create_engine(url, future=True)
    try:
        if "substances" not in inspect(engine).get_table_names():
            print("substances table missing — run `alembic upgrade head` first.", file=sys.stderr)
            return 2
        result = seed_regulatory_db(
            engine,
            resources=args.resources,
            label=args.label,
            include_common=not args.no_common,
            force=args.force,
        )
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
