"""Tests for grounded recommend post-check (Sprint 2)."""
from __future__ import annotations

from app.domain.schemas import Evidence, ProductDomain, RecommendedFormula, RecommendedFormulaComponent
from app.services.grounded_recommend import ground_recommended_formulas


def _ev(ident: str, snippet: str) -> Evidence:
    return Evidence(
        source="patent",
        identifier=ident,
        title=ident,
        snippet=snippet,
        relevance=0.9,
    )


def test_ground_strict_drops_unknown_ingredient_by_default():
    """A-5: strict 默认开 — 低可信度成分被剔除（而非仅标记），剔除记入 warnings。"""
    formulas = [
        RecommendedFormula(
            name="Test A",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(name="Zinc phosphate", weight_pct=10.0),
                RecommendedFormulaComponent(name="Imaginaryium-X999", weight_pct=5.0),
            ],
        )
    ]
    evidence = [_ev("US123", "Zinc phosphate 10 wt% in epoxy primer")]
    out, warnings = ground_recommended_formulas(formulas, evidence)
    assert [c.name for c in out[0].components] == ["Zinc phosphate"]
    assert out[0].components[0].grounding_confidence == "high"
    assert any("Imaginaryium-X999" in w and "剔除" in w for w in warnings)
    assert any("已剔除低可信度成分" in w for w in out[0].warnings)


def test_ground_non_strict_marks_unknown_ingredient_low_confidence():
    """strict=False 保留旧行为：仅标记低可信度成分。"""
    formulas = [
        RecommendedFormula(
            name="Test A",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(name="Zinc phosphate", weight_pct=10.0),
                RecommendedFormulaComponent(name="Imaginaryium-X999", weight_pct=5.0),
            ],
        )
    ]
    evidence = [_ev("US123", "Zinc phosphate 10 wt% in epoxy primer")]
    out, warnings = ground_recommended_formulas(formulas, evidence, strict=False)
    assert out[0].components[0].grounding_confidence == "high"
    assert out[0].components[1].grounding_confidence == "low"
    assert warnings


def test_catalog_ingredient_gets_high_confidence_without_evidence():
    formulas = [
        RecommendedFormula(
            name="Test B",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(name="Bisphenol-A epoxy (DGEBA)", weight_pct=40.0),
            ],
        )
    ]
    out, _ = ground_recommended_formulas(formulas, [])
    assert out[0].components[0].grounding_confidence == "high"


def test_cas_in_evidence_yields_high_confidence():
    formulas = [
        RecommendedFormula(
            name="Test C",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(
                    name="Zinc phosphate",
                    cas_no="7779-90-0",
                    weight_pct=10.0,
                ),
            ],
        )
    ]
    evidence = [
        _ev("US9982145B2", "Zinc rich primer formulation with CAS 7779-90-0 inhibitor"),
    ]
    out, _ = ground_recommended_formulas(formulas, evidence)
    assert out[0].components[0].grounding_confidence == "high"


def test_weak_name_overlap_without_cas_is_low():
    formulas = [
        RecommendedFormula(
            name="Test D",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(name="Obscure additive QZX-42", weight_pct=1.0),
            ],
        )
    ]
    evidence = [_ev("US999", "General polymer coatings market report overview")]
    # strict 默认：唯一成分低可信度 → 整个配方被剔除。
    out, warnings = ground_recommended_formulas(formulas, evidence)
    assert out == []
    assert any("整个配方已剔除" in w for w in warnings)
    # strict=False：旧行为，仅标记。
    out, warnings = ground_recommended_formulas(formulas, evidence, strict=False)
    assert out[0].components[0].grounding_confidence == "low"
    assert warnings


def test_match_evidence_ids_per_evidence_not_global():
    """P1-4：单条证据匹配用该条目的 token 集合，而非全局并集。

    旧代码查 t in corpus（全部证据的 token 并集），导致任一 token 在语料
    任意处出现过，所有证据 id 都被挂成引用（假引用）。
    """
    from app.services.grounded_recommend import _evidence_corpus, _match_evidence_ids

    ev1 = _ev("CN123", "Epoxy resin cured with amine hardener for anticorrosion coating")
    ev2 = _ev("LIT456", "Alkaline degreaser for metal surface cleaning before coating")
    _, _, id_map, per_ev = _evidence_corpus([ev1, ev2])
    refs = _match_evidence_ids("Epoxy resin", per_ev, id_map)
    assert refs == ["CN123"], f"应只返回专利证据，实际: {refs}"
    # 反向：查除油剂只返回文献
    refs2 = _match_evidence_ids("degreaser", per_ev, id_map)
    assert refs2 == ["LIT456"], f"实际: {refs2}"


def test_v15_prefilled_hallucinated_ref_rejected():
    """v15: 预填的幻觉引用被剔除，回退到真实证据匹配。"""
    from app.services.grounded_recommend import _ground_component

    id_map = {"zinc phosphate cn123": "CN123"}
    comp = RecommendedFormulaComponent(
        name="Zinc Phosphate",
        zh_name="磷酸锌",
        evidence_refs=["doi:10.9999/fake.1234"],  # 幻觉 ID
    )
    out = _ground_component(
        comp, set(), set(), id_map, set(), set(),
        texts=[], per_evidence_tokens={"CN123": {"zinc", "phosphate"}},
    )
    assert "doi:10.9999/fake.1234" not in out.evidence_refs
    assert out.evidence_refs == ["CN123"]


