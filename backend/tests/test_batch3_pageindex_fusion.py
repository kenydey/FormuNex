"""融合升级批 3：PageIndex 版本一致性校验（3-1）与启发式升级（3-2）。

Fixture 与 test_pageindex_two_stage_rag.py 同构：隔离库中 3 篇合成长专利
（22-26 页）+ 1 篇 3 页短文，经真实 index_source 入库建树。3-3 无代码
改动（已砍，见批 3 报告），故无测试。

3-1：deepen_documents 入口校验 tree.builder_version 与树页数 / 现存 chunk
页跨度；不一致重建一次，重建后仍不一致则 fail-open 降级 Stage 1。
3-2：分类器拒绝后，Stage 1 命中集中在同一长文档（share ≥ 0.5）且
top-k 覆盖度低（coverage ≤ 0.2）时升级 Stage 2，只走启发式导航。
两批行为默认关（config 开关），以下测试显式开开关。
"""
from __future__ import annotations

import hashlib

import pytest

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.models_plm import DocumentTocTreeRow
from app.db.toc_tree_store import TocTreeStore
from app.domain.schemas import DocumentChunkResponse
from app.services import kb_index
from app.services.pageindex import two_stage
from tests.pageindex_fixtures import ALL_PATENTS


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    """Isolated KB with three long patents and one short note ingested."""
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


def _enable(monkeypatch, **env: str) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def _fake_stage1(sid: str, n: int) -> tuple[list[str], list[DocumentChunkResponse]]:
    """First ``n`` real chunks of ``sid`` as stage-1 hits."""
    from app.db.chunk_store import get_chunk_store

    chunks = get_chunk_store().get_by_source(sid)[:n]
    ids = [c.id for c in chunks]
    resp = [DocumentChunkResponse(id=c.id, source_id=sid, ord=c.ord, text=c.text, page=c.page_no)
            for c in chunks]
    return ids, resp


EXTRACTION_Q = "实施例1的配方组成及其中性盐雾测试结果"
GENERIC_Q = "前体A"  # classifier rejects: short query, no strong marker


# --------------------------------------------------------------------- 3-1


def test_version_check_rebuilds_tree_after_builder_version_bump(kb, monkeypatch):
    sid = next(s for s, p in kb["patents"].items() if p.source_id == "pat-cn-basic")
    assert kb["trees"].get(sid).builder_version == "1"
    monkeypatch.setattr(two_stage, "BUILDER_VERSION", "999")
    _enable(monkeypatch, FORMUNEX_PAGEINDEX_VERSION_CHECK_ENABLED="true")
    depths = two_stage.deepen_documents(EXTRACTION_Q, [sid], use_llm=False)
    assert depths[0].mode == "pageindex", depths[0].reason
    assert kb["trees"].get(sid).builder_version == "999"


def test_version_check_off_leaves_stale_tree_untouched(kb, monkeypatch):
    sid = next(s for s, p in kb["patents"].items() if p.source_id == "pat-cn-basic")
    monkeypatch.setattr(two_stage, "BUILDER_VERSION", "999")
    # Switch stays off (default): the stale tree is used as-is.
    depths = two_stage.deepen_documents(EXTRACTION_Q, [sid], use_llm=False)
    assert depths[0].mode == "pageindex"
    assert kb["trees"].get(sid).builder_version == "1"


def test_version_check_rebuilds_on_page_span_mismatch(kb, monkeypatch):
    sid = next(s for s, p in kb["patents"].items() if p.source_id == "pat-cn-basic")
    real_pages = int(kb["trees"].get(sid).page_count)
    assert real_pages >= 20
    with kb["trees"]._factory() as session:
        row = session.get(DocumentTocTreeRow, sid)
        row.page_count = 999
        session.commit()
    assert int(kb["trees"].get(sid).page_count) == 999
    _enable(monkeypatch, FORMUNEX_PAGEINDEX_VERSION_CHECK_ENABLED="true")
    depths = two_stage.deepen_documents(EXTRACTION_Q, [sid], use_llm=False)
    assert depths[0].mode == "pageindex", depths[0].reason
    assert int(kb["trees"].get(sid).page_count) == real_pages


def test_version_check_degrades_to_stage1_when_rebuild_fails(kb, monkeypatch):
    sid = next(s for s, p in kb["patents"].items() if p.source_id == "pat-cn-basic")
    monkeypatch.setattr(two_stage, "BUILDER_VERSION", "999")

    def _boom(_source_id: str, **_kwargs):
        raise RuntimeError("rebuild unavailable")

    monkeypatch.setattr(two_stage, "build_tree_for_source", _boom)
    _enable(monkeypatch, FORMUNEX_PAGEINDEX_VERSION_CHECK_ENABLED="true")
    depths = two_stage.deepen_documents(EXTRACTION_Q, [sid], use_llm=False)
    assert depths[0].mode == "stage1"
    assert "degraded to stage1" in depths[0].reason
    assert depths[0].fallback == "children_retrieval"


# --------------------------------------------------------------------- 3-2


