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

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ...config import get_settings
from .navigator import LLMJson, NavigationResult, navigate
from .tree_builder import BUILDER_VERSION, build_toc_tree

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- result cache (plan 3-11)
#
# ``two_stage_retrieve`` is LLM-expensive (stage-2 navigation per document),
# so results are cached in-process keyed by (normalised query, retrieval
# params, corpus fingerprint). Invalidation rules:
#   * any SourceDocument in the query's scope changes (insert / update /
#     delete) → the corpus fingerprint moves → old entries miss;
#   * fingerprint unavailable (DB error, …) → fail-open: bypass the cache,
#     never serve a possibly-stale hit;
#   * entries older than ``_PAGEINDEX_CACHE_TTL_S`` are dropped on read;
#   * cache is per-process: a multi-worker deployment keeps one cache per
#     worker (stale at most TTL after a doc change the fingerprint missed —
#     see the fingerprint caveat below).
#
# Fingerprint: a hash of the state two_stage_retrieve actually reads —
# the chunk store (engine + write generation + counts) and the ToC-tree
# store (engine + tree-content hash). It deliberately does NOT read the
# SourceDocument table: retrieval never reads it (titles reach stage 2 via
# the stored trees), and the default-DB fingerprint missed stores swapped
# onto another engine (tests) entirely. Store identity comes from the
# engine URL, not id(): a swapped-in store on another database must never
# collide with the previous store's fingerprint.
_PAGEINDEX_CACHE: dict[str, tuple[float, "TwoStageResult"]] = {}
_PAGEINDEX_CACHE_LOCK = threading.Lock()
_PAGEINDEX_CACHE_TTL_S = 3600
_PAGEINDEX_CACHE_MAX = 256


def _engine_url(factory: Any) -> str:
    """Stable identity for a session factory: its engine URL (id() fallback)."""
    try:
        bind = getattr(factory, "bind", None)
        if bind is not None:
            return str(bind.url)
    except Exception:  # noqa: BLE001 - fall through to id()
        pass
    return f"id:{id(factory)}"


def corpus_fingerprint(*, project_id: str | None = None, include_global: bool = True) -> str | None:
    """Fingerprint of the corpus state ``two_stage_retrieve`` reads.

    ``project_id`` / ``include_global`` are accepted for call-site symmetry
    but do not narrow the fingerprint: the chunk/tree stores are not
    project-scoped, so narrowing would risk serving a hit after an
    out-of-scope write changed shared state. The fingerprint is
    conservative (extra invalidation, never stale).

    Returns ``None`` when the stores are unreachable (caller bypasses cache).
    """
    try:
        from ...db.chunk_store import get_chunk_store
        from ...db.toc_tree_store import get_toc_tree_store

        chunk_store = get_chunk_store()
        toc_store = get_toc_tree_store()
        raw = "|".join([
            _engine_url(chunk_store.session_factory),
            str(chunk_store.generation),
            str(chunk_store.counts()),
            _engine_url(toc_store._factory),
            toc_store.fingerprint(),
        ])
        return hashlib.sha1(raw.encode()).hexdigest()
    except Exception as exc:  # noqa: BLE001 - fingerprint failure must never break retrieval
        log.warning("pageindex cache fingerprint unavailable: %s", exc)
        return None


def invalidate_pageindex_cache() -> int:
    """Drop every cached PageIndex result; returns the number dropped."""
    with _PAGEINDEX_CACHE_LOCK:
        n = len(_PAGEINDEX_CACHE)
        _PAGEINDEX_CACHE.clear()
        return n


def _settings_snapshot() -> str:
    """PageIndex settings that change ``two_stage_retrieve`` output."""
    s = get_settings()
    return "|".join(str(getattr(s, k, None)) for k in (
        "pageindex_enabled", "pageindex_colbert_rerank", "pageindex_min_pages",
        "pageindex_max_hops", "pageindex_max_input_chars", "pageindex_max_input_tokens"))


def _cache_key(query: str, *, top_k: int, max_docs: int, project_id: str | None,
               include_global: bool, deep: bool | None, use_llm: bool, query_kind: str,
               fingerprint: str) -> str:
    params = "|".join([query.strip().lower(), str(top_k), str(max_docs), str(project_id),
                       str(include_global), str(deep), str(use_llm), query_kind, fingerprint,
                       _settings_snapshot()])
    return hashlib.sha1(params.encode()).hexdigest()


