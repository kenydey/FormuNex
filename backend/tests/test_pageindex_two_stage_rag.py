"""Two-stage hybrid RAG with PageIndex, end to end.

Synthetic long patents are ingested through the real ``kb_index.index_source``
(which now builds each document's ToC tree), retrieved with the real
``hybrid_search`` (BM25; no embedding model in CI), and walked by the
navigator. The central check is cross-page association: for questions whose
answer is split between a recipe table, a results table ten pages later and a
definition in 发明内容, stage 2 must read every part and say how it got there.
"""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.toc_tree_store import TocTreeStore
from app.services import kb_index
from app.services.pageindex import two_stage
from app.services.pageindex.navigator import navigate
from app.services.pageindex.tree_builder import index_nodes
from tests.pageindex_fixtures import ALL_PATENTS, patent_cn_basic


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    """Isolated KB (sources, chunks, ToC trees) with three long patents and one short note ingested."""
    import app.db.chunk_store as chunk_store_mod
    import app.db.source_store as source_store_mod
    import app.db.toc_tree_store as toc_store_mod
    from app.db.source_store import SourceStore

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
    trees = TocTreeStore(factory)
    monkeypatch.setattr(toc_store_mod, "_store", trees)

    def ingest(title: str, text: str) -> str:
        sid = sources.create(filename=f"{title}.pdf", title=title, source_kind="patent", full_text=text,
                             content_hash=hashlib.sha256(text.encode()).hexdigest())
        assert kb_index.index_source(sid, text, embed=False) > 0
        return sid

    patents = {}
    for make in ALL_PATENTS:
        p = make()
        patents[ingest(p.title, p.page_marked_text())] = p
    short = ingest("短篇技术说明", "\n".join(
        f"<!-- page:{i} -->\n第{i}页：前体A在实施例中的用量与盐雾测试结果的简要说明，供内部参考使用。" for i in (1, 2, 3)
    ))
    yield {"patents": patents, "short": short, "trees": trees, "sources": sources}
    get_settings.cache_clear()


def _labels_read(trees: TocTreeStore, nav) -> set[str]:
    nodes = index_nodes(trees.get(nav.source_id).tree)
    return {nodes[n].get("label") or nodes[n]["title"] for n in nav.node_path}


def _covers(read: set[str], expected: set[str]) -> bool:
    return all(any(e == r or e in r for r in read) for e in expected)


def test_ingest_builds_a_tree_per_document(kb):
    for sid, p in kb["patents"].items():
        row = kb["trees"].get(sid)
        assert row is not None and row.doc_kind == "patent"
        assert row.page_count >= 20
        labels = {n.get("label") for n in index_nodes(row.tree).values()}
        assert {p.recipe_label, p.performance_label} <= labels
    assert kb["trees"].get(kb["short"]).page_count == 3


def test_cross_page_association_accuracy(kb):
    """Every labelled question: stage 2 reads all the pieces; report against the stage-1 baseline."""
    total = stage2_hits = stage1_hits = 0
    for sid, p in kb["patents"].items():
        for query, expected in p.queries.items():
            total += 1
            result = two_stage.two_stage_retrieve(query, top_k=5, use_llm=False)
            depth = next(d for d in result.documents if d.source_id == sid)
            assert depth.mode == "pageindex", depth.reason  # ≥ 20 pages: automatic stage 2
            read = _labels_read(kb["trees"], depth.navigation)
            stage2_hits += _covers(read, expected)
            # Stage-1 baseline: the same question answered from the chunks hybrid search returned.
            tree_nodes = index_nodes(kb["trees"].get(sid).tree)
            hit_nodes = {
                n.get("label") or n["title"] for n in tree_nodes.values()
                if set(n.get("chunk_ids") or []) & set(depth.stage1_chunk_ids)
            }
            stage1_hits += _covers(hit_nodes, expected)
    assert total == 4
    assert stage2_hits / total == 1.0
    assert stage2_hits >= stage1_hits


