"""QA-03（2026-11-26 审查第一波）：同步 / 流式精排统一开关语义。

- ``recall_and_rerank``（同步 / 流式共用入口）：开关只有一个
  ``chat_cross_encoder_enabled``（默认关 → 两路默认均 BM25-only）；
  开后后端默认 CE-only，LLM 精排只在显式指定时走（prefer="llm" 或
  search_rerank_backend="llm"）；候选数一律截到
  ``cross_encoder_max_candidates``；meta 含 applied/backend/candidates。
- 流式 ``_stream_answer_plan``：默认不再走 LLM 精排，plan 透出
  rerank meta（done.meta 可观测）。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.domain.schemas import Evidence
from app.services import rag


def _ev(i: int, rel: float = 0.5) -> Evidence:
    return Evidence(
        source="kb",
        identifier=f"id-{i}",
        title=f"t{i}",
        snippet=f"snippet {i}",
        relevance=rel,
    )


def _settings(**overrides):
    base = dict(
        chat_cross_encoder_enabled=False,
        chat_rerank_top_k=20,
        cross_encoder_max_candidates=30,
        cross_encoder_model="fake/ce",
        cross_encoder_rerank_enabled=True,
        search_rerank_backend="auto",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_recall_and_rerank_disabled_by_default_never_calls_llm(monkeypatch):
    cands = [_ev(0, 0.9), _ev(1, 0.2), _ev(2, 0.5)]
    monkeypatch.setattr(
        rag,
        "llm_rerank_scored",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM 精排默认不得调用")),
    )
    monkeypatch.setattr(
        rag,
        "_load_cross_encoder",
        lambda name: (_ for _ in ()).throw(AssertionError("CE 默认不得加载")),
    )
    items, meta = rag.recall_and_rerank("q", cands, k=3, settings=_settings())
    assert meta["applied"] is False
    assert meta["backend"] == "none"
    assert meta["reason"] == "chat_rerank_disabled"
    assert [it.evidence.identifier for it in items] == ["id-0", "id-1", "id-2"]


def test_recall_and_rerank_ce_only_when_enabled(monkeypatch):
    cands = [_ev(0, 0.9), _ev(1, 0.2), _ev(2, 0.5)]

    class FakeCE:
        def predict(self, pairs):
            return [0.1, 5.0, 0.2]

    monkeypatch.setattr(rag, "cross_encoder_available", lambda: True)
    monkeypatch.setattr(rag, "cross_encoder_model_cached", lambda: True)
    monkeypatch.setattr(rag, "_load_cross_encoder", lambda name: FakeCE())
    monkeypatch.setattr(
        rag,
        "llm_rerank_scored",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("CE 可用时不得回退 LLM")),
    )
    items, meta = rag.recall_and_rerank(
        "q", cands, k=2, settings=_settings(chat_cross_encoder_enabled=True)
    )
    assert meta["applied"] is True
    assert meta["backend"] == "cross_encoder"
    assert meta["prefer"] == "cross_encoder"
    assert items[0].evidence.identifier == "id-1"


def test_recall_and_rerank_truncates_candidates_to_max(monkeypatch):
    seen: dict = {}

    class FakeCE:
        def predict(self, pairs):
            seen["n"] = len(pairs)
            return [float(i) for i in range(len(pairs))]

    monkeypatch.setattr(rag, "cross_encoder_available", lambda: True)
    monkeypatch.setattr(rag, "cross_encoder_model_cached", lambda: True)
    monkeypatch.setattr(rag, "_load_cross_encoder", lambda name: FakeCE())
    cands = [_ev(i, 0.5) for i in range(40)]
    items, meta = rag.recall_and_rerank(
        "q",
        cands,
        k=5,
        settings=_settings(chat_cross_encoder_enabled=True, cross_encoder_max_candidates=30),
    )
    assert seen["n"] == 30, "精排候选数须截到 cross_encoder_max_candidates"
    assert meta["candidates"] == 30
    assert meta["truncated_to"] == 30
    assert len(items) == 5


def test_recall_and_rerank_explicit_llm_opt_in(monkeypatch):
    cands = [_ev(0, 0.9), _ev(1, 0.2)]

    def fake_llm(query, candidates, k=6, req=None):
        return (
            [
                rag.RerankScoredItem(evidence=candidates[1], score=0.9, original_index=1),
                rag.RerankScoredItem(evidence=candidates[0], score=0.1, original_index=0),
            ][:k],
            True,
        )

    monkeypatch.setattr(rag, "llm_rerank_scored", fake_llm)
    items, meta = rag.recall_and_rerank(
        "q", cands, k=2, settings=_settings(), prefer="llm"
    )
    assert meta["applied"] is True
    assert meta["backend"] == "llm"
    assert items[0].evidence.identifier == "id-1"


def test_stream_plan_default_no_llm_rerank_and_meta_observable(monkeypatch):
    """流式默认不精排（开关关），plan 透出 rerank meta，BM25 序不变。"""
    import app.services.chat_orchestration as chat_orch

    srcs = [_ev(0, 0.5), _ev(1, 0.5), _ev(2, 0.5)]  # 等 relevance：query 压缩默认开会按分重排，等分可保 BM25 序

    class _FakeStore:
        def ingest(self, sources):
            pass

        def query(self, retrieval_query, k=0):
            return list(srcs)[:k] if k else list(srcs)

    monkeypatch.setattr(
        chat_orch, "_augment_with_kb", lambda q, s, **kw: (list(s) + list(srcs), len(srcs), None, None)
    )
    import app.services.rag as rag_mod

    monkeypatch.setattr(rag_mod, "build_store", lambda: _FakeStore())
    monkeypatch.setattr(
        rag_mod,
        "llm_rerank_scored",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("流式默认不得走 LLM 精排")),
    )
    import app.services.chat_context as chat_context

    monkeypatch.setattr(chat_context, "rewrite_query", lambda q, h, ce=None, **kw: (q, ""))
    monkeypatch.setattr(chat_context, "trim_history", lambda h, **kw: [])
    import app.services.chat_clarify as chat_clarify

    monkeypatch.setattr(chat_clarify, "detect_clarification", lambda *a, **kw: None)
    import app.services.connectors_builtin as connectors_builtin

    monkeypatch.setattr(connectors_builtin, "gather_connector_evidence", lambda *a, **kw: [])
    import app.services.text2sql as text2sql

    monkeypatch.setattr(text2sql, "hybrid_answer", lambda *a, **kw: {})

    req = SimpleNamespace(
        question="盐雾性能如何？",
        history=[],
        clarified_entities=None,
        sources=[],
        project_id=None,
        include_entity_resolution=False,
        selected_connectors=[],
        selected_skills=[],
        selected_mcp_servers=[],
        mode="chat",
        structure=None,
        domain="coating",
    )
    settings = SimpleNamespace(
        chat_rerank_candidates=20,
        chat_rerank_top_k=20,
        chat_skills_runtime_enabled=False,
        chat_cross_encoder_enabled=False,
        cross_encoder_max_candidates=30,
        search_rerank_backend="auto",
    )
    plan = chat_orch._stream_answer_plan(req, settings)
    assert plan["rerank"]["applied"] is False
    assert plan["rerank"]["backend"] == "none"
    assert [e.identifier for e in plan["relevant"]] == ["id-0", "id-1", "id-2"]
