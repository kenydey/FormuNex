"""FormuNex 新一轮全面审查（2026-11-26）第二波 B2：问答域批 Q2（P2）定向测试。

覆盖（报告 §5 批 Q2，跳过项见文件尾注释）：
- QA-06：identifier 规范 + 三处产出点改写 + _IDENT_RE 失败告警；
  date guard 接聊天收尾（提示级）。
- QA-08：rerank 阈值重定（分 fusion）+ 返回 meta。
- QA-09：动态 alpha 下移至 search_chunks_hybrid 解析处。
- QA-10：扫描截断进 notices；FAISS 补回 chunk 的 BM25 实算。
- QA-11：金标难负例 + 全量 graded（已满足，见断言）+ r == 1.0 改区间
  断言（在 test_golden_ranking_metrics.py）；生产库快照对照须生产库，跳过。
- QA-12：Wiki 封顶逃逸修复（未知不否决、空 indices 降级）+ 明确消费方。
- QA-13：会话内存同步/流式共享调用。

跳过（须 Cheng/EHS 定，一律不动）：QA-07 2-6 真重打分（决策 #11）、
QA-14 流式接 query_router 灰度方案（决策 #12）、QA-15 批 I（决策 #4）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.domain.chat_schemas import SourcedClaim
from app.domain.schemas import Evidence
from app.pipeline.claim_checker import ClaimVerdict, VerifiedClaim


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    """Isolated SourceStore + ChunkStore on a temp SQLite DB
    (复用 test_rerank_policy.py 的形态）。"""
    import app.db.chunk_store as chunk_store_mod
    import app.db.database as db_mod
    import app.db.source_store as source_store_mod
    from app.db.chunk_store import ChunkStore
    from app.db.database import Base, make_engine, make_session_factory
    from app.db.source_store import SourceStore

    engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    monkeypatch.setattr(
        "app.services.kb_index._embed_texts",
        lambda texts, model_name=None: [[0.9, 0.1]],
    )
    return src, chk


def _ev(identifier, source="kb", relevance=0.9):
    return Evidence(
        source=source, identifier=identifier, title="t",
        snippet="盐雾 720 h。", relevance=relevance,
    )


# ── QA-06：identifier 规范 ─────────────────────────────────────────────────


def test_identifier_spec_accepts_all_producer_namespaces():
    from app.services.kb_index import validate_evidence_identifier

    # 三处产出点改写后的形状 + 既有生产者（wiki/doi/chemlit/seed）：
    assert validate_evidence_identifier("kb:src-1#c0")
    assert validate_evidence_identifier("kb:src-1#c12")
    assert validate_evidence_identifier("wiki:concepts/epoxy")
    assert validate_evidence_identifier("doi:10.1000/xyz123")
    assert validate_evidence_identifier("chemlit:abcdef123456")
    assert validate_evidence_identifier("seed:epoxy-basics")
    assert validate_evidence_identifier("src-1#node-7")


def test_identifier_spec_rejects_legacy_shapes():
    from app.services.kb_index import validate_evidence_identifier

    # 旧形状（QA-06 的三条产出路径 + #p 变体）与已废弃的 nav: 前缀
    # （Cheng 2026-11-27 定：导航证据标识用裸格式，不要 nav: 前缀）：
    # 规范之外。
    assert not validate_evidence_identifier("kb:550e8400-e29b-41d4-a716-446655440000")
    assert not validate_evidence_identifier("kb:src-1#p03-01")
    assert not validate_evidence_identifier("nav:src-1#node-7")
    assert not validate_evidence_identifier("kb:src-1")
    assert not validate_evidence_identifier("")
    assert not validate_evidence_identifier(None)


def test_navigation_evidence_uses_bare_identifier():
    from app.services.pageindex import two_stage

    nav = SimpleNamespace(
        evidence=[
            {"node_id": "n1", "page_range": [3, 5], "title": "T", "text": "x"},
            {"node_id": "n2", "page_range": [6, 6], "title": "U", "text": "y"},
        ]
    )
    depths = [SimpleNamespace(source_id="s1", navigation=nav)]
    out = two_stage.navigation_evidence(depths, titles={"s1": "Doc"})
    assert [e.identifier for e in out] == ["s1#n1", "s1#n2"]
    from app.services.kb_index import validate_evidence_identifier

    assert all(validate_evidence_identifier(e.identifier) for e in out)
    assert all(e.page for e in out)


def test_table_evidence_uses_chunk_anchor(monkeypatch):
    from app.services import kb_index

    table = SimpleNamespace(
        table_id="s1#p03-01", source_id="s1", page_no=3,
        caption="表1", raw_markdown="| a |",
    )
    rows = [SimpleNamespace(page_no=3, ord=4), SimpleNamespace(page_no=5, ord=9)]
    monkeypatch.setattr(
        "app.services.pageindex.nav_layer.apply_nav",
        lambda q, ev: (list(ev), [], [table], {}),
    )
    monkeypatch.setattr(
        "app.db.chunk_store.get_chunk_store",
        lambda: SimpleNamespace(get_by_source=lambda sid: rows),
    )
    out = kb_index._apply_nav("q", [_ev("kb:s1#c0")], {"s1": {"title": "Doc"}})
    tables = [e for e in out if "表格" in (e.title or "")]
    assert len(tables) == 1
    # 表所在页（p3）的首个 chunk ord=4 为锚点。
    assert tables[0].identifier == "kb:s1#c4"
    assert tables[0].page == 3
    from app.services.kb_index import validate_evidence_identifier

    assert validate_evidence_identifier(tables[0].identifier)


def test_table_evidence_without_anchor_keeps_legacy_id_and_warns(monkeypatch, caplog):
    from app.services import kb_index

    table = SimpleNamespace(
        table_id="s1#p03-01", source_id="s1", page_no=3,
        caption="表1", raw_markdown="| a |",
    )
    monkeypatch.setattr(
        "app.services.pageindex.nav_layer.apply_nav",
        lambda q, ev: (list(ev), [], [table], {}),
    )
    monkeypatch.setattr(
        "app.db.chunk_store.get_chunk_store",
        lambda: SimpleNamespace(get_by_source=lambda sid: []),
    )
    import logging

    with caplog.at_level(logging.WARNING, logger="app.services.kb_index"):
        out = kb_index._apply_nav("q", [_ev("kb:s1#c0")], {"s1": {"title": "Doc"}})
    tables = [e for e in out if "表格" in (e.title or "")]
    assert len(tables) == 1
    # 召回优先于规范：无锚点时保留旧标识，不静默丢表，但必须告警。
    assert tables[0].identifier == "kb:s1#p03-01"
    assert "no chunk anchor" in caplog.text


def test_ident_re_failure_warns_for_kb_ids_only(monkeypatch, caplog):
    from app.services import kb_index

    monkeypatch.setattr(
        "app.db.chunk_store.get_chunk_store",
        lambda: SimpleNamespace(get_by_source=lambda sid: []),
    )
    import logging

    legacy = [_ev("kb:550e8400-e29b-41d4-a716-446655440000")]
    with caplog.at_level(logging.WARNING, logger="app.services.kb_index"):
        out = kb_index._stitch_patent_siblings(legacy, max_siblings=2, meta={})
    assert out == legacy
    assert "non-canonical kb identifier" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.services.kb_index"):
        kb_index._stitch_patent_siblings(
            [_ev("wiki:concepts/epoxy", source="wiki")], max_siblings=2, meta={}
        )
    # 非 kb: 命名空间本就不匹配 _IDENT_RE，不告警。
    assert "non-canonical kb identifier" not in caplog.text


# ── QA-06：date guard 接聊天收尾 ───────────────────────────────────────────


def test_future_dated_citation_notices():
    from app.services.citation_date_guard import future_dated_citation_notices

    answer = "研究表明涂层耐蚀性优异[^1]。\n\n[^1]: 张三等发表于 2028 年，防腐涂料研究。"
    notices = future_dated_citation_notices(answer)
    assert len(notices) == 1
    assert notices[0]["code"] == "future_dated_citation"
    assert notices[0]["footnote"] == 1
    assert notices[0]["year"] == 2028
    # f5480e1 回归：来源标识里的裸 2112 不算年份。
    assert future_dated_citation_notices("[^1]: 来源标识 2112 6ce-abc。") == []
    # 过去年份不报。
    assert future_dated_citation_notices("[^1]: 李四等发表于 2023 年。") == []


def test_stream_done_payload_includes_date_notice():
    import app.services.chat_orchestration as chat_mod

    req = SimpleNamespace(mode="chat")
    plan = {
        "question": "q", "prompt": "p", "sources": [], "kb_used": 1,
        "entity_resolution": None, "kg_stats": None, "clarification": None,
        "rewritten_query": None, "relevant": [_ev("kb:s1#c0")], "rerank": {},
        "mode": "chat", "selected_skills": [], "selected_mcp_servers": [],
        "data_sources": ["kb_evidence"],
    }
    payload = chat_mod._stream_done_payload(
        answer="结论[^1]。\n\n[^1]: 王五等发表于 2029 年，涂层研究。",
        citations=[_ev("kb:s1#c0")], kb_used=1, plan=plan, req=req,
        claims=[], sources_audit=None, gate_notices=[],
    )
    codes = [n.get("code") for n in payload["notices"]]
    assert "future_dated_citation" in codes


def test_run_chat_includes_date_notice(monkeypatch):
    import app.services.chat_orchestration as chat_mod
    from app.domain.chat_schemas import ChatRequest

    ev = _ev("kb:s1#c0")
    monkeypatch.setattr(
        chat_mod, "_augment_with_kb", lambda q, s, **kw: (list(s), 1, None, None)
    )
    monkeypatch.setattr(
        chat_mod,
        "answer_question",
        lambda *a, **k: ("盐雾 720 h[^1]。\n\n[^1]: 张三等发表于 2028 年。", [ev]),
    )
    monkeypatch.setattr(
        chat_mod, "_claims_and_audit", lambda *a, **k: ([], None, [])
    )
    req = ChatRequest(question="盐雾多久？", mode="chat", sources=[ev])
    resp = chat_mod.run_chat(req, None)
    assert resp.notices is not None
    assert any(n.get("code") == "future_dated_citation" for n in resp.notices)


# ── QA-08：rerank 阈值重定 + meta ─────────────────────────────────────────


def test_rerank_defaults_are_calibrated_per_fusion():
    from app.config import Settings

    s = Settings()
    assert s.kb_rerank_threshold == 0.10
    assert s.kb_rerank_threshold_rrf == 0.70


def test_rerank_meta_records_threshold_filtering():
    from app.services.hybrid_search import apply_rerank_policy, last_rerank_meta

    chunks = [SimpleNamespace(text=t) for t in ["aa bb", "cc dd", "ee ff"]]
    order = [0, 1, 2]
    out = apply_rerank_policy(
        order, [1.0, 0.5, 0.1], chunks, threshold=0.2, mmr_lambda=1.0, top_k=10
    )
    assert out == [0, 1]
    meta = last_rerank_meta()
    assert meta["threshold"] == 0.2
    assert meta["final_threshold"] == 0.2
    assert meta["degraded"] is False
    assert meta["threshold_dropped"] == 1
    assert meta["threshold_kept"] == 2
    assert meta["selected"] == 2


def test_rerank_meta_records_degrade_chain():
    from app.services.hybrid_search import apply_rerank_policy, last_rerank_meta

    chunks = [SimpleNamespace(text=t) for t in ["aa bb", "cc dd", "ee ff"]]
    out = apply_rerank_policy(
        [0, 1, 2], [0.3, 0.2, 0.1], chunks, threshold=0.8, mmr_lambda=1.0, top_k=10
    )
    assert out == [0]
    meta = last_rerank_meta()
    assert meta["degraded"] is True
    assert meta["final_threshold"] == 0.3
    assert meta["threshold_dropped"] == 2
    assert meta["threshold_kept"] == 1


def test_hybrid_search_uses_per_fusion_threshold(stores, monkeypatch):
    import app.services.hybrid_search as hs_mod
    from app.services.hybrid_search import hybrid_search_scored

    src, chk = stores
    sid = src.create(
        filename="d1.md", title="防腐涂料研究", source_kind="local",
        full_text="x", content_hash="h1",
    )
    chk.replace_for_source(sid, [{"text": "环氧树脂是一种优异的防腐涂料成膜物质，添加磷酸锌可提升盐雾耐受时间。",
                                  "embedding": [1.0, 0.0], "embedding_model": "m"}])
    # 另加 2 篇不含查询词的干扰 chunk：单 chunk 语料下 BM25 的 IDF 为负，
    # weighted combined < 0 会走 v7 fallback 旁路（策略层按设计不跑），
    # 3 篇语料才能让命中 chunk 的 BM25 为正、策略层可达。
    sid2 = src.create(filename="d2.md", title="烘焙", source_kind="local",
                      full_text="y", content_hash="h2")
    chk.replace_for_source(sid2, [{"text": "巧克力蛋糕配方需要面粉糖和鸡蛋黄油混合烘烤。",
                                   "embedding": [0.0, 1.0], "embedding_model": "m"}])
    sid3 = src.create(filename="d3.md", title="点蚀检测", source_kind="local",
                      full_text="z", content_hash="h3")
    chk.replace_for_source(sid3, [{"text": "不锈钢点蚀检测用电化学工作站做动电位极化扫描。",
                                   "embedding": [0.0, 0.0], "embedding_model": "m"}])
    captured: dict = {}

    def _capture(order, combined, chunks, **kw):
        captured.update(kw)
        return list(order)

    monkeypatch.setattr(hs_mod, "apply_rerank_policy", _capture)
    settings = get_settings()
    settings.kb_rerank_enabled = True

    settings.kb_hybrid_fusion = "rrf"
    hybrid_search_scored("防腐涂料 环氧 磷酸锌", top_k=5)
    assert captured["threshold"] == 0.70
    assert captured["fusion"] == "rrf"

    settings.kb_hybrid_fusion = "weighted"
    hybrid_search_scored("防腐涂料 环氧 磷酸锌", top_k=5)
    assert captured["threshold"] == 0.10
    assert captured["fusion"] == "weighted"


# ── QA-09：动态 alpha ─────────────────────────────────────────────────────


def test_resolve_hybrid_alpha_dynamic_rules():
    from app.services.hybrid_search import resolve_hybrid_alpha

    s = SimpleNamespace(kb_hybrid_alpha=0.85, dynamic_alpha_enabled=True)
    assert resolve_hybrid_alpha("环氧树脂防腐机理是什么", None, settings=s) == 0.95
    assert resolve_hybrid_alpha("salt spray", None, settings=s) == 0.70
    long_q = "how does epoxy coating corrosion protection work in marine environments today"
    assert resolve_hybrid_alpha(long_q, None, settings=s) == 0.95
    # 显式非默认值：调用方覆盖，动态不改。
    assert resolve_hybrid_alpha("环氧树脂防腐机理", 0.5, settings=s) == 0.5
    # 显式传入静态默认值：仍视为"未指定"，动态生效。
    assert resolve_hybrid_alpha("环氧树脂防腐机理", 0.85, settings=s) == 0.95


def test_resolve_hybrid_alpha_disabled_is_static():
    from app.services.hybrid_search import resolve_hybrid_alpha

    s = SimpleNamespace(kb_hybrid_alpha=0.85, dynamic_alpha_enabled=False)
    assert resolve_hybrid_alpha("环氧树脂防腐机理", None, settings=s) == 0.85


def test_search_chunks_hybrid_applies_dynamic_alpha(monkeypatch):
    from app.services import hybrid_search as hs_mod
    from app.services import kb_index

    captured: dict = {}

    def _fake_scored(query, top_k=10, alpha=None, **kw):
        captured["alpha"] = alpha
        return []

    monkeypatch.setattr(hs_mod, "hybrid_search_scored", _fake_scored)
    monkeypatch.setattr(kb_index, "kb_enabled", lambda: True)
    kb_index.search_chunks_hybrid("环氧树脂防腐机理是什么", k=4, source_meta={})
    # 生产链路此前把静态 0.85 显式传入，动态分支永不可达；现为 0.95。
    assert captured["alpha"] == 0.95


# ── QA-10：扫描截断 + FAISS 补回 ──────────────────────────────────────────


def test_scan_truncation_notice(monkeypatch):
    import app.services.chat_orchestration as chat_mod
    import app.services.hybrid_search as hs_mod

    monkeypatch.setattr(
        "app.services.kb_index.kb_vector_notice", lambda: {"vector_mode": "ok"}
    )
    monkeypatch.setattr(
        hs_mod, "last_scan_info",
        lambda: {"scanned": 5000, "limit": 5000, "truncated": True},
    )
    req = SimpleNamespace()
    notices = chat_mod._retrieval_degradation_notices(3, req)
    assert any(n.get("code") == "retrieval_scan_truncated" for n in notices)

    monkeypatch.setattr(
        hs_mod, "last_scan_info",
        lambda: {"scanned": 120, "limit": 5000, "truncated": False},
    )
    notices = chat_mod._retrieval_degradation_notices(3, req)
    assert not any(n.get("code") == "retrieval_scan_truncated" for n in notices)
    # kb 未参与检索时不提示（与 BM25-only 退化提示同口径）。
    assert chat_mod._retrieval_degradation_notices(0, req) == []


def test_faiss_extra_chunk_gets_real_bm25(stores, monkeypatch):
    """QA-10：FAISS 补回的 chunk 在 BM25 之前并入语料，其 bm25_score 与
    正常扫描同一 chunk 的值一致（非 0/None、非缺失）。此前若补回发生在
    BM25 之后，补回 chunk 的 bm25_score 会恒为 0 而误导融合；本测试以
    全量扫描为对照钉死"补回→分词→BM25"顺序。

    注意：2 篇小语料下 BM25 的 IDF 可为负（查询词在全部文档中出现），
    故断言"与对照相等"而非 "> 0"。"""
    import app.services.hybrid_search as hs_mod
    from app.services.hybrid_search import hybrid_search_scored

    src, chk = stores
    sid1 = src.create(filename="d1.md", title="磷酸锌", source_kind="local",
                      full_text="x", content_hash="h1")
    sid2 = src.create(filename="d2.md", title="环氧", source_kind="local",
                      full_text="y", content_hash="h2")
    chk.replace_for_source(sid1, [{"text": "磷酸锌是一种防锈颜料，环氧树脂防腐涂料中常用。",
                                   "embedding": [1.0, 0.0], "embedding_model": "m"}])
    chk.replace_for_source(sid2, [{"text": "环氧树脂固化后形成致密交联网络，防腐性能优异。",
                                   "embedding": [0.0, 1.0], "embedding_model": "m"}])
    all_rows = chk.all_chunks(limit=None)
    assert len(all_rows) == 2
    scanned = chk.all_chunks(limit=1)[0]
    extra = next(c for c in all_rows if c.id != scanned.id)

    settings = get_settings()
    settings.kb_rerank_enabled = False
    settings.kb_hybrid_fusion = "weighted"

    # 对照：全量扫描（无截断、无 FAISS），取同一 chunk 的 bm25_score。
    monkeypatch.setattr(hs_mod, "_faiss_vector_scores", lambda *a, **k: None)
    settings.kb_search_scan_limit = 5000
    control = hybrid_search_scored("环氧树脂 防腐", top_k=5)
    control_bm25 = {s.chunk.id: s.bm25_score for s in control}
    assert extra.id in control_bm25

    # 待测：扫描上限 1（截断）+ FAISS 补回同一 chunk。
    monkeypatch.setattr(
        hs_mod, "_faiss_vector_scores", lambda *a, **k: {extra.id: 0.9}
    )
    settings.kb_search_scan_limit = 1
    scored = hybrid_search_scored("环氧树脂 防腐", top_k=5)
    by_id = {s.chunk.id: s for s in scored}
    assert extra.id in by_id, "FAISS 补回的 chunk 须在结果中"
    assert by_id[extra.id].bm25_score is not None
    assert by_id[extra.id].bm25_score == control_bm25[extra.id]


# ── QA-11：金标难负例 + 全量 graded ───────────────────────────────────────


def test_golden_hard_negatives():
    from app.resources.golden_retrieval import golden_questions, sample_documents

    negs = [d for d in sample_documents if d.get("hard_negative")]
    assert len(sample_documents) == 83
    assert len(negs) == 10
    for d in negs:
        assert d.get("title") and d.get("text")
    # 难：每篇至少与 20 道金标题共享关键词。
    for d in negs:
        blob = d["title"] + d["text"]
        shared = sum(
            1 for q in golden_questions
            if any(kw in blob for kw in q["expected_keywords"])
        )
        assert shared >= 20, d["title"]
    # 不回答：没有任何一篇含某道题的全部期望关键词。
    for d in negs:
        blob = d["title"] + d["text"]
        for q in golden_questions:
            assert not all(kw in blob for kw in q["expected_keywords"]), (
                d["title"], q["question"]
            )


def test_golden_no_answer_questions_have_no_single_doc_answer():
    from app.resources.golden_retrieval import (
        golden_no_answer_questions, sample_documents,
    )

    assert len(golden_no_answer_questions) == 4
    for q in golden_no_answer_questions:
        assert q.get("expected") == "no_answer"
        kws = q["expected_keywords"]
        worst = max(
            sum(1 for kw in kws if kw in d["text"]) for d in sample_documents
        )
        assert worst <= 1, (q["question"], worst)


def test_golden_all_graded():
    """QA-11 全量 graded 在本轮核实已满足（250/250，RAG 升级批 0 已补）：
    此处钉死该状态——二值回退不得悄悄回来。"""
    from app.resources.golden_retrieval import golden_questions

    assert len(golden_questions) == 250
    assert all(q.get("graded_keywords") for q in golden_questions)
    for q in golden_questions:
        assert set(q["graded_keywords"]) == set(q["expected_keywords"])


# ── QA-12：Wiki 封顶逃逸修复 ─────────────────────────────────────────────


def _settings(cap=0.6):
    return SimpleNamespace(
        chat_claim_check_enabled=True, wiki_claim_confidence_cap=cap,
    )


def test_wiki_cap_empty_indices_downgrades_supported():
    from app.services.chat_claims import build_sourced_claims

    verified = [VerifiedClaim(text="涂层耐蚀。", verdict=ClaimVerdict.supported,
                               evidence_indices=[])]
    out = build_sourced_claims(
        "q", "涂层耐蚀。", [_ev("kb:s1#c0")], settings=_settings(), verified=verified
    )
    assert len(out) == 1
    # 逃逸 A：无据支撑的 supported 不得拿 0.9，降级为 weak。
    assert out[0].status == "weak"
    assert out[0].confidence == 0.4


def test_wiki_cap_wiki_only_capped():
    from app.services.chat_claims import build_sourced_claims

    wiki = _ev("wiki:concepts/epoxy", source="wiki")
    verified = [VerifiedClaim(text="环氧耐蚀。", verdict=ClaimVerdict.supported,
                               evidence_indices=[0])]
    out = build_sourced_claims(
        "q", "环氧耐蚀。", [wiki], settings=_settings(), verified=verified
    )
    assert out[0].status == "supported"
    assert out[0].confidence == 0.6


def test_wiki_cap_mixed_sources_not_capped():
    from app.services.chat_claims import build_sourced_claims

    sources = [_ev("wiki:concepts/epoxy", source="wiki"), _ev("kb:s1#c0")]
    verified = [VerifiedClaim(text="环氧耐蚀。", verdict=ClaimVerdict.supported,
                               evidence_indices=[0, 1])]
    out = build_sourced_claims(
        "q", "环氧耐蚀。", sources, settings=_settings(), verified=verified
    )
    assert out[0].confidence == 0.9


def test_wiki_only_sources_unknown_does_not_veto(caplog):
    from app.services.chat_claims import _wiki_only_sources

    import logging

    by_id = {"wiki:a": _ev("wiki:a", source="wiki")}
    with caplog.at_level(logging.WARNING, logger="app.services.chat_claims"):
        assert _wiki_only_sources(["wiki:a", "missing-id"], by_id) is True
    # 逃逸 B：未知不否决封顶，只记 warning。
    assert "unresolvable" in caplog.text
    assert _wiki_only_sources([], by_id) is False
    assert _wiki_only_sources(["missing-id"], by_id) is False


def test_wiki_cap_notice_is_consumed_by_answer_gates():
    import app.services.chat_orchestration as chat_mod

    settings = SimpleNamespace(
        chat_numeric_check_enabled=False,
        chat_conflict_section_enabled=False,
        chat_abstention_threshold=0.5,
        wiki_claim_confidence_cap=0.6,
    )
    claim = SourcedClaim(
        text="环氧耐蚀。", chunk_ids=["wiki:a"], confidence=0.6, status="supported",
    )
    _answer, _claims, abstained, notices = chat_mod._apply_answer_gates(
        "q", "环氧耐蚀。", [_ev("kb:s1#c0")], [claim], [], settings,
    )
    assert abstained is False
    assert any(n.get("code") == "wiki_only_support_capped" for n in notices)


# ── QA-13：会话内存同步/流式共享调用 ─────────────────────────────────────


def test_memory_block_shared_helper_falls_back_to_global(monkeypatch):
    import app.services.agent_memory as am
    from app.services.evidence_synthesis import memory_block_for_prompt

    monkeypatch.setattr(am, "build_memory_block",
                        lambda scope, sid, q, **k: "## Memory\n项目偏好" if scope == "project" else "")
    assert memory_block_for_prompt("q", "p1") == "## Memory\n项目偏好"

    monkeypatch.setattr(am, "build_memory_block",
                        lambda scope, sid, q, **k: "" if scope == "project" else "## Memory\n全局偏好")
    assert memory_block_for_prompt("q", "p1") == "## Memory\n全局偏好"
    assert memory_block_for_prompt("q", None) == "## Memory\n全局偏好"


def test_enrich_chat_prompt_includes_memory_block(monkeypatch):
    import app.services.agent_memory as am
    from app.services.evidence_synthesis import enrich_chat_prompt

    monkeypatch.setattr(am, "build_memory_block",
                        lambda scope, sid, q, **k: "## Memory\n记住：偏好无铬体系")
    out = enrich_chat_prompt(
        "base prompt", mode="chat", skill_ids=None,
        settings=SimpleNamespace(evidence_synthesis_mode="off"),
        project_id="p1",
    )
    assert "## Memory\n记住：偏好无铬体系" in out


def test_run_chat_prompt_includes_same_memory_block(monkeypatch):
    """同步 run_chat 与流式 enrich_chat_prompt 对同一记忆返回同一 block。"""
    import app.services.agent_memory as am
    import app.services.chat_orchestration as chat_mod
    from app.domain.chat_schemas import ChatRequest
    from app.services.evidence_synthesis import enrich_chat_prompt

    block = "## Memory\n记住：偏好无铬体系"
    monkeypatch.setattr(am, "build_memory_block",
                        lambda scope, sid, q, **k: block)
    monkeypatch.setattr(
        chat_mod, "_augment_with_kb", lambda q, s, **kw: (list(s), 1, None, None)
    )
    captured: dict = {}

    def _fake_answer(*a, **k):
        captured.update(k)
        return ("答。", [])

    monkeypatch.setattr(chat_mod, "answer_question", _fake_answer)
    monkeypatch.setattr(
        chat_mod, "_claims_and_audit", lambda *a, **k: ([], None, [])
    )
    ev = _ev("kb:s1#c0")
    req = ChatRequest(question="推荐无铬钝化方案", mode="chat", sources=[ev])
    chat_mod.run_chat(req, None)
    assert block in (captured.get("prompt_prefix") or "")

    stream_prompt = enrich_chat_prompt(
        "base prompt", mode="chat", skill_ids=None,
        settings=SimpleNamespace(evidence_synthesis_mode="off"),
        project_id="p1",
    )
    assert block in stream_prompt
