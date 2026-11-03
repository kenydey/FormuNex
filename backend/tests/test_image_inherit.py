"""WeKnora 第一批 2.4 Phase A：图像证据载体 + 继承 + top-k 保护测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.schemas.chat import Evidence
from app.services.evidence_merge import inherit_image_evidence, merge_evidence
from app.services.kb_index import _chunk_to_evidence
from app.services.kg.retrieval import _apply_caps


def _ev(ident, snippet="文本", relevance=0.5, **kw):
    base = dict(source="kb", identifier=ident, title=f"T-{ident}",
                snippet=snippet, relevance=relevance)
    base.update(kw)
    return Evidence(**base)


def _kb(src, ord_no, snippet="文本", relevance=0.5, **kw):
    return _ev(f"kb:{src}#c{ord_no}", snippet, relevance, **kw)


# ── 载体填充 ──

def _chunk(block_type):
    return SimpleNamespace(
        source_id="s1", ord=0, heading_path=None, text="图注文本",
        page_no=None, page_end=None, paragraph_idx=None,
        block_type=block_type,
    )


def test_chunk_to_evidence_image_carrier():
    fig = _chunk_to_evidence(_chunk("figure"), {}, 0.5)
    assert fig.image_refs == ["kb:s1#c0"]
    assert fig.image_vector_match is True
    txt = _chunk_to_evidence(_chunk("text"), {}, 0.5)
    assert txt.image_refs == []
    assert txt.image_vector_match is False


# ── 继承函数 ──

def test_inherit_cases():
    kept = _kb("s1", 0)
    dropped = _kb("s1", 1, image_refs=["kb:s1#c1"], image_vector_match=True)
    out = inherit_image_evidence(kept, dropped)
    assert out.image_refs == ["kb:s1#c1"]
    assert out.image_vector_match is True
    assert out.kept_by == "image_vector"
    assert kept.image_refs == []  # 入参不动

    kept2 = _kb("s1", 0, image_refs=["kb:s1#c0"], kept_by="patent_stitch")
    out2 = inherit_image_evidence(kept2, dropped)
    assert out2.image_refs == ["kb:s1#c0", "kb:s1#c1"]
    assert out2.kept_by == "patent_stitch"  # 已有值不覆盖

    out3 = inherit_image_evidence(kept, _kb("s1", 2))
    assert out3.image_refs == [] and out3.kept_by is None


# ── _apply_caps 接线 ──

def test_apply_caps_dedup_inherits():
    kept = _kb("s1", 0, relevance=0.9)
    dup = _kb("s1", 0, relevance=0.8, image_refs=["kb:s1#c0"],
              image_vector_match=True)
    final, _n, _t = _apply_caps(
        [(0.9, kept, "c0"), (0.8, dup, "c0")],
        llm_cap=5, max_sources=5, trade_only=False, inherit_enabled=True,
    )
    assert len(final) == 1
    assert final[0][0].image_refs == ["kb:s1#c0"]


def test_apply_caps_switch_off():
    kept = _kb("s1", 0, relevance=0.9)
    dup = _kb("s1", 0, relevance=0.8, image_refs=["kb:s1#c0"],
              image_vector_match=True)
    final, _n, _t = _apply_caps(
        [(0.9, kept, "c0"), (0.8, dup, "c0")],
        llm_cap=5, max_sources=5, trade_only=False, inherit_enabled=False,
    )
    assert final[0][0].image_refs == []


def test_apply_caps_kept_by_survives_cap_and_max_sources_binds():
    high = _ev("a1", relevance=0.9, source="srcA")
    kept_item = _ev("b1", relevance=0.5, source="srcB", kept_by="image_vector")
    other = _ev("b2", relevance=0.4, source="srcB")
    pool = [(0.9, high, "a1"), (0.5, kept_item, "b1"), (0.4, other, "b2")]
    final, _n, _t = _apply_caps(pool, llm_cap=1, max_sources=2,
                                trade_only=False, inherit_enabled=True)
    assert [e.identifier for e, _ in final] == ["a1", "b1"]
    # max_sources=1 时 kept 组的新源进不来。
    final2, _n, _t = _apply_caps(pool, llm_cap=1, max_sources=1,
                                 trade_only=False, inherit_enabled=True)
    assert [e.identifier for e, _ in final2] == ["a1"]


# ── rerank_evidence 接线 ──

@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_rerank_tail_inherits_no_resurrection(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "search_rerank_enabled", True)
    monkeypatch.setattr(settings, "search_rerank_llm_batch", 1)

    head = _kb("s1", 0)
    tail_dup = _kb("s1", 0, image_refs=["kb:s1#c0"], image_vector_match=True)
    tail_other = _kb("s2", 0)

    def _fake_rerank(query, items, k=None):
        return ([SimpleNamespace(evidence=head)], {"applied": True})

    monkeypatch.setattr("app.services.rag.rerank_scored", _fake_rerank)
    from app.services.kb_index import rerank_evidence

    out = rerank_evidence("q", [head, tail_dup, tail_other])
    assert [e.identifier for e in out] == ["kb:s1#c0", "kb:s2#c0"]
    assert out[0].image_refs == ["kb:s1#c0"]


# ── merge_evidence 串联 ──

def test_merge_dropped_image_inherited():
    a = _kb("s1", 0, "第一段正文结束于关键词")
    b = _kb("s1", 1, "关键词第二段正文",
            image_refs=["kb:s1#c1"], image_vector_match=True)
    out = merge_evidence([a, b])
    assert len(out) == 1
    assert out[0].image_refs == ["kb:s1#c1"]
    assert out[0].image_vector_match is True
    out_off = merge_evidence([a, b], inherit_images=False)
    assert out_off[0].image_refs == []


def test_merge_expansion_inherits_neighbour_image():
    short = _kb("s1", 1, "短句")
    lookup = {"s1": {0: ("前邻块文本", "kb:s1#c0", True)}}
    out = merge_evidence(
        [short],
        neighbour_lookup=lambda src, o: lookup.get(src, {}).get(o),
        short_chars=400,
    )
    assert out[0].image_refs == ["kb:s1#c0"]
