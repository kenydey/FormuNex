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


@router.get("/sources/{source_id}/toc-tree")
def get_toc_tree(source_id: str) -> dict[str, Any]:
    from ..db.toc_tree_store import get_toc_tree_store

    row = get_toc_tree_store().get(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="该文档尚无目录树（未入库或未构建）")
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


@router.post("/pageindex/search")
def pageindex_search(body: TwoStageSearchRequest) -> dict[str, Any]:
    """Stage 1 hybrid search → stage 2 ToC-tree navigation with an auditable reasoning trace."""
    if not kb_index.kb_enabled():
        raise HTTPException(status_code=409, detail="知识库 v2 未启用")
    from ..services.pageindex.two_stage import two_stage_retrieve

    return two_stage_retrieve(
        body.query, top_k=body.top_k, max_docs=body.max_docs, project_id=body.project_id,
        include_global=body.include_global, deep=body.deep, use_llm=body.use_llm,
    ).to_dict()
