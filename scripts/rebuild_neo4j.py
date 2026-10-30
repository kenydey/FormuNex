#!/usr/bin/env python3
"""P1-3: manually rebuild the Neo4j projection from the relational KG.

Reads ``kb_entities`` / ``kb_entity_links`` and re-projects them with
MERGE-based upserts (idempotent: re-running converges, never duplicates).
Manual only — no schedule, no ingestion hook.

Usage (from the repo root):
    backend/.venv/bin/python scripts/rebuild_neo4j.py
    backend/.venv/bin/python scripts/rebuild_neo4j.py --limit 100
    backend/.venv/bin/python scripts/rebuild_neo4j.py --dry-run   # counts only, no writes
"""

from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, "backend")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Limit entities projected")
    parser.add_argument("--dry-run", action="store_true", help="Count relational rows only, write nothing")
    args = parser.parse_args()

    from app.db.database import default_session_factory
    from app.db.models import KGEntity, KGEntityLink
    from app.services import neo4j_kg
    from app.services.kg.neo4j_rebuild import rebuild_from_relational

    if args.dry_run:
        factory = default_session_factory()
        with factory() as session:
            report = {
                "dry_run": True,
                "neo4j_enabled": neo4j_kg.is_enabled(),
                "entities": session.query(KGEntity).count(),
                "links_valid": session.query(KGEntityLink).filter(
                    KGEntityLink.is_valid.is_(True)).count(),
                "links_total": session.query(KGEntityLink).count(),
            }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    report = rebuild_from_relational(limit=args.limit)
    print(json.dumps(report.__dict__, ensure_ascii=False, indent=2))
    if report.disabled:
        print("Neo4j is disabled (neo4j_enabled=false); nothing was written.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
