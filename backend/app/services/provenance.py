"""Cross-artifact provenance graph (W2-6 / P1-2, Phase 2D 2-22).

Records directed edges between node kinds across the whole R&D → PLM
chain so any conclusion can be traced back to the evidence behind it:

    source -> claim -> formulation            (report / recommendation claims)
    run -> formulation                         (DOE experiments test formulations)
    formulation -> formulation_version         (version snapshots)
    formulation_version -> product_revision    (winning / frozen_as)
    experiment -> product_revision            (winning / selected_as_winning)
    product -> product_revision               (has_revision)
    product_revision -> document              (documented_as)
    product_revision -> release_package       (released_as)

Phase 2D (2-22): ONE edge store. The old ``provenance_edges`` table was
SQLite-only (``sqlite_master`` schema checks, ``INSERT OR IGNORE``) and the
API read it while ``winning.py`` wrote a second ``lineage_edges`` table —
on PostgreSQL the SQLite DDL failed fail-open, so ``lineage`` silently
returned []. All writes now go through the ORM ``LineageEdgeRow``
(``lineage_edges``, alembic-managed, dialect-neutral), and the legacy table
was retired by migration 0062 (rows copied over, then dropped).

Every public helper is fail-open — a provenance error is logged and
swallowed so provenance can never break the business path it instruments.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, inspect, or_, select
from sqlalchemy.orm import Session, sessionmaker

from ..db.models_plm import LineageEdgeRow
from ..db.session_utils import commit_session

logger = logging.getLogger(__name__)

#: Node kinds that may appear on either end of an edge.
NODE_TYPES = frozenset({
    "artifact",
    "run",
    "source",
    "claim",
    "formulation",
    "experiment",
    "formulation_version",
    "product",
    "product_revision",
    "document",
    "release_package",
    "project",
    "batch",
})

#: Reserved ``SourceDocument`` fields that provenance / agents must not rewrite.
#: ``source_url`` is the plan name for the model's ``origin_url``; ``retrieved_at``
#: has no model column yet but is guarded for forward compatibility.
RESERVED_SOURCE_FIELDS = frozenset(
    {"source_url", "retrieved_at", "content_hash", "origin_url", "created_at"}
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _session_factory() -> sessionmaker[Session]:
    from ..db.database import default_session_factory

    return default_session_factory()


def claim_id_for_text(text_value: str) -> str:
    """Stable claim node id derived from the claim text (sha256, 16 hex)."""
    digest = hashlib.sha256((text_value or "").encode("utf-8")).hexdigest()[:16]
    return f"claim:{digest}"


def ensure_provenance(session_factory: sessionmaker[Session] | None = None) -> None:
    """Create the ``lineage_edges`` table if it is missing.

    The table is alembic-managed (migration 0062), so in production it
    already exists and this is a read-only existence check — no write, no
    commit. Creation only happens for databases that predate the migration
    (e.g. a test DB built from ``Base.metadata`` before 0062 landed).
    A concurrent creator loses the race with an "already exists" error, and
    a concurrent writer may hold SQLite's write lock ("database is locked");
    both are swallowed and retried (up to 3 attempts).
    """
    sf = session_factory or _session_factory()
    for _ in range(3):
        with sf() as session:
            bind = session.get_bind()
            if inspect(bind).has_table(LineageEdgeRow.__tablename__):
                return
            try:
                LineageEdgeRow.__table__.create(bind=bind, checkfirst=True)
                session.commit()
                return
            except Exception as exc:
                session.rollback()
                if "already exists" in str(exc) or "database is locked" in str(exc):
                    continue
                raise


def _validate_node(node_type: str, node_id: str) -> None:
    if node_type not in NODE_TYPES:
        raise ValueError(f"unknown provenance node type: {node_type!r}")
    if not node_id:
        raise ValueError("provenance node id must be non-empty")


Edge = tuple[str, str, str, str] | tuple[str, str, str, str, str]


def _validated_rows(edges: Iterable[Edge]) -> list[dict[str, str]]:
    """Validate a whole batch up front (``ValueError`` before anything is written) and normalise to row dicts."""
    rows: list[dict[str, str]] = []
    for edge in edges:
        from_type, from_id, to_type, to_id, *rest = edge
        _validate_node(from_type, from_id)
        _validate_node(to_type, to_id)
        rows.append(
            {
                "from_type": from_type,
                "from_id": from_id,
                "to_type": to_type,
                "to_id": to_id,
                "relation": (rest[0] if rest else "") or "derived_from",
            }
        )
    return rows


def _insert_missing(session: Session, rows: list[dict[str, str]], *, created_by: str | None = None) -> None:
    """Insert the rows not already present (idempotent on the UNIQUE edge key).

    One atomic ``INSERT ... ON CONFLICT DO NOTHING`` — the SELECT-then-INSERT
    version had a TOCTOU race: two concurrent writers both see the edge as
    missing, both INSERT, one dies on the UNIQUE constraint. The upsert is
    the ORM equivalent of the old ``INSERT OR IGNORE``. Dialects without a
    native upsert fall back to SELECT-then-INSERT (best effort).
    """
    if not rows:
        return
    now = _utcnow()
    values = [
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "lineage:" + "|".join(
                (r["from_type"], r["from_id"], r["to_type"], r["to_id"], r["relation"])))),
            "created_by": created_by,
            "created_at": now,
            **r,
        }
        for r in rows
    ]
    dialect = session.get_bind().dialect.name if session.get_bind() is not None else ""
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _insert
    else:
        _insert = None
    if _insert is not None:
        session.execute(_insert(LineageEdgeRow).values(values).on_conflict_do_nothing())
        return
    conditions = [
        and_(
            LineageEdgeRow.from_type == r["from_type"],
            LineageEdgeRow.from_id == r["from_id"],
            LineageEdgeRow.to_type == r["to_type"],
            LineageEdgeRow.to_id == r["to_id"],
            LineageEdgeRow.relation == r["relation"],
        )
        for r in rows
    ]
    existing = {
        (e.from_type, e.from_id, e.to_type, e.to_id, e.relation)
        for e in session.execute(select(LineageEdgeRow).where(or_(*conditions))).scalars()
    }
    for value in values:
        key = (value["from_type"], value["from_id"], value["to_type"], value["to_id"], value["relation"])
        if key not in existing:
            session.add(LineageEdgeRow(**value))


def link_many(
    edges: Iterable[Edge],
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> bool:
    """Record many directed edges ``(from_type, from_id, to_type, to_id[, relation])`` in ONE transaction.

    One ``commit_session`` write instead of a connection, a schema check and
    a bare ``commit`` per edge: a DOE cycle of N runs x 5 candidate
    formulations is N * 5 edges, and ``record_claim_sources`` one per cited
    source. Idempotent and fail-open (False on a database error).
    Raises ``ValueError`` — before anything is written, for the whole batch —
    on an unknown node type or an empty node id: those are programmer
    errors, not runtime failures.
    """
    rows = _validated_rows(edges)
    if not rows:
        return True
    try:
        sf = session_factory or _session_factory()
        ensure_provenance(sf)
        with commit_session(sf) as session:
            _insert_missing(session, rows)
        return True
    except Exception:
        logger.warning("provenance.link_many failed (fail-open)", exc_info=True)
        return False


def link_many_in_session(
    session: Session,
    edges: Iterable[Edge],
    *,
    created_by: str | None = None,
) -> bool:
    """Record edges inside the caller's session/transaction (in-session hook for services).

    The edges commit or roll back with the business write they annotate.
    Validation failures raise ``ValueError`` (programmer error); a database
    error is isolated in a SAVEPOINT so it cannot poison the caller's
    transaction (on PostgreSQL one failed statement aborts the whole
    transaction) and returns False (fail-open).
    """
    rows = _validated_rows(edges)
    if not rows:
        return True
    try:
        with session.begin_nested():
            _insert_missing(session, rows, created_by=created_by)
        return True
    except Exception:
        logger.warning("provenance.link_many_in_session failed (fail-open)", exc_info=True)
        return False


def link(
    from_type: str,
    from_id: str,
    to_type: str,
    to_id: str,
    relation: str = "derived_from",
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> bool:
    """Record one directed edge. Idempotent; fail-open (returns False on DB error).

    Raises ``ValueError`` on an unknown node type or empty node id — those are
    programmer errors, not runtime failures. Several edges belong in :func:`link_many`.
    """
    return link_many([(from_type, from_id, to_type, to_id, relation)], session_factory=session_factory)


def record_claim_sources(
    claim_text: str,
    source_ids: list[str],
    *,
    relation: str = "cites",
    session_factory: sessionmaker[Session] | None = None,
) -> str:
    """Link one claim node to its evidence sources. Returns the claim node id."""
    claim_node_id = claim_id_for_text(claim_text)
    unique = list(dict.fromkeys(sid for sid in source_ids or [] if sid))
    link_many(
        [("claim", claim_node_id, "source", sid, relation) for sid in unique],
        session_factory=session_factory,
    )
    return claim_node_id


def _bfs(
    session: Session, node_type: str, node_id: str, depth: int, *, downstream: bool
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    visited: set[tuple[str, str]] = {(node_type, node_id)}
    seen_edges: set[tuple[str, str, str, str, str]] = set()
    frontier: list[tuple[str, str]] = [(node_type, node_id)]
    for _ in range(depth):
        nxt: list[tuple[str, str]] = []
        for ttype, tid in frontier:
            if downstream:
                stmt = select(LineageEdgeRow).where(
                    LineageEdgeRow.from_type == ttype, LineageEdgeRow.from_id == tid
                )
            else:
                stmt = select(LineageEdgeRow).where(
                    LineageEdgeRow.to_type == ttype, LineageEdgeRow.to_id == tid
                )
            for edge in session.execute(stmt).scalars():
                key = (edge.from_type, edge.from_id, edge.to_type, edge.to_id, edge.relation)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                out.append(
                    {
                        "from_type": edge.from_type,
                        "from_id": edge.from_id,
                        "to_type": edge.to_type,
                        "to_id": edge.to_id,
                        "relation": edge.relation,
                    }
                )
                node_key = (edge.to_type, edge.to_id) if downstream else (edge.from_type, edge.from_id)
                if node_key not in visited:
                    visited.add(node_key)
                    nxt.append(node_key)
        frontier = nxt
        if not frontier:
            break
    return out


def lineage(
    node_type: str,
    node_id: str,
    *,
    depth: int = 3,
    session_factory: sessionmaker[Session] | None = None,
) -> list[dict[str, Any]]:
    """Upstream BFS from a node: edges whose target is the node, then targets'
    own upstream edges, up to ``depth`` hops.

    Cycle-safe (visited node keys) and deduped (UNIQUE edge constraint plus a
    seen-edge set). Returns edge dicts in BFS order. Fail-open: returns [] on
    DB error.
    """
    _validate_node(node_type, node_id)
    if depth < 1:
        return []
    try:
        sf = session_factory or _session_factory()
        ensure_provenance(sf)
        with sf() as session:
            return _bfs(session, node_type, node_id, depth, downstream=False)
    except Exception:
        logger.warning("provenance.lineage failed (fail-open)", exc_info=True)
        return []


def downstream(
    node_type: str,
    node_id: str,
    *,
    depth: int = 3,
    session_factory: sessionmaker[Session] | None = None,
) -> list[dict[str, Any]]:
    """Downstream BFS from a node (mirror of :func:`lineage`): edges whose
    source is the node, then their targets' downstream edges, up to ``depth``
    hops. Fail-open: returns [] on DB error."""
    _validate_node(node_type, node_id)
    if depth < 1:
        return []
    try:
        sf = session_factory or _session_factory()
        ensure_provenance(sf)
        with sf() as session:
            return _bfs(session, node_type, node_id, depth, downstream=True)
    except Exception:
        logger.warning("provenance.downstream failed (fail-open)", exc_info=True)
        return []


def formulation_id_for(formulation: Any) -> str:
    """Stable formulation node id from ingredient composition (sha256, 16 hex).

    Accepts a ``Formulation``-like object or a plain dict. Same composition →
    same node id, so repeated DOE runs testing one composition converge on one
    node and cross-run lineage queries work.
    """
    try:
        ings = (
            formulation.ingredients
            if hasattr(formulation, "ingredients")
            else (formulation.get("ingredients", []) if isinstance(formulation, dict) else [])
        )
        rows: list[str] = []
        for ing in ings or []:
            if hasattr(ing, "name"):
                name, pct = ing.name or "", ing.weight_pct or 0
            elif isinstance(ing, dict):
                name, pct = ing.get("name", ""), ing.get("weight_pct", 0)
            else:
                continue
            rows.append(f"{name}:{round(float(pct), 4)}")
        payload = "|".join(sorted(rows))
    except Exception:
        payload = repr(formulation)[:500]
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"formulation:{digest}"


def count_edges(*, session_factory: sessionmaker[Session] | None = None) -> int:
    """Edge count (diagnostics/tests). Fail-open: -1 on DB error."""
    try:
        sf = session_factory or _session_factory()
        ensure_provenance(sf)
        with sf() as session:
            return int(session.execute(select(func.count()).select_from(LineageEdgeRow)).scalar() or 0)
    except Exception:
        logger.warning("provenance.count_edges failed (fail-open)", exc_info=True)
        return -1