def _cache_get(key: str) -> "TwoStageResult | None":
    with _PAGEINDEX_CACHE_LOCK:
        hit = _PAGEINDEX_CACHE.get(key)
        if hit is None:
            return None
        at, result = hit
        if time.monotonic() - at > _PAGEINDEX_CACHE_TTL_S:
            del _PAGEINDEX_CACHE[key]
            return None
        return result


def _cache_put(key: str, result: "TwoStageResult") -> None:
    with _PAGEINDEX_CACHE_LOCK:
        if len(_PAGEINDEX_CACHE) >= _PAGEINDEX_CACHE_MAX:
            _PAGEINDEX_CACHE.pop(next(iter(_PAGEINDEX_CACHE)))
        _PAGEINDEX_CACHE[key] = (time.monotonic(), result)


# --------------------------------------------------------------------------- query-type gate (plan §5.1.1 限定场景)


# 实施例萃取：问“某个实施例/配方的组成、对应性能、配对关系”。
_EXTRACTION_RE = re.compile(
    r"实施例|对比例|embodiment|example|权利要求|claim|配方表|性能表|配对|对比|对应|组成|盐雾|"
    r"formulation|composition|performance|salt.?spray",
    re.I,
)
# 深度研究：问“全面/系统/综述/机制”类长问题。
_DEEP_RE = re.compile(
    r"深度|全面|系统|综述|调研|分析|机制|影响因素|研究进展|发展趋势|"
    r"review|survey|analysis|mechanism|systematic",
    re.I,
)
# 事实型短查询：定义、查值、查身份——普通检索即可，Stage 2 不划算。
_FACTOID_RE = re.compile(r"是什么|定义|含义|指什么|多少钱|价格|供应商|哪家|CAS ?号|电话|地址", re.I)

#: Known caller contexts that bypass the query classifier (the caller already
#: knows the query belongs to a PageIndex scenario).
QUERY_KINDS = ("auto", "extraction", "deep_research", "explicit")


def classify_pageindex_query(query: str) -> tuple[bool, str]:
    """Plan §5.1.1 限定场景：Stage 2 只对实施例萃取 / 深度研究类查询触发。

    短查询 / 事实型查询返回 False——它们用 Stage 1 普通检索即可，逐文档
    LLM 遍历（3~8 次调用、5~20 s）不划算。``deep=True``（用户显式开启）
    与已知挂载点（``query_kind``）不走此分类器。
    """
    q = (query or "").strip()
    if not q:
        return False, "empty query"
    # Short keyword queries ("前体A 盐雾") are lookups, not extraction —
    # unless they carry a strong extraction/deep marker (实施例/配对/对应…).
    _STRONG_RE = re.compile(
        r"实施例|对比例|embodiment|权利要求|claim|配对|对比|对应|组成|结果|"
        r"深度|综述|调研|分析|机制",
        re.I,
    )
    if len(q) <= 8 and not _STRONG_RE.search(q):
        return False, "short query (factoid lookup)"
    if _DEEP_RE.search(q):
        return True, "deep research query"
    if _EXTRACTION_RE.search(q):
        return True, "extraction query"
    if len(q) <= 8:
        return False, "short query (factoid lookup)"
    if _FACTOID_RE.search(q):
        return False, "factoid query"
    return False, "generic query (not a PageIndex scenario)"


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
        built = build_tree_for_source(source_id, tables=tables)
    except Exception as exc:
        log.warning("pageindex: tree build failed for %s: %s", source_id, exc)
        return
    if built is None:
        return
    try:
        from .summaries import schedule_summaries_on_ingest

        schedule_summaries_on_ingest(source_id)
    except Exception as exc:
        log.warning("pageindex: summary scheduling failed for %s: %s", source_id, exc)


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
    # Plan §5.1.1 降级：无树或预算耗尽时回退 children_retrieval；这里是
    # children 重排后的 stage-1 chunk id（按重排后顺序），供调用方追加证据。
    fallback: str | None = None
    fallback_chunk_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id, "mode": self.mode, "reason": self.reason,
            "stage1_chunk_ids": self.stage1_chunk_ids,
            "navigation": self.navigation.to_dict() if self.navigation else None,
            "fallback": self.fallback, "fallback_chunk_ids": self.fallback_chunk_ids,
        }


