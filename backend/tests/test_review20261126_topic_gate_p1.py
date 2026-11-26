"""2026-11-26 审查第一波 · 资料检索 P1-3 / P1-5 定向单测 (kb_ingest.py)。

P1-3: 中文 topicality 改分词/二字词计分(单字不再虚高), 英文轻量词干归一;
topic_gate 文献 kind 要求 n_high>=1, 低判别词不再单独放行。
P1-5: 反向词 electrode 移入词边界表 —— 拦 "electrode(s)" 但不拦
electrodeposition/electrodeposited; 并审计其余子串反向词的重叠
(doping 在子串表与词边界表重复计两次, 已去重)。
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services import kb_ingest
from app.services.literature import _keywords


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ev(title, snippet="", ident="10.1016/j.test.2024.000001"):
    return Evidence(
        source="OpenAlex", identifier=ident, title=title,
        snippet=snippet or title, relevance=0.9,
    )


# ── P1-3: 中文分词计分 ─────────────────────────────────────────────────────


def test_keywords_have_no_single_chinese_chars():
    kws = _keywords("铝合金碱性脱脂剂")
    assert kws, "查询分词不应为空"
    assert all(len(t) >= 2 for t in kws), f"单字仍在计分 token 中: {sorted(kws)}"
    assert {"铝合金", "碱性", "脱脂剂"} <= kws


def test_keywords_stem_english_plurals():
    assert _keywords("protective coatings") == _keywords("protective coating")
    assert "coating" in _keywords("epoxy coatings corrosion")
    assert "coatings" not in _keywords("epoxy coatings corrosion")


def test_topicality_welding_paper_below_threshold():
    weld = _ev("铝合金焊接接头性能研究")
    assert kb_ingest._topicality(weld, "铝合金碱性脱脂剂") < 0.45


def test_topicality_ontopic_paper_scores_high():
    ontopic = _ev("铝合金碱性脱脂剂配方研究", "碱性脱脂剂清洗铝合金表面")
    assert kb_ingest._topicality(ontopic, "铝合金碱性脱脂剂") >= 0.45


# ── P1-3: 主题门收紧 ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "铝合金焊接接头性能研究",  # 0 高判别词, 仅铝/合金 2 低判别词
        "铝合金焊接工艺参数优化",  # 同上(第二条焊接金标)
        "半导体器件中铝合金互连工艺研究",  # 反向词半导体 + 基材词
    ],
)
def test_topic_gate_rejects_substrate_only_literature(title):
    assert kb_ingest.topic_gate(title, kind="literature") is False


def test_topic_gate_still_passes_high_signal_literature():
    assert kb_ingest.topic_gate(
        "Advances in magnesium alloy AZ91 surface treatments"
    ) is True
    assert kb_ingest.topic_gate(
        "铝合金碱性脱脂剂配方研究", kind="literature"
    ) is True


def test_select_ingest_targets_rejects_welding_keeps_ontopic():
    weld = _ev(
        "铝合金焊接接头性能研究",
        ident="10.1016/j.weld.2024.123456",
    )
    ontopic = _ev(
        "铝合金碱性脱脂剂配方研究",
        "碱性脱脂剂清洗铝合金表面",
        ident="10.1016/j.deg.2024.000001",
    )
    out = kb_ingest.select_ingest_targets(
        [weld, ontopic], query="铝合金碱性脱脂剂", write_audit=False
    )
    got = [e.identifier for e, _ in out]
    assert "10.1016/j.weld.2024.123456" not in got
    assert "10.1016/j.deg.2024.000001" in got


# ── P1-5: electrode 词边界化 ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "Electrodeposition of zinc on steel substrate",
        "Electrodeposition coating for corrosion protection of magnesium alloy",
        "Electrodeposited nickel coating on copper substrate",
    ],
)
def test_topic_gate_passes_electrodeposition(title):
    assert kb_ingest.topic_gate(title, kind="literature") is True


@pytest.mark.parametrize(
    "title",
    [
        "Lithium-ion battery electrode degradation",
        "Porous carbon electrodes for supercapacitor devices",
    ],
)
def test_topic_gate_still_blocks_standalone_electrode(title):
    assert kb_ingest.topic_gate(title, kind="literature") is False


def test_topic_hits_electrodeposition_not_counted_as_block():
    n_high, _n_low, n_block = kb_ingest._topic_hits(
        "electrodeposition of zinc on steel substrate"
    )
    assert n_high == 1  # electrodeposit
    assert n_block == 0


def test_topic_hits_doping_counted_once():
    # 审计: doping 原在子串表与词边界表各一份, 一次命中计两次; 去重后计一次。
    _n_high, _n_low, n_block = kb_ingest._topic_hits("doping effects in magnesium")
    assert n_block == 1
