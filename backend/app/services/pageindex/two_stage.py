"""Two-stage hybrid RAG with PageIndex (plan §5.1.1).

Stage 1 (breadth) is the existing persistent-KB ``hybrid_search`` (BM25 +
embeddings, cross-lingual expansion, structure search) over the whole corpus.
Stage 2 (depth) runs only where it pays off — long patents / standards, or
when the caller asks for it — and walks each top document's ToC tree with the
navigator, returning the sections it read and why.

Documents without a tree, or below the page threshold, keep their stage-1
chunks unchanged, so turning PageIndex on can only add evidence.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ...config import get_settings
from .navigator import LLMJson, NavigationResult, navigate
from .tree_builder import BUILDER_VERSION, build_toc_tree

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- build / store


def build_tree_for_source(source_id: str, *, tables: list | None = None) -> dict[str, Any] | None:
    """(Re)build and store the ToC tree of one ingested source. Returns build stats, or None."""
    from ...db.chunk_store import get_chunk_store
    from ...db.source_store import get_source_store
    from ...db.toc_tree_store import get_toc_tree_store

    chunks = get_chunk_store().get_by_source(source_id)
    if not chunks:
        get_toc_tree_store().delete(source_id)
        return None
    if tables is None:
        tables = _load_tables(source_id)
    doc = get_source_store().get(source_id)
    built = build_toc_tree(chunks, title=(doc.title if doc else "") or "", tables=tables)
    get_toc_tree_store().put(source_id, built, builder_version=BUILDER_VERSION)
    return {"source_id": source_id, "doc_kind": built["doc_kind"], **built["stats"]}


def build_tree_on_ingest(source_id: str, tables: list | None = None) -> None:
    """Ingest hook: never raises — a missing tree only disables stage 2 for this document."""
    settings = get_settings()
    if not (settings.pageindex_enabled and settings.pageindex_build_on_ingest):
        return
    try:
        build_tree_for_source(source_id, tables=tables)
    except Exception as exc:
        log.warning("pageindex: tree build failed for %s: %s", source_id, exc)


def _load_tables(source_id: str) -> list:
    try:
        from ..table_contract import load_tables

        return list(load_tables(source_id) or [])
    except Exception:
        return []


# --------------------------------------------------------------------------- node text


class _NodeText:
    """Loads a node's text from its chunks (and its tables' Markdown), once per document."""

    def __init__(self, source_id: str) -> None:
        from ...db.chunk_store import get_chunk_store

        self._chunks = {c.id: c.text or "" for c in get_chunk_store().get_by_source(source_id)}
        self._tables: dict[str, str] | None = None
        self._source_id = source_id

    def _table_md(self) -> dict[str, str]:
        if self._tables is None:
            self._tables = {}
            for t in _load_tables(self._source_id):
                tid = getattr(t, "table_id", None) or (t.get("table_id") if isinstance(t, dict) else None)
                md = getattr(t, "raw_markdown", None) or (t.get("raw_markdown") if isinstance(t, dict) else "")
                if tid and md:
                    self._tables[str(tid)] = md
        return self._tables

    def __call__(self, node: dict[str, Any]) -> str:
        parts = [self._chunks.get(cid, "") for cid in node.get("chunk_ids") or []]
        body = "\n".join(p for p in parts if p)
        if node.get("table_ids"):
            tables = self._table_md()
            for tid in node["table_ids"]:
                md = tables.get(tid)
                if md and md not in body:
                    body = f"{body}\n{md}" if body else md
        return body


# --------------------------------------------------------------------------- retrieval


@dataclass
class DocumentDepth:
    source_id: str
    mode: str  # pageindex | stage1
    reason: str
    stage1_chunk_ids: list[str] = field(default_factory=list)
    navigation: NavigationResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id, "mode": self.mode, "reason": self.reason,
            "stage1_chunk_ids": self.stage1_chunk_ids,
            "navigation": self.navigation.to_dict() if self.navigation else None,
        }


@dataclass
class TwoStageResult:
    query: str
    stage1: list[Any] = field(default_factory=list)  # DocumentChunkResponse
    documents: list[DocumentDepth] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "stage1": [c.model_dump() if hasattr(c, "model_dump") else c for c in self.stage1],
            "documents": [d.to_dict() for d in self.documents],
        }


def _default_llm() -> LLMJson | None:
    settings = get_settings()
    if not settings.get_active_api_key():
        return None
    from ..llm import complete_json

    return complete_json


def deepen_documents(
    query: str,
    source_ids: list[str],
    *,
    deep: bool | None = None,
    llm: LLMJson | None = None,
    use_llm: bool = True,
    stage1_chunks: dict[str, list[str]] | None = None,
) -> list[DocumentDepth]:
    """Stage 2 over already-chosen documents (also used by embodiment drafts / deep research)."""
    from ...db.toc_tree_store import get_toc_tree_store

    settings = get_settings()
    trees = get_toc_tree_store().get_many(source_ids) if settings.pageindex_enabled else {}
    model = llm if llm is not None else (_default_llm() if use_llm else None)
    out: list[DocumentDepth] = []
    for sid in source_ids:
        ids = (stage1_chunks or {}).get(sid, [])
        row = trees.get(sid)
        if deep is False:
            out.append(DocumentDepth(sid, "stage1", "deep=false", ids))
            continue
        if row is None:
            out.append(DocumentDepth(sid, "stage1", "no toc tree", ids))
            continue
        if deep is None and row.page_count < settings.pageindex_min_pages:
            out.append(DocumentDepth(sid, "stage1", f"{row.page_count} pages < {settings.pageindex_min_pages}", ids))
            continue
        nav = navigate(
            sid, row.tree, query, _NodeText(sid), llm=model,
            max_hops=settings.pageindex_max_hops, max_chars=settings.pageindex_max_input_chars,
        )
        out.append(DocumentDepth(sid, "pageindex", "deep=true" if deep else f"{row.page_count} pages", ids, nav))
    return out


def two_stage_retrieve(
    query: str,
    *,
    top_k: int = 10,
    max_docs: int = 3,
    project_id: str | None = None,
    include_global: bool = True,
    deep: bool | None = None,
    llm: LLMJson | None = None,
    use_llm: bool = True,
) -> TwoStageResult:
    """Stage 1 ``hybrid_search`` → stage 2 PageIndex navigation on the top documents.

    ``deep``: ``None`` = automatic (documents with at least ``pageindex_min_pages`` pages),
    ``True`` = every top document that has a tree, ``False`` = stage 1 only.
    """
    from ..hybrid_search import hybrid_search

    stage1 = hybrid_search(query, top_k=top_k, project_id=project_id, include_global=include_global)
    by_doc: dict[str, list[str]] = {}
    for c in stage1:
        by_doc.setdefault(c.source_id, []).append(c.id)
    top_docs = list(by_doc)[:max_docs]
    docs = deepen_documents(query, top_docs, deep=deep, llm=llm, use_llm=use_llm, stage1_chunks=by_doc)
    return TwoStageResult(query=query, stage1=stage1, documents=docs)


def navigation_evidence(depths: list[DocumentDepth], *, titles: dict[str, str] | None = None,
                        snippet_chars: int = 1600) -> list[Any]:
    """Stage-2 reads as page-anchored ``Evidence`` (for deep research / chat citations)."""
    from ...domain.schemas import Evidence

    out: list[Evidence] = []
    for d in depths:
        if d.navigation is None:
            continue
        n = len(d.navigation.evidence)
        for i, ev in enumerate(d.navigation.evidence):
            pr = ev.get("page_range") or [None, None]
            title = (titles or {}).get(d.source_id) or d.source_id
            out.append(Evidence(
                source="kb_pageindex",
                identifier=f"{d.source_id}#{ev['node_id']}",
                title=f"{title} · {ev.get('title') or ''}".strip(" ·"),
                snippet=(ev.get("text") or "")[:snippet_chars],
                # Visit order is the navigator's own ranking: earlier reads matter more.
                relevance=round(max(0.3, 0.9 - 0.6 * i / max(1, n)), 3),
                page=pr[0], page_end=pr[1] if pr[1] != pr[0] else None,
            ))
    return out