def test_stage2_reaches_the_results_table_ten_pages_away(kb, monkeypatch):
    """Stage 1 only finds the recipe table; the walk must reach the results table via the tree."""
    sid = next(s for s, p in kb["patents"].items() if p.source_id == "pat-cn-basic")
    tree = kb["trees"].get(sid).tree
    nodes = index_nodes(tree)
    recipe = next(n for n in nodes.values() if n.get("label") == "table:1")
    from app.db.chunk_store import get_chunk_store
    from app.domain.schemas import DocumentChunkResponse

    only_recipe = [
        DocumentChunkResponse(id=c.id, source_id=sid, ord=c.ord, text=c.text, page=c.page_no)
        for c in get_chunk_store().get_by_source(sid) if c.id in recipe["chunk_ids"]
    ]
    monkeypatch.setattr("app.services.hybrid_search.hybrid_search", lambda *a, **k: only_recipe)
    result = two_stage.two_stage_retrieve("表1配方对应的中性盐雾测试结果", use_llm=False)
    nav = result.documents[0].navigation
    pages = {p for e in nav.evidence for p in (e["page_range"] or []) if p}
    assert {10, 18} <= pages  # recipe on p10, results on p18
    labels = _labels_read(kb["trees"], nav)
    assert {"table:1", "table:2"} <= labels
    assert any(step["action"] in ("follow_ref", "follow_backref") for step in nav.reasoning_trace)


def test_llm_navigation_records_an_auditable_path(kb):
    sid = next(s for s, p in kb["patents"].items() if p.source_id == "pat-cn-basic")
    tree = kb["trees"].get(sid).tree
    nodes = index_nodes(tree)
    by_label = {n.get("label"): nid for nid, n in nodes.items() if n.get("label")}
    definition = next(nid for nid, n in nodes.items() if n["title"] == "发明内容")
    script = iter([
        {"select": [definition], "reason": "先确认前体A的定义"},
        {"done": False, "next": [by_label["table:1"]], "reason": "前体A出现在表1配方中"},
        {"done": False, "next": [by_label["table:2"]], "reason": "表1对应实施例的盐雾结果在表2"},
        {"done": True, "reason": "定义、配方与盐雾结果均已找到"},
    ])
    prompts: list[str] = []

    def fake_llm(prompt: str):
        prompts.append(prompt)
        return next(script)

    depths = two_stage.deepen_documents("前体A的定义、所在配方及其盐雾性能", [sid], deep=True, llm=fake_llm)
    nav = depths[0].navigation
    assert nav.mode == "llm" and nav.stopped == "answered"
    assert nav.node_path == [definition, by_label["table:1"], by_label["table:2"]]
    assert [s["action"] for s in nav.reasoning_trace] == ["select", "follow", "follow"]
    assert nav.reasoning_trace[-1]["conclusion"] == "定义、配方与盐雾结果均已找到"
    assert "环氧当量为320-360" not in prompts[0]  # the first prompt is the outline only, no body text
    assert all(e["text"] for e in nav.evidence)


def test_model_failure_falls_back_to_heuristic(kb):
    sid = next(iter(kb["patents"]))

    def broken(_prompt):
        raise RuntimeError("provider down")

    nav = two_stage.deepen_documents("配方组成与盐雾测试结果", [sid], deep=True, llm=broken)[0].navigation
    assert nav.mode == "heuristic"
    assert nav.reasoning_trace[0]["action"] == "fallback" and "provider down" in nav.reasoning_trace[0]["reason"]
    assert nav.node_path


def test_budget_is_enforced(kb):
    sid = next(iter(kb["patents"]))
    row = kb["trees"].get(sid)
    load = two_stage._NodeText(sid)
    nav = navigate(sid, row.tree, "配方组成与盐雾测试结果", load, max_hops=2)
    assert nav.hops == 2 and len(nav.node_path) == 2
    nav = navigate(sid, row.tree, "配方组成与盐雾测试结果", load, max_hops=10, max_chars=150)
    assert nav.chars_read <= 150


