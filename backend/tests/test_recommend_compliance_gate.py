"""P1-3 (review 2026-11-26)：RoHS 不可行配方在推荐路径剔除，与 DOE 路径硬性判定对齐。

此前 finalize_recommendation_bundle 只把 check_compliance 的 infeasible
结果写进 warnings，配方照常返回并打分；DOE 路径（compliance_screen /
baybe_engine）则把 RoHS 作硬性 ok=false。修法：推荐路径默认剔除
infeasible 候选并记警告，warn 级（SVHC / 低于限值）保持仅警告。
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.schemas import (
    ProductDomain,
    RecommendedFormula,
    RecommendedFormulaComponent,
    Requirement,
)
from app.services.recommend_pipeline import finalize_recommendation_bundle


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _bundle(recs: list[RecommendedFormula]):
    return finalize_recommendation_bundle(
        recs,
        Requirement(domain=ProductDomain.anticorrosion_coating),
        [],
        requested_n=3,
        include_tradeoff=False,
    )


def test_rohs_infeasible_candidate_is_dropped_from_recommendations():
    """含 5% 铅铬黄（限值 0.1%）的配方不再返回、不得分，只留剔除警告。"""
    recs = [
        RecommendedFormula(
            name="rohs-probe",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(name="Epoxy resin", component_type="resin", weight_pct=70.0),
                RecommendedFormulaComponent(name="Lead chromate", component_type="pigment", weight_pct=5.0, cas_no="7758-97-6"),
                RecommendedFormulaComponent(name="Xylene", component_type="solvent", weight_pct=25.0),
            ],
        ),
    ]
    aligned, scored, warnings, *_ = _bundle(recs)
    assert aligned == [] and scored == []
    assert any("RoHS" in w and "已剔除" in w for w in warnings), warnings


def test_rohs_below_limit_and_svhc_stay_warn_only():
    """低于限值的 RoHS 命中与 SVHC 候选保持仅警告、不剔除（不升级）。"""
    recs = [
        RecommendedFormula(
            name="below-limit",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(name="Epoxy resin", component_type="resin", weight_pct=70.0),
                RecommendedFormulaComponent(name="Lead chromate", component_type="pigment", weight_pct=0.05, cas_no="7758-97-6"),
                RecommendedFormulaComponent(name="Xylene", component_type="solvent", weight_pct=29.95),
            ],
        ),
        RecommendedFormula(
            name="svhc-probe",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(name="Epoxy resin", component_type="resin", weight_pct=70.0),
                RecommendedFormulaComponent(name="Zinc molybdate", component_type="inhibitor", weight_pct=5.0, cas_no="13767-32-3"),
                RecommendedFormulaComponent(name="Xylene", component_type="solvent", weight_pct=25.0),
            ],
        ),
    ]
    aligned, scored, warnings, *_ = _bundle(recs)
    # 两者都保留（排序按分数，顺序不固定）；aligned/scored 位置配对。
    assert {r.name for r in aligned} == {"below-limit", "svhc-probe"}
    assert [r.name for r in aligned] == [f.name for f in scored]
    assert all(f.score is not None for f in scored)
    assert not any("已剔除" in w for w in warnings)


def test_rohs_drop_keeps_other_candidates_and_pairing():
    """一轮多候选：RoHS 不可行者剔除，其余保留，aligned/scored 仍位置配对。"""
    bad = RecommendedFormula(
        name="bad",
        domain=ProductDomain.anticorrosion_coating,
        components=[
            RecommendedFormulaComponent(name="Epoxy resin", component_type="resin", weight_pct=70.0),
            RecommendedFormulaComponent(name="Lead chromate", component_type="pigment", weight_pct=5.0, cas_no="7758-97-6"),
            RecommendedFormulaComponent(name="Xylene", component_type="solvent", weight_pct=25.0),
        ],
    )
    good = RecommendedFormula(
        name="good",
        domain=ProductDomain.anticorrosion_coating,
        components=[
            RecommendedFormulaComponent(name="Epoxy resin", component_type="resin", weight_pct=70.0),
            RecommendedFormulaComponent(name="Titanium dioxide", component_type="pigment", weight_pct=5.0),
            RecommendedFormulaComponent(name="Xylene", component_type="solvent", weight_pct=25.0),
        ],
    )
    aligned, scored, warnings, *_ = _bundle([bad, good])
    assert [r.name for r in aligned] == ["good"]
    assert [f.name for f in scored] == ["good"]
    assert aligned[0].score == scored[0].score
    assert any("bad" in w and "已剔除" in w for w in warnings), warnings
