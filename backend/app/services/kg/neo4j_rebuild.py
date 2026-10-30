"""Relational -> Neo4j projection rebuild (P1-3, option A).

Neo4j writes today happen only through the explicit ``/api/kg/neo4j``
endpoints, and ingestion does not dual-write, so the graph drifts from
the relational KG tables with no way back. This module is the way back:
it reads ``kb_entities`` / ``kb_entity_links`` and re-projects them with
the adapter's own MERGE-based upserts, so re-running it converges the
graph instead of duplicating it.

Trigger: manual only (``scripts/rebuild_neo4j.py``). No automatic run:
a rebuild is a full-graph write pass and must stay an operator decision,
and the sandbox has no Neo4j to run it against anyway.

Mapping (uid space is shared with the wiki projection, which also uses
entity ids as Compound uids, so the two projections converge):
- ``KGEntity`` -> ``Compound`` node: uid = entity id, name =
  canonical_name (fallback zh_name, then id), cas_number / smiles /
  supplier carried over, kind recorded in notes.
- ``KGEntityLink`` (``is_valid`` only) -> relationship via
  ``upsert_relationship``. The link type is interpolated into Cypher by
  the adapter, so only ``^[A-Z0-9_]+$`` types are projected; anything
  else is counted as skipped-invalid, never interpolated.
- ``KGMention`` rows are NOT projected: they are occurrences, not graph
  structure (their signal is already summarized in ``mention_count``).

Not project-scoped: ``kb_entities`` carries no project column — the
projection is the global entity graph, and project scoping lives in
retrieval, exactly like the wiki projection.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .. import neo4j_kg
from ...db.database import default_session_factory
from ...db.models import KGEntity, KGEntityLink

logger = logging.getLogger(__name__)

_REL_TYPE_RE = re.compile(r"^[A-Z0-9_]+$")


@dataclass
class Neo4jRebuildReport:
    disabled: bool = False
    schema_ok: bool = False
    entities_seen: int = 0
    entities_upserted: int = 0
    entities_failed: int = 0
    links_seen: int = 0
    links_upserted: int = 0
    links_skipped_missing_endpoint: int = 0
    links_skipped_invalid_type: int = 0
    links_skipped_not_valid: int = 0
    links_failed: int = 0
    meta_recorded: bool = False
    failures: list[str] = field(default_factory=list)


def rebuild_from_relational(
    *,
    session_factory=None,
    limit: int | None = None,
) -> Neo4jRebuildReport:
    """Project all relational KG entities + links into Neo4j (idempotent)."""
    report = Neo4jRebuildReport()
    if not neo4j_kg.is_enabled():
        report.disabled = True
        return report
    report.schema_ok = bool(neo4j_kg.ensure_schema())
    factory = session_factory or default_session_factory()
    with factory() as session:
        entity_q = session.query(KGEntity).order_by(KGEntity.id)
        if limit is not None:
            entity_q = entity_q.limit(limit)
        entities = entity_q.all()
        projected_uids: set[str] = set()
        for entity in entities:
            report.entities_seen += 1
            name = entity.canonical_name or entity.zh_name or entity.id
            try:
                ok = neo4j_kg.upsert_compound(
                    entity.id,
                    name,
                    cas_number=entity.cas_no,
                    smiles=entity.smiles,
                    supplier=entity.supplier or None,
                    notes=f"kind:{entity.kind}" if entity.kind else None,
                )
            except Exception as exc:  # adapter normally returns False; never abort the run
                ok = False
                report.failures.append(f"compound {entity.id}: {exc}")
            if ok:
                report.entities_upserted += 1
                projected_uids.add(entity.id)
            else:
                report.entities_failed += 1
        link_q = session.query(KGEntityLink).order_by(KGEntityLink.id)
        for link in link_q.all():
            report.links_seen += 1
            if not link.is_valid:
                report.links_skipped_not_valid += 1
                continue
            if not _REL_TYPE_RE.match(link.link_type or ""):
                report.links_skipped_invalid_type += 1
                continue
            if link.src_entity_id not in projected_uids or link.dst_entity_id not in projected_uids:
                # The adapter MATCHes both endpoints; an unprojected
                # endpoint can never link. Counted, not silently dropped:
                # a second run after the entity appears will pick it up.
                report.links_skipped_missing_endpoint += 1
                continue
            try:
                ok = neo4j_kg.upsert_relationship(
                    link.src_entity_id,
                    link.dst_entity_id,
                    link.link_type,
                    confidence=link.confidence,
                    evidence=",".join(link.evidence_refs or []) or None,
                    extraction_method=link.extraction_method or None,
                )
            except Exception as exc:
                ok = False
                report.failures.append(f"link {link.id}: {exc}")
            if ok:
                report.links_upserted += 1
            else:
                report.links_failed += 1
    skipped = (
        report.links_skipped_missing_endpoint
        + report.links_skipped_invalid_type
        + report.links_skipped_not_valid
    )
    report.meta_recorded = bool(
        neo4j_kg.record_rebuild_meta(
            source="relational",
            entities=report.entities_upserted,
            links=report.links_upserted,
            skipped=skipped,
            failed=report.entities_failed + report.links_failed,
        )
    )
    return report
