"""PageIndex 评估（补齐计划 1-10）：配对准确率评估框架。

Honesty contract: pairing annotations must come from real patents + real human
judgement. This module runs the framework on the existing 3 *synthetic*
patents (a smoke check, NOT the ≥20pp target measurement) and reports the
real 30-patent annotated set as missing until a human annotates it. No
annotations are fabricated here.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from app.services.pageindex import two_stage

# Real human annotations live here when they exist. Absent = not yet annotated.
REAL_ANNOTATIONS_PATH = Path(__file__).parent / "pageindex_real_annotations.json"
REAL_SET_TARGET = 30


def load_real_annotations() -> list[dict] | None:
    """Return the real annotated set, or None when it does not exist yet."""
    if not REAL_ANNOTATIONS_PATH.is_file():
        return None
    data = json.loads(REAL_ANNOTATIONS_PATH.read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    return cases or None


def eval_pairing_accuracy(cases: list[dict], *, use_llm: bool = False) -> dict:
    """Run stage-2 vs stage-1 baseline on annotated cases.

    Each case: ``{query, source_id, expected_labels}`` where expected_labels
    are the ToC node labels / titles a correct walk must read (recipe +
    performance + definition pairing). Returns honest metrics — no target
    claim is made here; the caller reports them.
    """
    from app.db.toc_tree_store import get_toc_tree_store
    from app.services.pageindex.tree_builder import index_nodes

    trees = get_toc_tree_store()
    total = stage2_hits = stage1_hits = 0
    hops: list[int] = []
    lat_ms: list[float] = []
    page_hits = 0
    for case in cases:
        t0 = time.perf_counter()
        result = two_stage.two_stage_retrieve(case["query"], top_k=5, use_llm=use_llm)
        lat_ms.append((time.perf_counter() - t0) * 1000.0)
        depth = next((d for d in result.documents if d.source_id == case["source_id"]), None)
        if depth is None:
            continue
        total += 1
        expected = set(case["expected_labels"])
        tree_nodes = index_nodes(trees.get(case["source_id"]).tree)
        if depth.navigation is not None:
            read = {
                (tree_nodes[n].get("label") or tree_nodes[n]["title"])
                for n in depth.navigation.node_path if n in tree_nodes
            }
            hops.append(depth.navigation.hops)
            stage2_hits += all(any(e == r or e in r for r in read) for e in expected)
            pages = {p for e in depth.navigation.evidence for p in (e.get("page_range") or []) if p}
            page_hits += bool(pages & set(case.get("expected_pages") or pages))
        hit_nodes = {
            n.get("label") or n["title"] for n in tree_nodes.values()
            if set(n.get("chunk_ids") or []) & set(depth.stage1_chunk_ids)
        }
        stage1_hits += all(any(e == r or e in r for r in hit_nodes) for e in expected)
    return {
        "cases": total,
        "stage2_pairing_accuracy": (stage2_hits / total) if total else None,
        "stage1_pairing_accuracy": (stage1_hits / total) if total else None,
        "pairing_delta_pp": (
            (stage2_hits - stage1_hits) / total * 100.0 if total else None
        ),
        "page_hit_rate": (page_hits / total) if total else None,
        "avg_hops": (sum(hops) / len(hops)) if hops else None,
        "avg_latency_ms": (sum(lat_ms) / len(lat_ms)) if lat_ms else None,
    }


@pytest.fixture()
def pageindex_kb(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.source_store as source_store_mod
    import app.db.toc_tree_store as toc_store_mod
    from app.config import get_settings
    from app.db.chunk_store import ChunkStore
    from app.db.database import Base, make_engine, make_session_factory
    from app.db.source_store import SourceStore
    from app.db.toc_tree_store import TocTreeStore
    from app.services import kb_index
    from tests.pageindex_fixtures import ALL_PATENTS

    for key, value in {
        "FORMUNEX_API_AUTH_ENABLED": "false",
        "FORMUNEX_CROSSLINGUAL_EXPAND_ENABLED": "false",
        "FORMUNEX_QUERY_REWRITE_ON_EMPTY_ENABLED": "false",
        "FORMUNEX_KG_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    sources = SourceStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", sources)
    monkeypatch.setattr(chunk_store_mod, "_store", ChunkStore(factory))
    monkeypatch.setattr(toc_store_mod, "_store", TocTreeStore(factory))

    cases: list[dict] = []
    for make in ALL_PATENTS:
        p = make()
        sid = sources.create(filename=f"{p.title}.pdf", title=p.title, source_kind="patent",
                             full_text=p.page_marked_text(),
                             content_hash=hashlib.sha256(p.page_marked_text().encode()).hexdigest())
        assert kb_index.index_source(sid, p.page_marked_text(), embed=False) > 0
        for query, expected in p.queries.items():
            cases.append({"query": query, "source_id": sid, "expected_labels": sorted(expected),
                          "expected_pages": []})
    yield cases
    get_settings.cache_clear()


def test_real_30_patent_set_is_missing_until_human_annotated():
    """The ≥20pp target can only be measured on real annotated patents.

    If someone fabricates a file to make the target look measured, the
    provenance check below rejects it: every real case must carry an
    ``annotator`` + ``annotated_at``.
    """
    cases = load_real_annotations()
    if cases is None:
        pytest.skip("real 30-patent annotated set not yet built (needs human annotation)")
    assert len(cases) >= REAL_SET_TARGET, f"real set has {len(cases)} < {REAL_SET_TARGET} cases"
    for c in cases:
        assert c.get("annotator"), "every real case must name its human annotator"
        assert c.get("annotated_at"), "every real case must carry annotated_at"
        assert c.get("expected_labels"), "every real case must list expected labels"


def test_eval_framework_runs_on_synthetic_patents(pageindex_kb):
    """Smoke check on the 3 synthetic patents: framework runs, metrics honest.

    Synthetic results are NOT the ≥20pp target measurement — they only prove
    the harness works end to end.
    """
    metrics = eval_pairing_accuracy(pageindex_kb)
    assert metrics["cases"] == 4  # 3 patents carry 4 labelled queries
    assert metrics["stage2_pairing_accuracy"] == 1.0
    assert metrics["pairing_delta_pp"] is not None
    assert metrics["avg_hops"] is not None and metrics["avg_hops"] > 0
    assert metrics["avg_latency_ms"] is not None
