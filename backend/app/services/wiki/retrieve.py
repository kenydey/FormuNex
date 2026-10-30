"""Wiki keyword retrieve for Chat dual-track (W3).

Batch 2: blend retrieval is FTS-first (``wiki/fts.py``) merged with the
keyword + embedded results from :func:`search_wiki`; substring keyword
search remains the fallback when FTS and embedded both come back empty.
"""
from __future__ import annotations

import re
from typing import Any

from ...config import get_settings
from ...db.wiki_store import get_wiki_store
from ...domain.schemas import Evidence
from .schema import parse_front_matter

_TOKEN = re.compile(r"[a-zA-Z0-9\u4e00-\u9fff]{2,}")


def is_wiki_evidence(ev: Evidence | dict[str, Any]) -> bool:
    if isinstance(ev, dict):
        src = str(ev.get("source") or "")
        ident = str(ev.get("identifier") or "")
    else:
        src = ev.source or ""
        ident = ev.identifier or ""
    return src == "wiki" or ident.startswith("wiki:")


def filter_raw_evidence(evidence: list[Evidence]) -> list[Evidence]:
    """Claims / provenance stay on Raw chunks only."""
    return [e for e in evidence if not is_wiki_evidence(e)]


def _normalize_chat_mode(raw: str | None) -> str:
    mode = (raw or "balanced").strip().lower()
    if mode in ("wiki_first", "wiki", "wiki-priority"):
        return "wiki_first"
    if mode in ("raw_first", "raw", "raw-priority"):
        return "raw_first"
    return "balanced"


def search_wiki(
    query: str, *, k: int = 5, project_id: str | None = None
) -> list[Evidence]:
    """Keyword search over wiki titles / norm_key / markdown body.

    ``project_id`` scopes retrieval to one project (P0-2); legacy/None
    keeps the old global behavior.
    """
    settings = get_settings()
    if not settings.wiki_enabled or not settings.wiki_chat_blend:
        return []
    if _normalize_chat_mode(settings.wiki_chat_mode) == "raw_first":
        k = min(k, 2)
    q = (query or "").strip()
    if not q:
        return []
    tokens = [t.lower() for t in _TOKEN.findall(q)]
    if not tokens:
        tokens = [q.lower()]

    store = get_wiki_store()
    pages = store.list_pages(
        limit=min(500, max(50, k * 40)), project_id=project_id
    )
    scored: list[tuple[float, Evidence]] = []
    for row in pages:
        md = store.read_markdown(row.path) or ""
        meta, body = parse_front_matter(md)
        title = (row.title or meta.get("title") or row.path).strip()
        blob = f"{title}\n{row.norm_key}\n{row.path}\n{body}".lower()
        hits = sum(1 for t in tokens if t in blob)
        if hits <= 0:
            continue
        summary = ""
        if "## Summary" in body:
            summary = body.split("## Summary", 1)[1]
            if "## " in summary:
                summary = summary.split("## ", 1)[0]
            summary = summary.strip()
        snippet = (summary or body)[:800].strip()
        if not snippet:
            continue
        score = min(
            1.0,
            0.35
            + 0.15 * hits
            + (0.1 if row.kind in ("material", "system", "pitfall") else 0)
            + (0.08 if row.kind == "theme" and str(row.path or "").startswith("themes/project-") else 0),
        )
        scored.append(
            (
                score,
                Evidence(
                    source="wiki",
                    identifier=f"wiki:{row.path}",
                    title=f"[Wiki/{row.kind}] {title}",
                    snippet=snippet,
                    relevance=score,
                    url=None,
                ),
            )
        )
    scored.sort(key=lambda x: x[0], reverse=True)
    keyword_hits = [ev for _, ev in scored[: max(1, min(20, k))]]

    # Phase 3: merge embedded wiki summaries (semantic) when flag on.
    if settings.wiki_embed_enabled:
        try:
            from .embed import search_wiki_embedded

            embedded = search_wiki_embedded(q, k=k, project_id=project_id)
        except Exception:
            embedded = []
        if embedded:
            by_id: dict[str, Evidence] = {ev.identifier: ev for ev in embedded}
            for ev in keyword_hits:
                prev = by_id.get(ev.identifier)
                if prev is None or float(ev.relevance or 0) > float(prev.relevance or 0):
                    by_id[ev.identifier] = ev
            merged = sorted(
                by_id.values(),
                key=lambda e: float(e.relevance or 0),
                reverse=True,
            )
            return merged[: max(1, min(20, k))]
    return keyword_hits