@dataclass
class TwoStageResult:
    query: str
    stage1: list[Any] = field(default_factory=list)  # DocumentChunkResponse
    documents: list[DocumentDepth] = field(default_factory=list)
    # Plan §5.1.1 output: the best stage-2 answer across documents (None when
    # no document navigated). Per-document answers live in documents[].navigation.
    answer: str | None = None
    answer_source: str = "none"
    stage1_reranked: bool = False  # ColBERT rerank applied to stage 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "stage1": [c.model_dump() if hasattr(c, "model_dump") else c for c in self.stage1],
            "documents": [d.to_dict() for d in self.documents],
            "answer": self.answer, "answer_source": self.answer_source,
            "stage1_reranked": self.stage1_reranked,
        }


def _default_llm() -> LLMJson | None:
    settings = get_settings()
    if not settings.get_active_api_key():
        return None
    from ..llm import complete_json

    return complete_json


# --------------------------------------------------------------------------- fallback / summaries / rerank


@dataclass
class _SimpleScored:
    """Stage-1 chunk adapted to ``children_rerank``'s scored-chunk protocol."""
    id: str
    text: str
    source_id: str
    page: int | None = None
    heading_path: list[str] = field(default_factory=list)
    relevance: float = 0.0
    chunk: Any = None  # original stage-1 chunk


def _children_fallback(query: str, stage1: list[Any]) -> list[str]:
    """Plan §5.1.1 降级：对 stage-1 chunk 做 children_retrieval 子块重排，返回 chunk id 顺序。

    children_retrieval 会切分长 chunk 并打分，只对最相关的子块投票；
    fail-open：任何异常 → 返回 []，调用方只用 stage-1 结果。
    """
    if not stage1:
        return []
    try:
        from ..children_retrieval import children_rerank

        scored = [_SimpleScored(id=c.id, text=c.text or "", source_id=c.source_id,
                                page=getattr(c, "page", None),
                                heading_path=list(getattr(c, "heading_path", None) or []),
                                relevance=getattr(c, "relevance", 0.0) or 0.0, chunk=c)
                   for c in stage1]
        ranked = children_rerank(query, scored)
        return [s.chunk.id for s in ranked]
    except Exception as exc:
        log.warning("pageindex: children fallback failed: %s", exc)
        return []


def _schedule_summaries_if_eligible(source_id: str) -> None:
    """Schedule node summaries for a Stage-2-visited document (fail-open)."""
    try:
        from .summaries import maybe_schedule_summaries

        maybe_schedule_summaries(source_id, visited_by_stage2=True)
    except Exception as exc:
        log.warning("pageindex: summary scheduling failed for %s: %s", source_id, exc)


def _colbert_rerank_stage1(query: str, stage1: list[Any]) -> tuple[list[Any], bool]:
    """Plan §5.1.1 stage 1 = hybrid_search + ColBERT rerank (existing colbert_store stack).

    Fail-open: rerank off / empty / failure → original order, applied=False.
    """
    settings = get_settings()
    if not settings.pageindex_colbert_rerank or not stage1:
        return stage1, False
    try:
        from ...domain.schemas import Evidence
        from ..colbert_store import rerank_candidates

        candidates = [
            Evidence(
                source="kb", identifier=c.id, title="",
                snippet=(c.text or "")[:2000],
                relevance=getattr(c, "relevance", 0.0) or 0.0,
            )
            for c in stage1
        ]
        reordered, meta = rerank_candidates(query, candidates)
        if not meta.get("applied"):
            return stage1, False
        order = [e.identifier for e in reordered]
        by_id = {c.id: c for c in stage1}
        ranked = [by_id[i] for i in order if i in by_id]
        ranked.extend(c for c in stage1 if c not in ranked)
        return ranked, True
    except Exception as exc:
        log.warning("pageindex: ColBERT stage-1 rerank failed: %s", exc)
        return stage1, False


