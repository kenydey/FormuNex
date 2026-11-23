"""融合升级批 4：确定性查询路由器 v1。

覆盖：路由表四路 + 优先级（structured > deep > wiki_first > hybrid，
显式 deep 最高优先）+ 稳定概念页 kind 过滤 + fail-open + blend
wiki_first 模式覆盖 + _augment_with_kb wiki_mode 透传 +
_with_routed_deep_evidence（成功 / 失败 / 无证据 / 开关关）。
开关默认关；以下测试只在需要时显式开。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.wiki_store import WikiStore
from app.domain.chat_schemas import ChatRequest
from app.domain.schemas import Evidence
from app.services.query_router import route_query
from app.services.wiki.schema import dump_page


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUNEX_WIKI_CHAT_BLEND", "true")
    monkeypatch.setenv("FORMUNEX_KG_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_KB_V2_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    """隔离 WikiStore：PVAM 材料页（稳定概念）+ 一份报告页（非稳定 kind）。"""
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "wiki_b4.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    get_settings.cache_clear()

    def _upsert(path, kind, title):
        key = path.rsplit("/", 1)[-1].replace(".md", "")
        md = dump_page(
            kind=kind,
            title=title,
            entity_id=f"{kind}:{key}",
            norm_key=key,
            source_ids=["s1"],
            summary=f"{title} summary.",
            flags=[],
        )
        wiki.upsert_page(
            path=path, kind=kind, title=title, norm_key=key,
            entity_id=f"{kind}:{key}", markdown=md, source_ids=["s1"], flags=[],
        )

    _upsert("materials/pvam.md", "material", "PVAM")
    _upsert("reports/quarterly.md", "report", "Quarterly Report")
    return wiki


def test_router_default_off():
    assert get_settings().query_router_enabled is False


def test_explicit_deep_highest_priority():
    d = route_query("统计各配方的盐雾试验平均时长并对比", explicit_deep=True)
    assert d.route == "deep"
    assert d.explicit_deep is True


def test_structured_intent_routes_structured():
    d = route_query("统计各配方的盐雾试验平均时长并对比")
    assert d.route == "structured"
    assert d.intent_route == "structured"


def test_hybrid_intent_routes_structured():
    d = route_query("GB/T 1771盐雾试验的沉降率要求是多少，试样如何放置")
    assert d.intent_route == "hybrid"
    assert d.route == "structured"


def test_pageindex_only_routes_deep():
    d = route_query("实施例1的配方组成及其中性盐雾测试结果")
    assert d.intent_route == "unstructured"
    assert d.pageindex_allowed is True
    assert d.route == "deep"


def test_generic_routes_hybrid():
    d = route_query("如何提高涂层的耐蚀性？")
    assert d.route == "hybrid"
    assert d.intent_route == "unstructured"
    assert d.pageindex_allowed is False
    assert d.wiki_stable_hit is False


def test_empty_query_routes_hybrid():
    assert route_query("").route == "hybrid"
    assert route_query("   ").route == "hybrid"


def test_structured_beats_pageindex():
    # 盐雾/对比同时触发两个分类器：首要需求是实验库数据，走 structured。
    d = route_query("对比实施例1和实施例2的盐雾性能")
    assert d.intent_route == "structured"
    assert d.pageindex_allowed is True
    assert d.route == "structured"


def test_wiki_only_routes_wiki_first(wiki_env):
    d = route_query("PVAM 是什么材料？")
    assert d.route == "wiki_first"
    assert d.wiki_stable_hit is True
    assert d.wiki_stable_pages == ["wiki:materials/pvam.md"]


def test_deep_beats_wiki(wiki_env):
    # 全面分析是长文深度问题：首要需求是长文导航，走 deep；Wiki 页仍会混入。
    d = route_query("全面分析 PVAM 在水性涂料中的作用机制")
    assert d.pageindex_allowed is True
    assert d.wiki_stable_hit is True
    assert d.route == "deep"


def test_non_stable_wiki_page_does_not_count(wiki_env):
    # 报告页不是稳定概念页（kind=report）：命中也不走 wiki_first。
    d = route_query("Quarterly Report 的结论是什么？")
    assert d.wiki_stable_hit is False
    assert d.route == "hybrid"


def test_fail_open_when_intent_raises(monkeypatch):
    import app.services.query_router as router_mod
    import app.services.text2sql as text2sql_mod

    def _boom(q):
        raise RuntimeError("intent down")

    monkeypatch.setattr(text2sql_mod, "classify_intent", _boom)
    d = route_query("如何提高涂层的耐蚀性？")
    assert d.route == "hybrid"


def test_fail_open_when_wiki_signal_raises(monkeypatch, wiki_env):
    import app.services.wiki.retrieve as retrieve_mod

    def _boom(question, k=5, project_id=None):
        raise RuntimeError("wiki down")

    monkeypatch.setattr(retrieve_mod, "_wiki_blend_hits", _boom)
    d = route_query("PVAM 是什么材料？")
    assert d.route == "hybrid"
    assert d.wiki_stable_hit is False


def test_decision_to_dict_roundtrip():
    d = route_query("如何提高涂层的耐蚀性？")
    payload = d.to_dict()
    assert payload["route"] == "hybrid"
    assert payload["intent_route"] == "unstructured"
    assert payload["wiki_stable_pages"] == []


def test_blend_wiki_first_mode_takes_more_hits(monkeypatch):
    """wiki_first 模式 k=8 前置、balanced k=5：只改条数与排序，不改权重。"""
    from app.services.wiki import retrieve as retrieve_mod

    def _ten(question, k=5, project_id=None):
        # 生产 _wiki_blend_hits 按 k 截断；fake 同样按 k 截断，测的是 blend 的
        # mode→k 映射，不是 fake 的截断。
        return [
            Evidence(source="wiki", identifier=f"wiki:p{i}.md", title=f"[Wiki/material] P{i}",
                     snippet="s", relevance=0.9 - 0.01 * i)
            for i in range(10)
        ][: max(1, k)]

    monkeypatch.setattr(retrieve_mod, "_wiki_blend_hits", _ten)
    merged_balanced, added_balanced = retrieve_mod.blend_wiki_evidence("PVAM 是什么？", [])
    merged_first, added_first = retrieve_mod.blend_wiki_evidence(
        "PVAM 是什么？", [], mode="wiki_first"
    )
    assert added_balanced == 5
    assert added_first == 8
    assert len(merged_balanced) == 5
    assert len(merged_first) == 8
    # 非法 mode 回退 balanced。
    _, added_bogus = retrieve_mod.blend_wiki_evidence("PVAM 是什么？", [], mode="bogus")
    assert added_bogus == 5


def test_augment_with_kb_passes_wiki_mode(monkeypatch):
    """_augment_with_kb 只透传 wiki_mode；KG/hybrid 分支行为不变。"""
    import app.services.wiki.retrieve as retrieve_mod
    from app.services.chat_orchestration import _augment_with_kb

    seen: dict = {}

    def _fake_blend(question, sources, *, k=None, project_id=None, mode=None):
        seen["mode"] = mode
        return sources, 0

    monkeypatch.setattr(retrieve_mod, "blend_wiki_evidence", _fake_blend)
    sources, added, resolution, kg_stats = _augment_with_kb(
        "PVAM 是什么材料？", [], wiki_mode="wiki_first"
    )
    assert seen["mode"] == "wiki_first"
    assert sources == []
    assert added == 0
    assert resolution is None
    assert kg_stats is None
    # 不传时 mode=None，沿用 settings。
    _augment_with_kb("PVAM 是什么材料？", [])
    assert seen["mode"] is None


def _req(question: str) -> ChatRequest:
    return ChatRequest(question=question)


def _nav_result(doc_ids=("s1",)):
    nav = SimpleNamespace(
        evidence=[
            {"node_id": "n1", "title": "T", "text": "x" * 10, "page_range": [3, 3],
             "chunk_ids": []}
        ]
    )
    docs = [
        SimpleNamespace(source_id=sid, navigation=nav, mode="pageindex", reason="3 pages")
        for sid in doc_ids
    ]
    return SimpleNamespace(documents=docs, stage1=[], answer=None, answer_source="none")


def test_routed_deep_prepends_navigation_evidence(monkeypatch):
    import app.db.source_store as source_store_mod
    import app.services.pageindex.two_stage as two_stage_mod
    from app.services.chat_orchestration import _with_routed_deep_evidence

    monkeypatch.setattr(
        two_stage_mod, "two_stage_retrieve", lambda *a, **k: _nav_result()
    )
    monkeypatch.setattr(
        source_store_mod,
        "get_source_store",
        lambda: SimpleNamespace(get=lambda sid: SimpleNamespace(title="Doc")),
    )
    out, notices = _with_routed_deep_evidence(_req("实施例1的配方组成"), "实施例1的配方组成", [])
    assert notices == []
    assert len(out) == 1
    assert out[0].source == "kb_pageindex"
    assert out[0].identifier == "s1#n1"


def test_routed_deep_fails_open_on_error(monkeypatch):
    import app.services.pageindex.two_stage as two_stage_mod
    from app.services.chat_orchestration import _with_routed_deep_evidence

    def _boom(*a, **k):
        raise RuntimeError("two-stage down")

    monkeypatch.setattr(two_stage_mod, "two_stage_retrieve", _boom)
    before = [Evidence(source="kb", identifier="kb:s#c1", title="t", snippet="s", relevance=0.5)]
    out, notices = _with_routed_deep_evidence(_req("实施例1的配方组成"), "实施例1的配方组成", before)
    assert out == before
    assert notices[0]["code"] == "routed_deep_failed"


def test_routed_deep_empty_navigation_notice(monkeypatch):
    import app.db.source_store as source_store_mod
    import app.services.pageindex.two_stage as two_stage_mod
    from app.services.chat_orchestration import _with_routed_deep_evidence

    empty = SimpleNamespace(documents=[SimpleNamespace(source_id="s1", navigation=None)],
                            stage1=[], answer=None, answer_source="none")
    monkeypatch.setattr(two_stage_mod, "two_stage_retrieve", lambda *a, **k: empty)
    monkeypatch.setattr(
        source_store_mod,
        "get_source_store",
        lambda: SimpleNamespace(get=lambda sid: None),
    )
    before = [Evidence(source="kb", identifier="kb:s#c1", title="t", snippet="s", relevance=0.5)]
    out, notices = _with_routed_deep_evidence(_req("实施例1的配方组成"), "实施例1的配方组成", before)
    assert out == before
    assert notices[0]["code"] == "routed_deep_empty"


def test_routed_deep_disabled_notice(monkeypatch):
    monkeypatch.setenv("FORMUNEX_PAGEINDEX_ENABLED", "false")
    get_settings.cache_clear()
    from app.services.chat_orchestration import _with_routed_deep_evidence

    before = [Evidence(source="kb", identifier="kb:s#c1", title="t", snippet="s", relevance=0.5)]
    out, notices = _with_routed_deep_evidence(_req("实施例1的配方组成"), "实施例1的配方组成", before)
    assert out == before
    assert notices[0]["code"] == "routed_deep_unavailable"