def _fts_blend_hits(
    question: str, top_k: int, project_id: str | None = None
) -> list[Evidence]:
    """FTS hits as Evidence, relevance mapped from FTS rank order.

    SQLite FTS5 bm25 ranks are on an arbitrary scale, so relevance is a
    relative mapping inside this hit list (best 0.9 → worst 0.5, single
    hit 0.7) — never presented as an absolute relevance score. Empty on
    any failure (caller falls back to :func:`search_wiki`).

    Project-scoped blends skip FTS: the FTS table carries no project
    dimension, so merging it would leak cross-project pages. Project
    blends keep keyword + embedded (both project-filtered).
    """
    settings = get_settings()
    if project_id is not None:
        return []
    if not getattr(settings, "wiki_fts_enabled", False):
        return []
    try:
        from .fts import search_fts

        rows = search_fts(get_wiki_store().session_factory, question, limit=top_k)
    except Exception:
        return []
    if not rows:
        return []
    n = len(rows)
    out: list[Evidence] = []
    for i, row in enumerate(rows):
        rel = 0.7 if n == 1 else 0.9 - 0.4 * (i / max(1, n - 1))
        title = (row.get("title") or row.get("path") or "").strip()
        out.append(
            Evidence(
                source="wiki",
                identifier=f"wiki:{row.get('path')}",
                title=f"[Wiki/{row.get('kind')}] {title}",
                snippet=(row.get("snippet") or "")[:800],
                relevance=round(rel, 4),
                url=None,
            )
        )
    return out


def _wiki_blend_hits(
    question: str, *, k: int, project_id: str | None = None
) -> list[Evidence]:
    """Blend retrieval: FTS merged with keyword + embedded, substring fallback.

    FTS and :func:`search_wiki` (keyword + embedded merge) results are
    combined by identifier, keeping the higher relevance on collision,
    sorted by relevance, capped at ``k``. When FTS is off or empty, the
    result is exactly :func:`search_wiki` (pre-Batch-2 behavior).
    """
    base = search_wiki(question, k=k, project_id=project_id)
    fts = _fts_blend_hits(question, k, project_id)
    if not fts:
        return base
    by_id: dict[str, Evidence] = {ev.identifier: ev for ev in base}
    for ev in fts:
        prev = by_id.get(ev.identifier)
        if prev is None or float(ev.relevance or 0) > float(prev.relevance or 0):
            by_id[ev.identifier] = ev
    merged = sorted(
        by_id.values(), key=lambda e: float(e.relevance or 0), reverse=True
    )
    return merged[: max(1, k)]


def blend_wiki_evidence(
    question: str,
    sources: list[Evidence],
    *,
    k: int | None = None,
    project_id: str | None = None,
) -> tuple[list[Evidence], int]:
    """Merge wiki hits (deduped). Ordering depends on ``wiki_chat_mode``.

    Returns (merged, wiki_added_count). Claims still use ``filter_raw_evidence``.
    """
    settings = get_settings()
    if not settings.wiki_enabled or not settings.wiki_chat_blend:
        return sources, 0
    mode = _normalize_chat_mode(settings.wiki_chat_mode)
    if mode == "raw_first":
        default_k = min(2, max(1, settings.kb_chat_top_k // 8 or 1))
    elif mode == "wiki_first":
        default_k = min(8, max(3, settings.kb_chat_top_k // 2 or 3))
    else:
        default_k = min(5, max(2, settings.kb_chat_top_k // 4 or 2))
    top_k = k if k is not None else default_k
    hits = _wiki_blend_hits(question, k=top_k, project_id=project_id)
    if not hits:
        return sources, 0
    seen = {ev.identifier for ev in sources}
    added = [h for h in hits if h.identifier not in seen]
    if not added:
        return sources, 0
    if mode == "raw_first":
        return sources + added, len(added)
    return added + sources, len(added)
