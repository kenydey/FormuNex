#!/usr/bin/env python3
"""Import MSDS-Generator formula records into FormuNex (整合方案 §6.3).

Input: a JSON file holding a list of records, or one JSON object per line::

    {"product_code": "FX-100", "product_name_zh": "FX-100 环氧底漆", "version": 1,
     "company": {"name": "方枢示例化工有限公司", "emergency_phone": "021-0001"},
     "components": [{"name": "二甲苯", "pct": 25, "cas": "1330-20-7"},
                    {"name": "环氧树脂", "pct": 60, "cas": "25068-38-6"},
                    {"name": "水", "pct": 15, "cas": "7732-18-5"}],
     "source_ref": "msds-gen-001"}

Also accepts CSV with columns ``product_code,product_name_zh,version,
company_name,company_emergency_phone,components`` where ``components`` is
``name:pct:cas;name:pct:cas``.

Idempotent: a record whose (company, product, source_ref) mapping already
exists is skipped, never duplicated or overwritten. ``--dry-run`` validates
without writing.

用法:
    .venv/bin/python scripts/import_msds_generator_formulas.py formulas.json --dry-run
    .venv/bin/python scripts/import_msds_generator_formulas.py formulas.json
    .venv/bin/python scripts/import_msds_generator_formulas.py formulas.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_records(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".csv":
        out = []
        for row in csv.DictReader(text.splitlines()):
            comps = []
            for part in (row.get("components") or "").split(";"):
                part = part.strip()
                if not part:
                    continue
                bits = part.split(":")
                comps.append({"name": bits[0], "pct": float(bits[1]) if len(bits) > 1 else 0,
                              "cas": bits[2] if len(bits) > 2 else ""})
            out.append({"product_code": row.get("product_code"), "product_name_zh": row.get("product_name_zh"),
                        "version": int(row.get("version") or 1),
                        "company": {"name": row.get("company_name") or "",
                                    "emergency_phone": row.get("company_emergency_phone") or ""},
                        "components": comps, "source_ref": row.get("source_ref") or ""})
        return out
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in text.splitlines() if line.strip()]  # JSONL
    if isinstance(data, dict):
        return data.get("records") or data.get("formulas") or [data]  # 单条记录对象
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="JSON / JSONL / CSV file with formula records")
    parser.add_argument("--db-url", help="SQLAlchemy URL (default: FORMUNEX_DB_URL / settings.db_url)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.config import get_settings
    from app.services.plm.formula_mapping import import_formulas

    records = _load_records(Path(args.input))
    s = get_settings()
    engine = create_engine(args.db_url or s.db_url, future=True)
    try:
        with Session(engine) as session:
            report = import_formulas(session, records, dry_run=args.dry_run)
            if not args.dry_run:
                session.commit()
    finally:
        engine.dispose()
    print(json.dumps({"input": args.input, "dry_run": args.dry_run,
                      "created": len(report["created"]), "skipped": len(report["skipped"]),
                      "errors": report["errors"], "warnings": report["warnings"]}, ensure_ascii=False, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