def test_v15_prefilled_valid_ref_kept():
    """v15: 预填的真实引用保留。"""
    from app.services.grounded_recommend import _ground_component

    id_map = {"zinc phosphate cn123": "CN123"}
    comp = RecommendedFormulaComponent(
        name="Zinc Phosphate",
        zh_name="磷酸锌",
        evidence_refs=["CN123"],
    )
    out = _ground_component(
        comp, set(), set(), id_map, set(), set(),
        texts=[], per_evidence_tokens={"CN123": {"zinc", "phosphate"}},
    )
    assert out.evidence_refs == ["CN123"]


# ── P1-1 (review 2026-11-26)：名称↔CAS 一致性校验 ──────────────────────────


def test_p1_1_fictional_name_with_real_cas_is_not_high():
    """虚构名称借真实库内 CAS 不得拿 high：77-92-9 属于柠檬酸，不属于该名称。"""
    formulas = [
        RecommendedFormula(
            name="g-probe2",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(
                    name="Zqxwobble resinoid",
                    component_type="resin",
                    weight_pct=100.0,
                    cas_no="77-92-9",
                ),
            ],
        )
    ]
    # strict：整个配方剔除（此前判 high、零引用、零警告、配方保留）。
    out, warnings = ground_recommended_formulas(formulas, [], strict=True)
    assert out == []
    assert any("整个配方已剔除" in w for w in warnings)
    # 非 strict：仅标记 low，不标 high。
    out, _ = ground_recommended_formulas(formulas, [], strict=False)
    assert out[0].components[0].grounding_confidence == "low"
    assert out[0].components[0].evidence_refs == []


def test_p1_1_real_name_with_wrong_cas_is_rejected_with_warning():
    """真实名称配错 CAS 不得拿 high：磷酸锌登记 CAS 为 7779-90-0，非二甲苯 CAS。"""
    formulas = [
        RecommendedFormula(
            name="g-probe3",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(
                    name="Zinc phosphate",
                    component_type="inhibitor",
                    weight_pct=10.0,
                    cas_no="1330-20-7",
                ),
            ],
        )
    ]
    out, warnings = ground_recommended_formulas(formulas, [], strict=True)
    assert out == []
    assert any("不一致" in w and "1330-20-7" in w and "7779-90-0" in w for w in warnings)
    # 非 strict：保留但标 low，并带 cas_mismatch 明细。
    out, warnings = ground_recommended_formulas(formulas, [], strict=False)
    comp = out[0].components[0]
    assert comp.grounding_confidence == "low"
    assert any(d.hit_type == "cas_mismatch" for d in comp.evidence_details)
    assert any("不一致" in w for w in warnings)


def test_p1_1_right_name_right_cas_stays_high():
    """正例：名称正确 + CAS 正确仍 high（回归保护）。"""
    formulas = [
        RecommendedFormula(
            name="g-probe4",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(
                    name="Zinc phosphate",
                    component_type="inhibitor",
                    weight_pct=10.0,
                    cas_no="7779-90-0",
                ),
            ],
        )
    ]
    out, _ = ground_recommended_formulas(formulas, [], strict=True)
    assert out[0].components[0].grounding_confidence == "high"


def test_p1_1_cas_linked_to_name_in_evidence_stays_high():
    """库外名称 + 证据同条目中名称与 CAS 共现，仍可经 CAS 分支判 high。"""
    formulas = [
        RecommendedFormula(
            name="g-probe8",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(
                    name="Benzotriazole",
                    component_type="inhibitor",
                    weight_pct=1.0,
                    cas_no="95-14-7",
                ),
            ],
        )
    ]
    evidence = [
        _ev("US777", "Benzotriazole (CAS 95-14-7) inhibits copper corrosion."),
    ]
    out, _ = ground_recommended_formulas(formulas, evidence, strict=True)
    comp = out[0].components[0]
    assert comp.grounding_confidence == "high"
    assert any(d.hit_type == "cas" for d in comp.evidence_details)


def test_p1_1_component_in_catalog_requires_name_cas_consistency():
    """_component_in_catalog：单借 CAS 或单长 token 不再构成目录命中。"""
    from app.services.grounded_recommend import (
        _catalog_tokens,
        _component_in_catalog,
        _catalog_cas,
    )

    catalog = _catalog_tokens()
    catalog_cas = _catalog_cas()
    # 虚构名 + 真实 CAS：False（此前 True）。
    assert not _component_in_catalog(
        RecommendedFormulaComponent(name="Zqxwobble resinoid", cas_no="77-92-9"),
        catalog, catalog_cas,
    )
    # 真实名 + 错 CAS：False（此前 True，靠单长 token "phosphate"）。
    assert not _component_in_catalog(
        RecommendedFormulaComponent(name="Zinc phosphate", cas_no="1330-20-7"),
        catalog, catalog_cas,
    )
    # 真实名 + 对 CAS：True。
    assert _component_in_catalog(
        RecommendedFormulaComponent(name="Zinc phosphate", cas_no="7779-90-0"),
        catalog, catalog_cas,
    )
    # 真实名（无 CAS）：True（精确名匹配）。
    assert _component_in_catalog(
        RecommendedFormulaComponent(name="Zinc phosphate"),
        catalog, catalog_cas,
    )
