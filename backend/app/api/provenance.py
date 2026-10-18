"""Provenance lineage API (W3-10, Phase 2D 2-23) — read-only BFS over provenance edges.

``GET /lineage``: upstream BFS from any node (the original formulation-level view).
``GET /product-chain``: the product-level full-chain view — downstream from
the product (revisions → documents / release packages) plus each revision's
upstream chain (experiment / formulation version / formulation / project).

The underlying service is fail-open: DB errors yield an empty edge list,
while an unknown node type is a 400.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db.database import default_session_factory
from ..db.models import ExperimentRow, FormulationVersion
from ..db.models_plm import ProductRevisionRow, ProductRow, RegulatoryDocumentRow
from ..services import provenance as prov_svc

router = APIRouter(prefix="/api/provenance", tags=["provenance"])

#: Which chain stage a node kind belongs to (for the frontend's stage grouping).
_STAGE = {
    "source": "research",
    "claim": "research",
    "formulation": "research",
    "project": "research",
    "run": "doe",
    "experiment": "doe",
    "formulation_version": "winning",
    "product_revision": "winning",
    "product": "plm",
    "document": "plm",
    "release_package": "plm",
    "batch": "plm",
}


@router.get("/lineage", response_model=dict)
def get_lineage(
    node_type: str = Query(..., min_length=1, max_length=32),
    node_id: str = Query(..., min_length=1, max_length=512),
    depth: int = Query(default=3, ge=1, le=6),
):
    try:
        edges = prov_svc.lineage(node_type, node_id, depth=depth)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "node_type": node_type,
        "node_id": node_id,
        "depth": depth,
        "edges": edges,
    }


def _labels(session, node_keys: set[tuple[str, str]]) -> dict[str, str]:
    """Display names for the nodes in the chain; falls back to the raw id."""
    labels: dict[str, str] = {}
    by_type: dict[str, list[str]] = {}
    for node_type, node_id in node_keys:
        by_type.setdefault(node_type, []).append(node_id)
    for pid in by_type.get("product", []):
        row = session.get(ProductRow, pid)
        if row is not None:
            labels[f"product:{pid}"] = row.name_zh or row.code or pid
    for rid in by_type.get("product_revision", []):
        row = session.get(ProductRevisionRow, rid)
        if row is not None:
            labels[f"product_revision:{rid}"] = f"修订版 {row.rev}"
    for did in by_type.get("document", []):
        row = session.get(RegulatoryDocumentRow, did)
        if row is not None:
            labels[f"document:{did}"] = f"{row.doc_type.upper()} ({row.lang}) v{row.version_no}"
    for vid in by_type.get("formulation_version", []):
        row = session.get(FormulationVersion, vid)
        if row is not None:
            labels[f"formulation_version:{vid}"] = f"{row.name} v{row.version}"
    for eid in by_type.get("experiment", []):
        if eid.isdigit():
            row = session.get(ExperimentRow, int(eid))
            if row is not None:
                labels[f"experiment:{eid}"] = f"实验 #{row.id}"
    return labels


@router.get("/product-chain", response_model=dict)
def get_product_chain(
    product_id: str = Query(..., min_length=1, max_length=64),
):
    """Full-chain traceability for one product: downstream from the product
    (revisions → documents / release packages) plus each revision's upstream
    chain (experiment / formulation version / formulation / project)."""
    sf = default_session_factory()
    with sf() as session:
        if session.get(ProductRow, product_id) is None:
            raise HTTPException(status_code=404, detail="产品不存在")
        edges: list[dict] = []
        seen: set[tuple[str, str, str, str, str]] = set()

        def _add(new_edges: list[dict]) -> None:
            for e in new_edges:
                key = (e["from_type"], e["from_id"], e["to_type"], e["to_id"], e["relation"])
                if key not in seen:
                    seen.add(key)
                    edges.append(e)

        _add(prov_svc.downstream("product", product_id, depth=4, session_factory=sf))
        revision_ids = sorted({e["to_id"] for e in edges if e["to_type"] == "product_revision"})
        for rid in revision_ids:
            _add(prov_svc.lineage("product_revision", rid, depth=6, session_factory=sf))
        node_keys: set[tuple[str, str]] = {("product", product_id)}
        for e in edges:
            node_keys.add((e["from_type"], e["from_id"]))
            node_keys.add((e["to_type"], e["to_id"]))
        labels = _labels(session, node_keys)
        nodes = [
            {
                "node_type": node_type,
                "node_id": node_id,
                "stage": _STAGE.get(node_type, "research"),
                "label": labels.get(f"{node_type}:{node_id}", node_id),
            }
            for node_type, node_id in sorted(node_keys)
        ]
        return {"product_id": product_id, "nodes": nodes, "edges": edges}