def test_automatic_mode_leaves_short_documents_to_stage1(kb):
    depths = two_stage.deepen_documents("前体A 盐雾", [kb["short"]], use_llm=False)
    assert depths[0].mode == "stage1" and "3 pages < 20" in depths[0].reason
    forced = two_stage.deepen_documents("前体A 盐雾", [kb["short"]], deep=True, use_llm=False)
    assert forced[0].mode == "pageindex"
    off = two_stage.deepen_documents("前体A 盐雾", list(kb["patents"]), deep=False)
    assert {d.mode for d in off} == {"stage1"}


def test_pageindex_can_be_switched_off(kb, monkeypatch):
    monkeypatch.setenv("FORMUNEX_PAGEINDEX_ENABLED", "false")
    get_settings.cache_clear()
    depths = two_stage.deepen_documents("配方", list(kb["patents"]), deep=True)
    assert {d.reason for d in depths} == {"no toc tree"}


def test_navigation_evidence_is_page_anchored(kb):
    sid = next(s for s, p in kb["patents"].items() if p.source_id == "pat-cn-basic")
    depths = two_stage.deepen_documents("实施例1的配方组成及其中性盐雾测试结果", [sid], deep=True, use_llm=False)
    evidence = two_stage.navigation_evidence(depths, titles={sid: "无铬钝化专利"})
    assert evidence and all(e.source == "kb_pageindex" and e.page for e in evidence)
    assert evidence[0].title.startswith("无铬钝化专利 · ")
    assert evidence[0].relevance > evidence[-1].relevance


def test_deep_research_engine_mounts_pageindex(kb):
    from app.services.deep_research.engine import DeepResearchEngine

    with DeepResearchEngine() as engine:
        evidence = engine.pageindex_evidence("实施例1的配方组成及其中性盐雾测试结果")
    assert any(e.source == "kb_pageindex" and e.page == 18 for e in evidence)


def test_embodiment_draft_links_recipe_and_results(kb):
    from app.services.embodiment_drafts import _pageindex_table_links

    sid = next(s for s, p in kb["patents"].items() if p.source_id == "pat-cn-basic")
    links = _pageindex_table_links(sid)
    assert len(links) == 1
    assert links[0]["recipe"]["page_range"] == [10, 10] and links[0]["performance"]["page_range"] == [18, 18]
    assert _pageindex_table_links("no-such-source") == []


def test_api_endpoints(kb):
    from app.main import app

    client = TestClient(app)
    sid = next(iter(kb["patents"]))
    r = client.get(f"/api/kb/sources/{sid}/toc-tree")
    assert r.status_code == 200 and r.json()["tree"]["children"]
    assert client.get("/api/kb/sources/missing/toc-tree").status_code == 404
    r = client.post(f"/api/kb/sources/{sid}/toc-tree/rebuild")
    assert r.status_code == 200 and r.json()["node_count"] > 3
    r = client.post("/api/kb/pageindex/search", json={"query": "实施例1的配方组成及其中性盐雾测试结果", "use_llm": False})
    body = r.json()
    assert r.status_code == 200 and body["stage1"]
    assert any(d["mode"] == "pageindex" and d["navigation"]["reasoning_trace"] for d in body["documents"])


def test_deleting_a_source_removes_its_tree(kb):
    from app.services.kb_delete import delete_kb_source

    sid = next(iter(kb["patents"]))
    delete_kb_source(sid)
    assert kb["trees"].get(sid) is None


def test_reindexing_without_text_drops_the_tree(kb):
    sid = next(iter(kb["patents"]))
    kb_index.index_source(sid, "<!-- page:1 -->\n" + "x" * 5, embed=False)
    assert kb["trees"].get(sid) is None


def test_build_on_ingest_respects_its_switch(kb, monkeypatch):
    monkeypatch.setenv("FORMUNEX_PAGEINDEX_BUILD_ON_INGEST", "false")
    get_settings.cache_clear()
    p = patent_cn_basic()
    text = p.page_marked_text()
    sid = kb["sources"].create(filename="x.pdf", title="x", source_kind="patent", full_text=text,
                               content_hash=hashlib.sha256(text.encode() + b"2").hexdigest())
    kb_index.index_source(sid, text, embed=False)
    assert kb["trees"].get(sid) is None