def deepen_documents(
    query: str,
    source_ids: list[str],
    *,
    deep: bool | None = None,
    llm: LLMJson | None = None,
    use_llm: bool = True,
    stage1_chunks: dict[str, list[str]] | None = None,
    stage1: list[Any] | None = None,
    query_kind: str = "auto",
) -> list[DocumentDepth]:
    """Stage 2 over already-chosen documents (also used by embodiment drafts / deep research).

    Gating (plan §5.1.1 限定场景): Stage 2 fires only when the document has
    ≥ ``pageindex_min_pages`` pages **and** the query is a PageIndex scenario
    (extraction / deep-research) — unless ``deep is True`` (explicit opt-in)
    or ``query_kind`` is a known caller context (``deep_research`` /
    ``extraction`` / ``explicit``), which bypass the classifier. The fallback
    for gated / tree-less / budget-exhausted documents is children_retrieval.
    """
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
            fb = _children_fallback(query, [c for c in (stage1 or []) if c.source_id == sid])
            out.append(DocumentDepth(sid, "stage1", "no toc tree", ids,
                                     fallback="children_retrieval", fallback_chunk_ids=fb))
            continue
        if deep is None and row.page_count < settings.pageindex_min_pages:
            out.append(DocumentDepth(sid, "stage1", f"{row.page_count} pages < {settings.pageindex_min_pages}", ids))
            continue
        if deep is None and query_kind == "auto":
            eligible, why = classify_pageindex_query(query)
            if not eligible:
                out.append(DocumentDepth(sid, "stage1", f"not eligible: {why}", ids))
                continue
        nav = navigate(
            sid, row.tree, query, _NodeText(sid), llm=model,
            max_hops=settings.pageindex_max_hops, max_chars=settings.pageindex_max_input_chars,
            max_tokens=settings.pageindex_max_input_tokens,
        )
        depth = DocumentDepth(sid, "pageindex", "deep=true" if deep else f"{row.page_count} pages", ids, nav)
        if nav.stopped == "budget":
            # Budget-exhausted navigation may have missed the best section:
            # children rerank over stage-1 chunks is the supplementary signal.
            fb = _children_fallback(query, [c for c in (stage1 or []) if c.source_id == sid])
            depth.fallback = "children_retrieval"
            depth.fallback_chunk_ids = fb
        out.append(depth)
        _schedule_summaries_if_eligible(sid)
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
    query_kind: str = "auto",
    use_cache: bool = True,
) -> TwoStageResult:
    """Stage 1 ``hybrid_search`` (+ ColBERT rerank) → stage 2 PageIndex navigation on the top documents.

    ``deep``: ``None`` = automatic (documents with at least ``pageindex_min_pages`` pages),
    ``True`` = every top document that has a tree, ``False`` = stage 1 only.
    ``query_kind``: ``"auto"`` = classify the query; a known context
    (``extraction`` / ``deep_research`` / ``explicit``) bypasses the classifier.
    ``use_cache``: plan 3-11 — serve the cached result when (query, params,
    corpus fingerprint) match; a cache hit returns the *same*
    ``TwoStageResult`` object (callers must treat it as read-only).
    """
    from ..hybrid_search import hybrid_search

    cache_key: str | None = None
    if use_cache:
        fingerprint = corpus_fingerprint(project_id=project_id, include_global=include_global)
        if fingerprint is not None:
            cache_key = _cache_key(query, top_k=top_k, max_docs=max_docs, project_id=project_id,
                                   include_global=include_global, deep=deep, use_llm=use_llm,
                                   query_kind=query_kind, fingerprint=fingerprint)
            hit = _cache_get(cache_key)
            if hit is not None:
                return hit
    stage1 = hybrid_search(query, top_k=top_k, project_id=project_id, include_global=include_global)
    stage1, reranked = _colbert_rerank_stage1(query, stage1)
    by_doc: dict[str, list[str]] = {}
    for c in stage1:
        by_doc.setdefault(c.source_id, []).append(c.id)
    top_docs = list(by_doc)[:max_docs]
    docs = deepen_documents(query, top_docs, deep=deep, llm=llm, use_llm=use_llm,
                            stage1_chunks=by_doc, stage1=stage1, query_kind=query_kind)
    answer = next((d.navigation.answer for d in docs if d.navigation and d.navigation.answer), None)
    source = next((d.navigation.answer_source for d in docs if d.navigation and d.navigation.answer), "none")
    result = TwoStageResult(query=query, stage1=stage1, documents=docs,
                            answer=answer, answer_source=source, stage1_reranked=reranked)
    if cache_key is not None:
        _cache_put(cache_key, result)
    return result


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
