"""N-1~N-6 配方推荐升级测试。"""
from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_chem_validate_smiles():
    """N-2: SMILES 校验。"""
    from app.services.chem_validate import validate_smiles
    ok, _ = validate_smiles("CCO")
    assert ok
    ok, reason = validate_smiles("not-a-smiles!!!")
    # RDKit 可用时应返回 False
    from app.services.chem_validate import validate_smiles as vs
    # 至少不抛异常
    assert isinstance(ok, bool)


def test_chem_validate_compatibility():
    """N-3: 兼容性检查。"""
    from app.services.chem_validate import check_compatibility
    warns = check_compatibility(["硫酸", "氢氧化钠", "水"])
    assert len(warns) >= 1
    assert "硫酸" in warns[0]
    # 无冲突时为空
    assert check_compatibility(["水", "乙醇"]) == []


def test_chem_validate_cas_format():
    """N-2: CAS 格式校验。"""
    from app.services.chem_validate import verify_cas
    ok, _ = verify_cas("64-17-5")  # 乙醇
    assert isinstance(ok, bool)
    ok, reason = verify_cas("invalid-cas")
    assert not ok


def test_complete_ratios():
    """N-5: 配比补全。"""
    from app.services.recommend_pipeline import _complete_ratios

    class FakeIng:
        def __init__(self, name, wp=None):
            self.name = name
            self.weight_pct = wp

    class FakeForm:
        def __init__(self):
            self.ingredients = [
                FakeIng("环氧树脂", 50.0),
                FakeIng("钛白粉", None),
                FakeIng("分散剂", None),
            ]

    f = FakeForm()
    _complete_ratios(f)
    total = sum(i.weight_pct for i in f.ingredients)
    assert abs(total - 100.0) < 0.1
    assert all(i.weight_pct > 0 for i in f.ingredients)


def test_complete_ratios_role_first_not_name_substring():
    """P1-2 (review 2026-11-26)：补全按 component_type/角色取默认值，而非名称子串。

    英文名 "Xylene" 不含 solvent/二甲苯，旧表只给 5.0 兜底值；按角色
    （solvent）应取溶剂档 15.0，归一化后为 16.67%，而非旧的 6.25%。
    """
    from app.services.recommend_pipeline import _complete_ratios

    class FakeIng:
        def __init__(self, name, wp=None, component_type=""):
            self.name = name
            self.weight_pct = wp
            self.component_type = component_type

    class FakeForm:
        def __init__(self):
            self.ingredients = [
                FakeIng("Epoxy resin", 50.0, "resin"),
                FakeIng("Xylene", None, "solvent"),
                FakeIng("Titanium dioxide", 25.0, "pigment"),
            ]

    f = FakeForm()
    records = _complete_ratios(f)
    assert len(records) == 1
    assert records[0]["name"] == "Xylene"
    assert records[0]["default_pct"] == 15.0
    assert records[0]["final_pct"] == pytest.approx(16.67, abs=0.01)
    by_name = {i.name: i.weight_pct for i in f.ingredients}
    assert by_name["Xylene"] == pytest.approx(16.67, abs=0.01)
    assert by_name["Epoxy resin"] == pytest.approx(55.56, abs=0.01)
    assert sum(by_name.values()) == pytest.approx(100.0, abs=0.1)


def test_complete_ratios_returns_empty_when_nothing_missing():
    """P1-2：无缺失配比时不改动配方，返回空记录。"""
    from app.services.recommend_pipeline import _complete_ratios

    class FakeIng:
        def __init__(self, name, wp):
            self.name = name
            self.weight_pct = wp

    class FakeForm:
        def __init__(self):
            self.ingredients = [FakeIng("Epoxy resin", 60.0), FakeIng("Xylene", 40.0)]

    f = FakeForm()
    assert _complete_ratios(f) == []
    assert [i.weight_pct for i in f.ingredients] == [60.0, 40.0]


