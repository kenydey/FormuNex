"""WeKnora 第一批 2.2：重排策略层（阈值 + 降级 + 保底 + MMR）测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.hybrid_search import apply_rerank_policy


def _run(texts, scores, **kw):
    chunks = [SimpleNamespace(text=t) for t in texts]
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return apply_rerank_policy(order, list(scores), chunks, **kw)


def test_threshold_on_normalized_scores():
    # λ=1.0 隔离 MMR，只测阈值。
    out = _run(["aa bb", "cc dd", "ee ff"], [1.0, 0.5, 0.1],
               threshold=0.2, mmr_lambda=1.0, top_k=10)
    assert out == [0, 1]


def test_rrf_scale_keeps_small_mass_scores():
    # RRF 质量和约 0.02 量级：不除以理论上限（scale=1）会被阈值清空，
    # 除以 2/61 后归一化分数 0.76 / 0.61，均高于 0.2。
    out = _run(["aa bb", "cc dd"], [0.025, 0.02],
               threshold=0.2, mmr_lambda=1.0, top_k=10, scale=2.0 / 61.0)
    assert out == [0, 1]
    out_raw = _run(["aa bb", "cc dd"], [0.025, 0.02],
                   threshold=0.2, mmr_lambda=1.0, top_k=10, scale=1.0)
    assert out_raw == []


def test_degrade_chain_to_floor():
    # 0.8 清空 → 0.56 仍空 → 0.392 仍空 → 地板 0.3 捞回最高分项。
    out = _run(["aa bb", "cc dd", "ee ff"], [0.3, 0.2, 0.1],
               threshold=0.8, mmr_lambda=1.0, top_k=10)
    assert out == [0]


def test_default_threshold_does_not_degrade():
    # 默认阈值 0.2 低于地板 0.3：过滤为空时不降级。
    out = _run(["aa bb", "cc dd"], [0.15, 0.1],
               threshold=0.2, mmr_lambda=1.0, top_k=10)
    assert out == []


def test_explicit_scope_keeps_best():
    out = _run(["aa bb", "cc dd"], [0.3, 0.2],
               threshold=0.8, degrade_floor=0.5, mmr_lambda=1.0,
               top_k=10, explicit_scope=True)
    assert out == [0]
    out_no_scope = _run(["aa bb", "cc dd"], [0.3, 0.2],
                        threshold=0.8, degrade_floor=0.5, mmr_lambda=1.0,
                        top_k=10, explicit_scope=False)
    assert out_no_scope == []


def test_mmr_prefers_unrelated_over_near_dup():
    texts = [
        "alpha beta gamma delta",
        "alpha beta gamma epsilon",
        "zinc phosphate coating steel",
    ]
    out = _run(texts, [0.9, 0.85, 0.8], mmr_lambda=0.7, top_k=2)
    assert out == [0, 2]


def test_candidate_pool_cap():
    texts = [f"token{i} unique{i}" for i in range(250)]
    scores = [1.0 - i * 0.001 for i in range(250)]
    out = _run(texts, scores, mmr_lambda=1.0, top_k=250, max_candidates=200)
    assert len(out) == 200
    assert 249 not in out


def test_all_zero_scores_pass_through():
    out = _run(["aa bb", "cc dd"], [0.0, 0.0], top_k=10)
    assert out == [0, 1]


def test_empty_and_zero_top_k():
    assert apply_rerank_policy([], [], []) == []
    assert _run(["aa bb"], [1.0], top_k=0) == []


# ── 调用方集成：fail-open 与开关回归（复用 test_kb_query_test 的 stores 形态） ──


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
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
    sid1 = src.create(filename="d1.md", title="防腐涂料研究",
                      source_kind="local", full_text="x", content_hash="h1")
    sid2 = src.create(filename="d2.md", title="烘焙食谱",
                      source_kind="local", full_text="y", content_hash="h2")
    chk.replace_for_source(sid1, [{"text": "环氧树脂是一种优异的防腐涂料成膜物质，添加磷酸锌可提升盐雾耐受时间。",
                                   "embedding": [1.0, 0.0], "embedding_model": "m"}])
    chk.replace_for_source(sid2, [{"text": "巧克力蛋糕配方需要面粉糖和鸡蛋黄油混合烘烤。",
                                   "embedding": [0.0, 1.0], "embedding_model": "m"}])
    monkeypatch.setattr("app.services.kb_index._embed_texts",
                        lambda texts, model_name=None: [[0.9, 0.1]])
    return src, chk


def test_policy_off_keeps_low_score_chunk(stores):
    from app.config import get_settings
    from app.services.hybrid_search import hybrid_search_scored

    settings = get_settings()
    settings.kb_rerank_enabled = False
    scored = hybrid_search_scored("防腐涂料 环氧 磷酸锌", top_k=5)
    assert len(scored) == 2


def test_policy_on_filters_low_score_chunk(stores):
    from app.services.hybrid_search import hybrid_search_scored

    scored = hybrid_search_scored("防腐涂料 环氧 磷酸锌", top_k=5)
    assert len(scored) == 1
    assert "环氧树脂" in scored[0].chunk.text


def test_policy_exception_fails_open(stores, monkeypatch):
    import app.services.hybrid_search as hs_mod
    from app.services.hybrid_search import hybrid_search_scored

    def _boom(*args, **kwargs):
        raise RuntimeError("policy boom")

    monkeypatch.setattr(hs_mod, "apply_rerank_policy", _boom)
    scored = hybrid_search_scored("防腐涂料 环氧 磷酸锌", top_k=5)
    assert len(scored) == 2
