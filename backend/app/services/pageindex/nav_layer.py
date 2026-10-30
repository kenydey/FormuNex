"""PageIndex navigation layer for general KB retrieval (Batch 2, Q4=A).

The deep two-stage path (``two_stage.two_stage_retrieve``) already navigates
trees for explicit deep queries. This module brings the tree into *general*
retrieval: for long documents (``pageindex_min_pages`` pages or more) among
the hybrid top-k, sections whose titles match the query get their chunks
boosted, and section chunks / tables that missed top-k are fetched as
supplements. Short documents pass through untouched.

Also owns tree hygiene for the general path:

* :func:`ensure_tree_current` — missing, version-stale, or page-span-orphaned
  trees are rebuilt on demand (fail-open: never raises, returns None).
* :func:`pageindex_coverage_stats` — counts for the batch-3 observability
  panel (trees present / stale / eligible / orphaned).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ...config import get_settings
from .tree_builder import BUILDER_VERSION, iter_nodes

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^kb:([^#]+)#c(\d+)$")
_LATIN_RE = re.compile(r"[A-Za-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def _query_terms(query: str) -> list[str]:
    """Search terms: latin words + CJK runs (len >= 2), deduped in order."""
    q = (query or "").strip()
    if not q:
        return []
    terms: list[str] = []
    for t in _LATIN_RE.findall(q):
        t = t.lower()
        if t not in terms:
            terms.append(t)
    for t in _CJK_RE.findall(q):
        if len(t) >= 2 and t not in terms:
            terms.append(t)
    if not terms:
        terms = [q]
    return terms


def _node_score(title: str, terms: list[str], query: str) -> int:
    """Title match score: full-query hit 3, CJK term 2, latin term 1."""
    t = (title or "").strip()
    if not t:
        return 0
    low = t.lower()
    q = (query or "").strip()
    if q and q in t:
        return 3
    score = 0
    for term in terms:
        if term in low:
            score += 2 if any("\u4e00" <= ch <= "\u9fff" for ch in term) else 1
    return score


def match_sections(tree: dict[str, Any], query: str) -> list[tuple[dict[str, Any], int]]:
    """Sections (non-root nodes) whose titles match the query, best first."""
    terms = _query_terms(query)
    if not terms:
        return []
    scored: list[tuple[dict[str, Any], int]] = []
    for node in iter_nodes(tree or {}):
        if node.get("kind") == "root":
            continue
        score = _node_score(node.get("title") or "", terms, query or "")
        if score > 0:
            scored.append((node, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def _tree_row(source_id: str):
    try:
        from ...db.toc_tree_store import get_toc_tree_store

        return get_toc_tree_store().get(source_id)
    except Exception:  # noqa: BLE001 - fail-open
        return None


def _chunk_rows(source_id: str) -> list | None:
    """Chunk rows for a source, or None when the store errors (fail-open)."""
    try:
        from ...db.chunk_store import get_chunk_store

        return list(get_chunk_store().get_by_source(source_id) or [])
    except Exception:  # noqa: BLE001 - fail-open
        return None


def ensure_tree_current(source_id: str):
    """Return the stored tree row, rebuilding when missing / stale / orphaned.

    Rebuild triggers: no row, ``builder_version`` != ``BUILDER_VERSION``,
    or the stored page count no longer matches the live chunk page span
    (chunks replaced without a tree rebuild). A store error leaves the
    existing row untouched. Never raises.
    """
    try:
        row = _tree_row(source_id)
        if row is None:
            from .two_stage import build_tree_for_source

            build_tree_for_source(source_id)
            return _tree_row(source_id)
        if (row.builder_version or "") != BUILDER_VERSION:
            from .two_stage import build_tree_for_source

            build_tree_for_source(source_id)
            return _tree_row(source_id)
        chunks = _chunk_rows(source_id)
        if chunks is None:
            return row
        if not chunks:
            from .two_stage import build_tree_for_source

            build_tree_for_source(source_id)
            return _tree_row(source_id)
        live_pages = max((int(getattr(c, "page_no", 0) or 0) for c in chunks), default=0)
        if row.page_count and live_pages and int(row.page_count) != live_pages:
            from .two_stage import build_tree_for_source

            build_tree_for_source(source_id)
            return _tree_row(source_id)
        return row
    except Exception:  # noqa: BLE001 - fail-open
        logger.debug("pageindex ensure_tree_current failed for %s", source_id, exc_info=True)
        return _tree_row(source_id)


def pageindex_coverage_stats() -> dict[str, Any]:
    """Tree coverage counts (batch-3 panel input; fail-open empty on error)."""
    settings = get_settings()
    try:
        from ...db.source_store import get_source_store
        from ...db.toc_tree_store import get_toc_tree_store

        rows = get_toc_tree_store().list_all()
        min_pages = int(settings.pageindex_min_pages or 20)
        stale = sum(1 for r in rows if (r.builder_version or "") != BUILDER_VERSION)
        eligible = sum(1 for r in rows if (r.page_count or 0) >= min_pages)
        orphan = 0
        store = get_source_store()
        for r in rows:
            try:
                if store.get(r.source_id) is None:
                    orphan += 1
            except Exception:  # noqa: BLE001 - per-row fail-open
                continue
        return {
            "trees_total": len(rows),
            "stale_version": stale,
            "eligible_with_tree": eligible,
            "orphan_trees": orphan,
            "min_pages": min_pages,
            "builder_version": BUILDER_VERSION,
        }
    except Exception:  # noqa: BLE001 - fail-open
        logger.debug("pageindex coverage stats failed", exc_info=True)
        return {}


def apply_nav(
    query: str, evidence: list
) -> tuple[list, list, list, dict[str, Any]]:
    """Apply the navigation layer to a hybrid Evidence list.

    Returns ``(boosted_evidence, extra_chunks, extra_tables, meta)`` where
    ``extra_chunks`` are chunk rows from matched sections that missed top-k
    and ``extra_tables`` are TableAssets attached to matched sections. The
    caller converts extras to Evidence (it owns source meta). Boosted
    evidence is re-sorted by relevance; extras are appended after by the
    caller, never interleaved by fabricated scores.
    """
    meta: dict[str, Any] = {
        "docs_navigated": 0,
        "sections_matched": 0,
        "chunks_boosted": 0,
        "chunks_added": 0,
        "tables_added": 0,
    }
    settings = get_settings()
    if not getattr(settings, "pageindex_nav_enabled", False) or not evidence:
        return list(evidence), [], [], meta
    boost = float(getattr(settings, "pageindex_nav_boost", 0.0) or 0.0)
    max_extra = int(getattr(settings, "pageindex_nav_max_extra_chunks", 0) or 0)
    min_pages = int(settings.pageindex_min_pages or 20)

    parsed: list[tuple[int, str, int]] = []  # (evidence idx, sid, ord)
    by_sid: dict[str, list[int]] = {}
    for idx, ev in enumerate(evidence):
        m = _IDENT_RE.match(getattr(ev, "identifier", "") or "")
        if not m:
            continue
        sid, ord_no = m.group(1), int(m.group(2))
        parsed.append((idx, sid, ord_no))
        by_sid.setdefault(sid, []).append(idx)
    if not by_sid:
        return list(evidence), [], [], meta

    boosted = list(evidence)
    extra_chunks: list = []
    extra_tables: list = []
    seen_extra_keys: set[str] = set()
    for sid in by_sid:
        row = ensure_tree_current(sid)
        if row is None or row.tree is None:
            continue
        if (row.page_count or 0) < min_pages:
            continue
        sections = match_sections(row.tree, query)
        if not sections:
            continue
        meta["docs_navigated"] += 1
        meta["sections_matched"] += len(sections)
        chunk_rows = _chunk_rows(sid)
        if chunk_rows is None:
            continue
        rows_by_id = {str(getattr(c, "id", "")): c for c in chunk_rows}
        rows_by_ord = {int(getattr(c, "ord", -1)): c for c in chunk_rows}
        present_ords = {ord_no for _, s, ord_no in parsed if s == sid}
        matched_ords: set[int] = set()
        matched_table_ids: set[str] = set()
        for node, _score in sections:
            for cid in node.get("chunk_ids") or []:
                c = rows_by_id.get(str(cid))
                if c is not None:
                    matched_ords.add(int(getattr(c, "ord", -1)))
            for tid in node.get("table_ids") or []:
                matched_table_ids.add(str(tid))
        if boost:
            for idx in by_sid[sid]:
                _, _, ord_no = next(p for p in parsed if p[0] == idx)
                if ord_no in matched_ords:
                    ev = boosted[idx]
                    new_rel = min(1.0, float(getattr(ev, "relevance", 0) or 0) + boost)
                    boosted[idx] = ev.model_copy(update={"relevance": new_rel})
                    meta["chunks_boosted"] += 1
        if max_extra > 0:
            for node, _score in sections:
                if len(extra_chunks) >= max_extra:
                    break
                for cid in node.get("chunk_ids") or []:
                    if len(extra_chunks) >= max_extra:
                        break
                    c = rows_by_id.get(str(cid))
                    if c is None:
                        continue
                    ord_no = int(getattr(c, "ord", -1))
                    if ord_no in present_ords:
                        continue
                    key = f"{sid}#c{ord_no}"
                    if key in seen_extra_keys:
                        continue
                    seen_extra_keys.add(key)
                    extra_chunks.append(c)
        if matched_table_ids:
            try:
                from ..table_contract import load_tables

                for t in load_tables(sid):
                    if str(t.table_id) in matched_table_ids:
                        extra_tables.append(t)
            except Exception:  # noqa: BLE001 - tables are supplementary
                pass
    meta["chunks_added"] = len(extra_chunks)
    meta["tables_added"] = len(extra_tables)
    boosted.sort(key=lambda e: float(getattr(e, "relevance", 0) or 0), reverse=True)
    return boosted, extra_chunks, extra_tables, meta