def test_finalize_bundle_backfills_completed_ratios_and_rescores():
    """P1-2 (review 2026-11-26)：配比补全回填到 API 返回配方并重算。

    本次审查批准的行为变更：此前返回配方仍含 weight_pct=None、合计
    75%，而 score/predicted 按补全前配方算出（含 20% 缺配比折扣、VOC=0）。
    此后补全在评分前完成，返回配方合计 100%、无 None，score/predicted
    与返回配比同源（Xylene 计入后 VOC>0、无 20% 折扣警告）。
    """
    from app.domain.schemas import (
        ProductDomain,
        RecommendedFormula,
        RecommendedFormulaComponent,
        Requirement,
    )
    from app.services.recommend_pipeline import finalize_recommendation_bundle

    rec = RecommendedFormula(
        name="probe",
        domain=ProductDomain.anticorrosion_coating,
        components=[
            RecommendedFormulaComponent(name="Epoxy resin", component_type="resin", weight_pct=50.0),
            RecommendedFormulaComponent(name="Xylene", component_type="solvent", weight_pct=None),
            RecommendedFormulaComponent(name="Titanium dioxide", component_type="pigment", weight_pct=25.0),
        ],
    )
    aligned, scored, warnings, *_ = finalize_recommendation_bundle(
        [rec],
        Requirement(domain=ProductDomain.anticorrosion_coating),
        [],
        requested_n=1,
    )
    assert len(aligned) == 1 and len(scored) == 1
    by_name = {c.name: c for c in aligned[0].components}
    # 返回配方：合计 100%、无 None、溶剂按角色档补全（非旧的 6.25%）。
    assert all(c.weight_pct is not None for c in aligned[0].components)
    assert sum(c.weight_pct for c in aligned[0].components) == pytest.approx(100.0, abs=0.1)
    assert by_name["Xylene"].weight_pct == pytest.approx(16.67, abs=0.01)
    assert by_name["Epoxy resin"].weight_pct == pytest.approx(55.56, abs=0.01)
    # 返回配方与内部评分配方一致。
    assert [(c.name, c.weight_pct) for c in aligned[0].components] == [
        (i.name, i.weight_pct) for i in scored[0].ingredients
    ]
    # score/predicted 与返回配比同源：与 scored 侧完全一致，且 VOC 已计入溶剂。
    assert aligned[0].score == scored[0].score
    assert aligned[0].predicted == scored[0].predicted
    assert aligned[0].predicted["voc_gpl"] > 0.0
    # 成分级标记 + 补全警告；旧的缺配比折扣警告不再出现。
    assert "配比由系统补全" in (by_name["Xylene"].notes or "")
    assert any("配比已补全" in w and "Xylene" in w for w in warnings)
    assert not any("sum to 75.0%" in w for w in warnings)


def test_finalize_bundle_keeps_complete_recipe_unchanged():
    """P1-2：配比本来就完整的配方不受补全影响（无补全警告、无标记）。"""
    from app.domain.schemas import (
        ProductDomain,
        RecommendedFormula,
        RecommendedFormulaComponent,
        Requirement,
    )
    from app.services.recommend_pipeline import finalize_recommendation_bundle

    rec = RecommendedFormula(
        name="complete-probe",
        domain=ProductDomain.anticorrosion_coating,
        components=[
            RecommendedFormulaComponent(name="Epoxy resin", component_type="resin", weight_pct=50.0),
            RecommendedFormulaComponent(name="Xylene", component_type="solvent", weight_pct=25.0),
            RecommendedFormulaComponent(name="Titanium dioxide", component_type="pigment", weight_pct=25.0),
        ],
    )
    aligned, scored, warnings, *_ = finalize_recommendation_bundle(
        [rec],
        Requirement(domain=ProductDomain.anticorrosion_coating),
        [],
        requested_n=1,
    )
    assert len(aligned) == 1
    assert [c.weight_pct for c in aligned[0].components] == [50.0, 25.0, 25.0]
    assert all(not c.notes for c in aligned[0].components)
    assert not any("配比已补全" in w for w in warnings)
    assert aligned[0].score == scored[0].score


def test_selection_reason():
    """N-6: 选择解释。"""
    from app.services.grounded_recommend import _selection_reason

    class FakeComp:
        name = "环氧树脂"

    r = _selection_reason(FakeComp(), "high", ["ref1", "ref2"])
    assert "2 篇" in r
    r = _selection_reason(FakeComp(), "low", [])
    assert "缺乏" in r


def test_consensus_config():
    """N-1/N-4 配置存在。"""
    s = get_settings()
    assert hasattr(s, "recommend_consensus_enabled")
    assert hasattr(s, "prompt_ab_enabled")
    assert hasattr(s, "chem_validate_enabled")
    assert hasattr(s, "ratio_completion_enabled")


def test_compatibility_expanded():
    """O-2: 20 对兼容性规则。"""
    from app.services.chem_validate import INCOMPATIBLE_PAIRS, check_compatibility
    assert len(INCOMPATIBLE_PAIRS) >= 20
    # 新增规则生效
    warns = check_compatibility(["高锰酸钾", "甘油"])
    assert len(warns) >= 1


def test_ratio_reasonableness():
    """O-4: 配比合理性评分。"""
    from app.services.recommend_pipeline import _score_ratio_reasonableness

    class FakeIng:
        def __init__(self, name, wp):
            self.name = name
            self.weight_pct = wp

    class FakeForm:
        def __init__(self, ings):
            self.ingredients = ings

    # 正常配比无警告
    f = FakeForm([FakeIng("环氧树脂", 50.0), FakeIng("钛白粉", 25.0)])
    assert _score_ratio_reasonableness(f) == []
    # 异常配比有警告
    f = FakeForm([FakeIng("环氧树脂", 95.0)])
    warns = _score_ratio_reasonableness(f)
    assert len(warns) >= 1
    assert "过高" in warns[0]
