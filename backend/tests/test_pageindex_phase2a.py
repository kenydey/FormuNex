"""Phase 2A (补齐计划 2-1~2-5): PageIndex 限定场景 + STORM 大纲编辑。

Covers: query-type gate, stage-1 ColBERT rerank, children_retrieval fallback,
answer synthesis (LLM + extractive), token budget, summary auto-scheduling,
STORM outline endpoints + provided-outline reports.
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
from app.services.pageindex.navigator import estimate_tokens, navigate
from app.services.pageindex.two_stage import classify_pageindex_query
from tests.pageindex_fixtures import ALL_PATENTS, patent_cn_basic


@pytest.fixture()
def kb(tmp_path, monkeypatch):
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


# --------------------------------------------------------------------------- 2-1 query-type gate


@pytest.mark.parametrize(("query", "eligible"), [
    ("表1配方对应的中性盐雾测试结果", True),          # 实施例萃取
    ("实施例1的配方组成及其中性盐雾测试结果", True),  # 实施例萃取
    ("前体A的定义、所在配方及其盐雾性能", True),      # 多段配对提问
    ("无铬钝化配方的深度研究综述", True),             # 深度研究
    ("前体A 盐雾", False),                            # 短查询
    ("环氧树脂是什么", False),                        # 事实型
    ("前体A的价格是多少", False),                     # 事实型
    ("你好", False),                                  # 短查询
    ("", False),                                      # 空
])
def test_query_type_gate(query, eligible):
    ok, reason = classify_pageindex_query(query)
    assert ok is eligible, reason


def test_gate_blocks_generic_queries_for_long_docs(kb):
    sid = next(iter(kb["patents"]))
    depths = two_stage.deepen_documents("你好", [sid], use_llm=False)
    assert depths[0].mode == "stage1" and "not eligible" in depths[0].reason
    # Known mount points bypass the classifier.
    depths = two_stage.deepen_documents("你好", [sid], use_llm=False, query_kind="deep_research")
    assert depths[0].mode == "pageindex"
    # Explicit deep=True also bypasses (user explicitly enabled deep mode).
    depths = two_stage.deepen_documents("你好", [sid], deep=True, use_llm=False)
    assert depths[0].mode == "pageindex"


# --------------------------------------------------------------------------- 2-2 stage-1 ColBERT rerank


def test_colbert_rerank_reorders_candidates():
    from app.domain.schemas import Evidence
    from app.services.colbert_store import rerank_candidates

    cands = [
        Evidence(source="kb", identifier="a", title="", snippet="毫不相关的天气新闻", relevance=0.5),
        Evidence(source="kb", identifier="b", title="", snippet="环氧树脂配方中前体A的用量与盐雾测试结果", relevance=0.5),
        Evidence(source="kb", identifier="c", title="", snippet="公司食堂菜单", relevance=0.5),
    ]
    ranked, meta = rerank_candidates("前体A配方与盐雾测试结果", cands)
    assert meta["applied"] is True
    assert ranked[0].identifier == "b"
    assert {c.identifier for c in ranked} == {"a", "b", "c"}


def test_colbert_rerank_passthrough_on_edge_cases():
    from app.services.colbert_store import rerank_candidates

    assert rerank_candidates("q", [])[0] == []
    assert rerank_candidates("q", [])[1]["applied"] is False


def test_stage1_rerank_reported(kb):
    result = two_stage.two_stage_retrieve("表1配方对应的中性盐雾测试结果", use_llm=False)
    assert result.stage1_reranked is True
    assert result.stage1  # rerank preserves the candidate set


# --------------------------------------------------------------------------- 2-3 fallback + answer


def test_no_tree_falls_back_to_children_retrieval(kb):
    from app.db.chunk_store import get_chunk_store

    sid = next(iter(kb["patents"]))
    chunks = get_chunk_store().get_by_source(sid)
    chunk_ids = [c.id for c in chunks]
    kb["trees"].delete(sid)
    depths = two_stage.deepen_documents(
        "表1配方对应的中性盐雾测试结果", [sid], use_llm=False,
        stage1_chunks={sid: chunk_ids}, stage1=list(chunks),
    )
    assert depths[0].mode == "stage1" and depths[0].reason == "no toc tree"
    assert depths[0].fallback == "children_retrieval"
    assert depths[0].fallback_chunk_ids
    assert set(depths[0].fallback_chunk_ids) <= set(chunk_ids)


def test_budget_exhaustion_falls_back_to_children_retrieval(kb, monkeypatch):
    from app.db.chunk_store import get_chunk_store

    monkeypatch.setenv("FORMUNEX_PAGEINDEX_MAX_HOPS", "1")
    get_settings.cache_clear()
    sid = next(iter(kb["patents"]))
    chunks = get_chunk_store().get_by_source(sid)
    chunk_ids = [c.id for c in chunks]
    depths = two_stage.deepen_documents(
        "表1配方对应的中性盐雾测试结果", [sid], use_llm=False,
        stage1_chunks={sid: chunk_ids}, stage1=list(chunks),
    )
    assert depths[0].navigation is not None
    assert depths[0].navigation.stopped == "budget"
    assert depths[0].fallback == "children_retrieval"
    assert depths[0].fallback_chunk_ids


def test_heuristic_answer_is_extractive(kb):
    sid = next(iter(kb["patents"]))
    depths = two_stage.deepen_documents("实施例1的配方组成及其中性盐雾测试结果", [sid], deep=True, use_llm=False)
    nav = depths[0].navigation
    assert nav is not None and nav.answer
    assert nav.answer_source == "extractive"
    assert "p." in nav.answer  # page refs on quoted snippets


def test_llm_answer_is_composed(kb):
    sid = next(iter(kb["patents"]))
    tree = kb["trees"].get(sid).tree
    nodes = {n["node_id"]: n for n in _walk(tree)}
    definition = next(nid for nid, n in nodes.items() if n["title"] == "发明内容")

    def fake_llm(prompt: str):
        if "根据下面从长文档中读到的证据回答问题" in prompt:
            return {"answer": "前体A定义见发明内容，表1配方对应表2盐雾结果。"}
        if "选择最可能" in prompt:
            return {"select": [definition], "reason": "先确认定义"}
        return {"done": True, "reason": "证据已足够"}

    depths = two_stage.deepen_documents("前体A的定义、所在配方及其盐雾性能", [sid], deep=True, llm=fake_llm)
    nav = depths[0].navigation
    assert nav is not None and nav.answer_source == "llm"
    assert "表1" in nav.answer and "表2" in nav.answer


def _walk(node):
    yield node
    for c in node.get("children") or []:
        yield from _walk(c)


def test_two_stage_result_carries_top_answer(kb):
    result = two_stage.two_stage_retrieve("实施例1的配方组成及其中性盐雾测试结果", use_llm=False)
    assert result.answer
    assert result.answer_source == "extractive"


# --------------------------------------------------------------------------- token budget


def test_token_budget_enforced(kb):
    sid = next(iter(kb["patents"]))
    row = kb["trees"].get(sid)
    load = two_stage._NodeText(sid)
    nav = navigate(sid, row.tree, "配方组成与盐雾测试结果", load, max_tokens=10)
    assert nav.tokens_read <= 10


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("前体A") == 3  # 3 CJK chars
    assert estimate_tokens("abcd") == 1  # 4 ASCII chars ≈ 1 token
    assert estimate_tokens("hello world, this is a test") == 7


# --------------------------------------------------------------------------- 2-4 summary auto-scheduling


def test_schedule_on_stage2_visit(kb):
    from app.services.pageindex.summaries import maybe_schedule_summaries

    sid = next(iter(kb["patents"]))
    assert kb["trees"].get(sid).summaries_status == "none"
    assert maybe_schedule_summaries(sid, visited_by_stage2=True) is True
    assert kb["trees"].get(sid).summaries_status == "pending"
    assert maybe_schedule_summaries(sid, visited_by_stage2=True) is False  # fires once


def test_schedule_skips_short_unvisited_docs(kb):
    from app.services.pageindex.summaries import maybe_schedule_summaries

    assert maybe_schedule_summaries(kb["short"], visited_by_stage2=False) is False
    assert kb["trees"].get(kb["short"]).summaries_status == "none"


def test_schedule_fires_for_long_docs_on_view(kb):
    from app.services.pageindex.summaries import maybe_schedule_summaries

    sid = next(iter(kb["patents"]))
    assert maybe_schedule_summaries(sid, visited_by_stage2=False) is True


# --------------------------------------------------------------------------- API


def test_api_query_kind_gate(kb):
    from app.main import app

    client = TestClient(app)
    # "前体A" is a short lookup (gate blocks) but matches stage-1 chunks.
    r = client.post("/api/kb/pageindex/search",
                    json={"query": "前体A", "use_llm": False})
    assert r.status_code == 200
    assert r.json()["documents"][0]["mode"] == "stage1"
    r = client.post("/api/kb/pageindex/search",
                    json={"query": "前体A", "use_llm": False, "query_kind": "deep_research"})
    assert r.status_code == 200
    assert r.json()["documents"][0]["mode"] == "pageindex"


def test_api_get_toc_tree_schedules_summaries(kb):
    from app.main import app

    client = TestClient(app)
    sid = next(iter(kb["patents"]))
    assert kb["trees"].get(sid).summaries_status == "none"
    r = client.get(f"/api/kb/sources/{sid}/toc-tree")
    assert r.status_code == 200
    assert kb["trees"].get(sid).summaries_status == "pending"


# --------------------------------------------------------------------------- 2-5 STORM outline endpoints


@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    import app.db.project_store as ps_mod
    import app.db.wiki_store as wiki_store_mod
    from app.db.database import Base, make_engine, make_session_factory
    from app.db.project_store import ProjectStore
    from app.db.wiki_store import WikiStore

    for key, value in {
        "FORMUNEX_API_AUTH_ENABLED": "false",
        "FORMUNEX_WIKI_ENABLED": "true",
        "FORMUNEX_WIKI_PROJECT_DOSSIER_ENABLED": "true",
        "FORMUNEX_WIKI_DOSSIER_REPORT_ENABLED": "true",
        "FORMUNEX_WIKI_STORM_REPORT_ENABLED": "true",
        "FORMUNEX_WIKI_DOSSIER_LLM_NARRATIVE": "false",
        "FORMUNEX_WIKI_DOSSIER_AUTO_PATCH": "false",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/storm.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    projects = ProjectStore(factory)
    monkeypatch.setattr(ps_mod, "_store", projects)
    yield {"wiki": wiki, "projects": projects, "root": wiki_root}
    get_settings.cache_clear()


def _make_project(projects, pid="proj-storm-1"):
    from app.domain.schemas import ProductDomain, Requirement

    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate="carbon_steel",
        salt_spray_hours=720,
        voc_limit_gpl=420,
    )
    return projects.create(title="STORM 测试项目", requirement=req).id


def test_storm_outline_endpoints(wiki_env):
    from app.main import app

    pid = _make_project(wiki_env["projects"])
    client = TestClient(app)
    assert client.get(f"/api/wiki/storm/outline/{pid}").status_code == 404
    r = client.post("/api/wiki/storm/outline", json={"project_id": pid, "topic": "无铬钝化技术"})
    assert r.status_code == 200
    outline = r.json()
    assert outline["project_id"] == pid and len(outline["sections"]) >= 1
    r = client.get(f"/api/wiki/storm/outline/{pid}")
    assert r.status_code == 200 and r.json()["sections"] == outline["sections"]
    # Edit: rename the first section, save, re-read.
    edited = dict(outline)
    edited["sections"] = [dict(outline["sections"][0], title="编辑后的章节标题"), *outline["sections"][1:]]
    r = client.put(f"/api/wiki/storm/outline/{pid}", json=edited)
    assert r.status_code == 200
    assert client.get(f"/api/wiki/storm/outline/{pid}").json()["sections"][0]["title"] == "编辑后的章节标题"
    # project_id mismatch is rejected.
    bad = dict(edited, project_id="other-project")
    assert client.put(f"/api/wiki/storm/outline/{pid}", json=bad).status_code == 400


def test_storm_outline_blank_project_id_is_400(wiki_env):
    from app.main import app

    client = TestClient(app)
    # api-fuzz: whitespace-only project_id used to 500 (ValueError from
    # build_project_dossier_pack was not caught here; /dossier/pack already
    # mapped it to 400).
    assert client.post("/api/wiki/storm/outline", json={"project_id": "\n\r\t"}).status_code == 400
    assert client.post("/api/wiki/storm/outline", json={"project_id": "   "}).status_code == 400


def test_run_storm_report_uses_provided_outline(wiki_env):
    from app.services.wiki.storm_orchestrator import run_storm_report
    from app.services.wiki.storm_schema import ReportOutline, SectionSpec

    pid = _make_project(wiki_env["projects"])
    custom = ReportOutline(
        project_id=pid, topic="自定义大纲主题",
        sections=[SectionSpec(section_id="s1", title="自定义章节", core_intent="验证提供的大纲被使用")],
    )
    out = run_storm_report(pid, outline=custom, persist=False)
    assert out["ok"] is True
    assert out["title"] == "自定义大纲主题"
    assert out["meta"]["section_count"] == 1