def test_escalation_fires_when_hits_concentrate_in_one_long_doc(kb, monkeypatch):
    sids = list(kb["patents"])
    a, b, c = sids[0], sids[1], sids[2]
    ids_a, resp_a = _fake_stage1(a, 4)  # share 4/8 = 0.50, coverage 4/24 = 0.167
    ids_b, resp_b = _fake_stage1(b, 2)
    ids_c, resp_c = _fake_stage1(c, 2)
    _enable(monkeypatch, FORMUNEX_PAGEINDEX_HEURISTIC_ESCALATION_ENABLED="true")
    depths = two_stage.deepen_documents(
        GENERIC_Q, [a, b, c], use_llm=False,
        stage1_chunks={a: ids_a, b: ids_b, c: ids_c},
        stage1=resp_a + resp_b + resp_c,
    )
    by_sid = {d.source_id: d for d in depths}
    assert by_sid[a].mode == "pageindex", by_sid[a].reason
    assert "heuristic escalation" in by_sid[a].reason
    assert "share=0.50" in by_sid[a].reason and "coverage=0.167" in by_sid[a].reason
    assert by_sid[a].navigation is not None and by_sid[a].navigation.mode == "heuristic"
    assert by_sid[b].mode == "stage1" and "not eligible" in by_sid[b].reason
    assert by_sid[c].mode == "stage1" and "not eligible" in by_sid[c].reason


def test_escalation_off_keeps_classifier_rejection(kb, monkeypatch):
    sids = list(kb["patents"])
    a, b, c = sids[0], sids[1], sids[2]
    ids_a, resp_a = _fake_stage1(a, 4)
    ids_b, resp_b = _fake_stage1(b, 2)
    ids_c, resp_c = _fake_stage1(c, 2)
    # Switch stays off (default): same concentrated hits, no escalation.
    depths = two_stage.deepen_documents(
        GENERIC_Q, [a, b, c], use_llm=False,
        stage1_chunks={a: ids_a, b: ids_b, c: ids_c},
        stage1=resp_a + resp_b + resp_c,
    )
    assert {d.mode for d in depths} == {"stage1"}
    assert all("not eligible" in d.reason for d in depths)


def test_escalation_blocked_when_share_low(kb, monkeypatch):
    sids = list(kb["patents"])
    a, b = sids[0], sids[1]
    ids_a, resp_a = _fake_stage1(a, 3)  # share 3/8 = 0.375 < 0.5
    ids_b, resp_b = _fake_stage1(b, 5)  # share 5/8 = 0.625, coverage 5/26 = 0.192
    _enable(monkeypatch, FORMUNEX_PAGEINDEX_HEURISTIC_ESCALATION_ENABLED="true")
    depths = two_stage.deepen_documents(
        GENERIC_Q, [a, b], use_llm=False,
        stage1_chunks={a: ids_a, b: ids_b}, stage1=resp_a + resp_b,
    )
    by_sid = {d.source_id: d for d in depths}
    assert by_sid[a].mode == "stage1" and "not eligible" in by_sid[a].reason
    assert by_sid[b].mode == "pageindex" and "heuristic escalation" in by_sid[b].reason


def test_escalation_blocked_when_coverage_high(kb, monkeypatch):
    sids = list(kb["patents"])
    a = sids[0]
    ids_a, resp_a = _fake_stage1(a, 10)  # share 1.0, coverage 10/24 = 0.417 > 0.2
    _enable(monkeypatch, FORMUNEX_PAGEINDEX_HEURISTIC_ESCALATION_ENABLED="true")
    depths = two_stage.deepen_documents(
        GENERIC_Q, [a], use_llm=False, stage1_chunks={a: ids_a}, stage1=resp_a,
    )
    assert depths[0].mode == "stage1" and "not eligible" in depths[0].reason


def test_escalation_uses_heuristic_even_with_llm(kb, monkeypatch):
    sids = list(kb["patents"])
    a, b, c = sids[0], sids[1], sids[2]
    ids_a, resp_a = _fake_stage1(a, 4)
    ids_b, resp_b = _fake_stage1(b, 2)
    ids_c, resp_c = _fake_stage1(c, 2)
    calls: list[str] = []

    def _recording_llm(prompt: str):
        calls.append(prompt)
        raise AssertionError("escalated navigation must not call the LLM")

    _enable(monkeypatch, FORMUNEX_PAGEINDEX_HEURISTIC_ESCALATION_ENABLED="true")
    depths = two_stage.deepen_documents(
        GENERIC_Q, [a, b, c], llm=_recording_llm,
        stage1_chunks={a: ids_a, b: ids_b, c: ids_c},
        stage1=resp_a + resp_b + resp_c,
    )
    assert depths[0].mode == "pageindex"
    assert depths[0].navigation is not None and depths[0].navigation.mode == "heuristic"
    assert calls == []


def test_escalation_ignored_for_short_documents(kb, monkeypatch):
    short = kb["short"]
    ids, resp = _fake_stage1(short, 3)  # share 1.0 but 3 pages < 20
    _enable(monkeypatch, FORMUNEX_PAGEINDEX_HEURISTIC_ESCALATION_ENABLED="true")
    depths = two_stage.deepen_documents(
        GENERIC_Q, [short], use_llm=False, stage1_chunks={short: ids}, stage1=resp,
    )
    assert depths[0].mode == "stage1" and "3 pages < 20" in depths[0].reason


def test_classifier_passed_query_unchanged_by_escalation_switch(kb, monkeypatch):
    sid = next(s for s, p in kb["patents"].items() if p.source_id == "pat-cn-basic")
    _enable(monkeypatch, FORMUNEX_PAGEINDEX_HEURISTIC_ESCALATION_ENABLED="true")
    depths = two_stage.deepen_documents(EXTRACTION_Q, [sid], use_llm=False)
    assert depths[0].mode == "pageindex"
    assert depths[0].reason == "24 pages"  # classifier path, not the escalation path
