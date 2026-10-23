"""PageIndex endpoints: per-document ToC trees and two-stage retrieval."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import kb_index

router = APIRouter(prefix="/kb", tags=["kb", "pageindex"])


class TwoStageSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=50)
    max_docs: int = Field(default=3, ge=1, le=10)
    project_id: str | None = None
    include_global: bool = True
    # None = automatic (long documents only); True = every top document with a tree; False = stage 1 only.
    deep: bool | None = None
    use_llm: bool = True
    # Caller context for the Stage-2 gate (plan §5.1.1 限定场景): "auto" = classify
    # the query; "extraction" / "deep_research" / "explicit" bypass the classifier.
    query_kind: str = Field(default="auto", pattern="^(auto|extraction|deep_research|explicit)$")


@router.get("/sources/{source_id}/toc-tree")
def get_toc_tree(source_id: str) -> dict[str, Any]:
    from ..db.toc_tree_store import get_toc_tree_store

    row = get_toc_tree_store().get(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="该文档尚无目录树（未入库或未构建）")
    # Viewing a long document's tree is a summary trigger (plan §5.1.1):
    # ≥ pageindex_min_pages pages + never summarised → schedule node summaries (fail-open).
    try:
        from ..services.pageindex.summaries import maybe_schedule_summaries

        maybe_schedule_summaries(source_id)
    except Exception:
        pass  # scheduling must never break a read
    row = get_toc_tree_store().get(source_id) or row
    return {
        "source_id": row.source_id, "doc_kind": row.doc_kind, "builder_version": row.builder_version,
        "node_count": row.node_count, "max_depth": row.max_depth, "page_count": row.page_count,
        "summaries_status": row.summaries_status, "built_at": row.built_at, "tree": row.tree,
    }


@router.post("/sources/{source_id}/toc-tree/rebuild")
def rebuild_toc_tree(source_id: str) -> dict[str, Any]:
    from ..services.pageindex.two_stage import build_tree_for_source

    stats = build_tree_for_source(source_id)
    if stats is None:
        raise HTTPException(status_code=404, detail="该文档没有已入库的切块")
    return stats


@router.post("/sources/{source_id}/toc-tree/summarize", status_code=202)
def summarize_toc_tree(source_id: str):
    """Trigger background LLM summaries for one document's ToC nodes.

    Cost guard: only the explicitly requested document is summarised, never a
    full-library sweep. The row moves to ``pending`` now; the Celery job flips
    it to ``done`` / ``partial``. Poll ``GET .../toc-tree`` for the result.
    """
    from ..db.toc_tree_store import get_toc_tree_store

    store = get_toc_tree_store()
    row = store.get(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="该文档尚无目录树（未入库或未构建）")
    if row.summaries_status == "done":
        return {"source_id": source_id, "summaries_status": "done", "detail": "节点摘要已全部生成"}
    previous = row.summaries_status
    store.set_summaries_status(source_id, "pending")
    from ..worker.tasks import run_toc_summaries_task
    from ._dispatch import submit

    try:
        return submit(run_toc_summaries_task, {"source_id": source_id}, kind="toc_summaries")
    except HTTPException:
        store.set_summaries_status(source_id, previous)  # not queued: leave it to the next trigger
        raise


@router.post("/pageindex/search")
def pageindex_search(body: TwoStageSearchRequest) -> dict[str, Any]:
    """Stage 1 hybrid search → stage 2 ToC-tree navigation with an auditable reasoning trace."""
    if not kb_index.kb_enabled():
        raise HTTPException(status_code=409, detail="知识库 v2 未启用")
    from ..services.pageindex.two_stage import two_stage_retrieve

    return two_stage_retrieve(
        body.query, top_k=body.top_k, max_docs=body.max_docs, project_id=body.project_id,
        include_global=body.include_global, deep=body.deep, use_llm=body.use_llm,
        query_kind=body.query_kind,
    ).to_dict()
