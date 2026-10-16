"""GB 30000 mixture classification engine — rule boundaries, label elements, transport.

Each case states the expected category from the GB 30000 / GHS Rev.4 formula
by hand; boundaries are tested on both sides. These are rule tests, not the
EHS-signed golden set the plan asks for before release (§5.5.2) — that set is
built from real formulations and reviewed by a specialist.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services.plm.ghs.engine import classify, inputs_hash
from app.services.plm.ghs.label_elements import H_TO_P, build_label
from app.services.plm.ghs.model import Component, ComponentHazard, HazardOutcome, MixtureProperties
from app.services.plm.ghs.ruleset import available_rulesets, load_ruleset
from app.services.plm.regulatory.hazard_classes import HAZARD_CLASSES

RES = Path(__file__).resolve().parents[1] / "app" / "resources" / "regulatory"
WATER = "7732-18-5"


def H(hazard_class, category, h_code=None, **kw):
    return ComponentHazard(hazard_class, category, h_code, **kw)


def mix(*parts, props=None, filler=True, **kw):
    """Components ``(pct, [hazards])`` topped up to 100 % with water."""
    comps = [Component(f"组分{i}", pct, cas=None, hazards=list(hz)) for i, (pct, hz) in enumerate(parts)]
    rest = 100 - sum(p for p, _ in parts)
    if filler and rest > 0:
        comps.append(Component("水", rest, WATER, hazards=[]))
    return classify(comps, props or MixtureProperties(), **kw)


def cats(result, hazard_class):
    return sorted(o.category for o in result.hazards if o.hazard_class == hazard_class)


# --------------------------------------------------------------------------- acute toxicity (GB 30000.18)


@pytest.mark.parametrize(("cat", "pct", "expected"), [
    ("1", 5, ["2"]),      # 100 / (5/0.5) = 10 mg/kg → cat 2
    ("1", 10, ["1"]),     # 100 / (10/0.5) = 5 → cat 1 (≤ 5 inclusive)
    ("2", 5, ["3"]),      # 100 / (5/5) = 100 → cat 3 (MSDS-Generator regression: 5 % "H300" + water is not H300)
    ("3", 50, ["3"]),     # 200 → cat 3
    ("4", 30, ["4"]),     # 1667 → cat 4
    ("4", 20, []),        # 2500 → not classified (cat 5 off)
    ("1", 0.9, []),       # below the 1 % relevance cut-off
])
def test_ate_oral(cat, pct, expected):
    assert cats(mix((pct, [H("acute_tox_oral", cat)])), "acute_tox_oral") == expected


def test_measured_ate_beats_category_point_estimate():
    r = mix((10, [H("acute_tox_oral", "3", ate_value=50, ate_unit="mg/kg")]))
    assert cats(r, "acute_tox_oral") == ["4"]  # 100 / (10/50) = 500
    assert r.trace[0].numbers["ate_mix"] == pytest.approx(500)


def test_unknown_toxicity_correction_above_ten_percent():
    comps = [Component("毒物", 30, hazards=[H("acute_tox_oral", "3")]), Component("未知物", 40, assessed=False),
             Component("水", 30, WATER)]
    r = classify(comps)
    # (100 − 40) / (30/100) = 200 → cat 3; without the correction it would be 333 → cat 4.
    assert cats(r, "acute_tox_oral") == ["3"]
    assert r.unknown_toxicity_pct["oral"] == 40
    assert any(g.code == "unknown_acute_toxicity" for g in r.data_gaps)


def test_unknown_up_to_ten_percent_uses_plain_formula():
    comps = [Component("毒物", 30, hazards=[H("acute_tox_oral", "3")]), Component("未知物", 10, assessed=False),
             Component("水", 60, WATER)]
    r = classify(comps)
    assert cats(r, "acute_tox_oral") == ["4"]  # 100 / 0.3 = 333
    assert not any(g.code == "unknown_acute_toxicity" for g in r.data_gaps)


def test_inhalation_uses_the_mixture_form():
    vapour = mix((50, [H("acute_tox_inhal", "3")]))                       # 3 mg/L → 6 → cat 3 (≤ 10)
    dust = mix((50, [H("acute_tox_inhal", "3")]), props=MixtureProperties(phys_state="solid"))  # 0.5 → 1.0 → cat 3
    assert cats(vapour, "acute_tox_inhal") == ["3"] and cats(dust, "acute_tox_inhal") == ["3"]
    assert "dust_mist" in dust.trace[0].explanation


# --------------------------------------------------------------------------- skin / eye (GB 30000.19 / .20)


@pytest.mark.parametrize(("parts", "expected"), [
    ([(5, "1")], ["1"]),
    ([(4.99, "1")], ["2"]),
    ([(1, "1")], ["2"]),
    ([(0.99, "1")], []),
    ([(10, "2")], ["2"]),
    ([(9.99, "2")], []),
    ([(0.5, "1"), (5, "2")], ["2"]),      # 10·0.5 + 5 = 10
    ([(5, "1A")], ["1A"]),
    ([(3, "1A"), (3, "1B")], ["1B"]),
    ([(6, "1C")], ["1C"]),
    ([(3, "1A"), (3, "1")], ["1"]),       # an unspecified sub-category leaves the mixture at "1"
])
def test_skin_additivity(parts, expected):
    r = mix(*[(p, [H("skin_corrosion", c)]) for p, c in parts])
    assert cats(r, "skin_corrosion") == expected


def test_skin_specific_concentration_limit():
    scl = [{"category": "1", "min_pct": 10}]
    assert cats(mix((6, [H("skin_corrosion", "1", scl=scl)])), "skin_corrosion") == ["2"]
    assert cats(mix((10, [H("skin_corrosion", "1", scl=scl)])), "skin_corrosion") == ["1"]


def test_non_additive_acids_and_bases():
    assert cats(mix((1, [H("skin_corrosion", "1", non_additive=True)])), "skin_corrosion") == ["1"]
    assert cats(mix((3, [H("skin_corrosion", "2", non_additive=True)])), "skin_corrosion") == ["2"]
    assert cats(mix((2.9, [H("skin_corrosion", "2", non_additive=True)])), "skin_corrosion") == []


@pytest.mark.parametrize("ph", [1.5, 2.0, 11.5, 12.8])
def test_extreme_ph_gives_corrosive_with_a_review_note(ph):
    r = mix(props=MixtureProperties(ph=ph))
    assert cats(r, "skin_corrosion") == ["1"] and cats(r, "eye_damage") == ["1"]
    assert any(g.code == "ph_without_reserve" for g in r.data_gaps)


def test_moderate_ph_is_not_a_hazard():
    assert mix(props=MixtureProperties(ph=2.1)).hazards == []


@pytest.mark.parametrize(("parts", "expected"), [
    ([("eye_damage", "1", 3)], ["1"]),
    ([("eye_damage", "1", 2.9)], ["2A"]),
    ([("skin_corrosion", "1", 3)], ["1"]),          # skin-corrosive ingredients count for the eye too
    ([("eye_damage", "2B", 10)], ["2B"]),
    ([("eye_damage", "2B", 6), ("eye_damage", "2A", 6)], ["2A"]),
    ([("eye_damage", "2", 9.9)], []),
])
def test_eye_additivity(parts, expected):
    r = mix(*[(p, [H(hc, c)]) for hc, c, p in parts])
    assert cats(r, "eye_damage") == expected


def test_corrosive_mixture_is_eye_category_1_but_label_shows_only_h314():
    r = mix((6, [H("skin_corrosion", "1B")]))
    assert cats(r, "skin_corrosion") == ["1B"] and cats(r, "eye_damage") == ["1"]
    assert "H314" in r.h_codes and "H318" not in r.h_codes


# --------------------------------------------------------------------------- sensitisation / CMR / STOT


@pytest.mark.parametrize(("hazard", "pct", "expected", "props"), [
    (H("skin_sensitizer", "1"), 1.0, ["1"], None),
    (H("skin_sensitizer", "1"), 0.9, [], None),
    (H("skin_sensitizer", "1A"), 0.1, ["1A"], None),
    (H("resp_sensitizer", "1"), 0.2, ["1"], MixtureProperties(phys_state="gas")),
    (H("resp_sensitizer", "1"), 0.2, [], None),
    (H("carcinogenicity", "1B"), 0.1, ["1B"], None),
    (H("carcinogenicity", "2"), 0.99, [], None),
    (H("carcinogenicity", "2"), 1.0, ["2"], None),
    (H("mutagenicity", "1A"), 0.1, ["1A"], None),
    (H("reproductive", "1B"), 0.3, ["1B"], None),
    (H("reproductive", "2"), 2.9, [], None),
    (H("stot_se", "1"), 10, ["1"], None),
    (H("stot_se", "1"), 5, ["2"], None),
    (H("stot_se", "2"), 10, ["2"], None),
    (H("stot_re", "1"), 1, ["2"], None),
    (H("stot_re", "1"), 0.9, [], None),
])
def test_concentration_limit_classes(hazard, pct, expected, props):
    assert cats(mix((pct, [hazard]), props=props), hazard.hazard_class) == expected


def test_lactation_is_an_additional_category():
    r = mix((0.5, [H("reproductive", "1B"), H("reproductive", "lact")]))
    assert cats(r, "reproductive") == ["1B", "lact"]
    assert {"H360", "H362"} <= set(r.h_codes)


def test_stot_se3_is_additive_per_effect():
    r = mix((12, [H("stot_se", "3", "H335")]), (9, [H("stot_se", "3", "H335,H336")]), (10, [H("stot_se", "3", "H336")]))
    effects = sorted(o.effect for o in r.hazards if o.hazard_class == "stot_se")
    assert effects == ["H335"]  # 21 % respiratory irritation; 19 % narcotic effects stays below 20 %


@pytest.mark.parametrize(("viscosity", "expected"), [(20.5, ["1"]), (21, []), (None, [])])
def test_aspiration_needs_viscosity(viscosity, expected):
    r = mix((10, [H("aspiration", "1")]), props=MixtureProperties(kinematic_viscosity_40c=viscosity))
    assert cats(r, "aspiration") == expected
    assert any(g.code == "viscosity_missing" for g in r.data_gaps) is (viscosity is None)


# --------------------------------------------------------------------------- aquatic / ozone (GB 30000.28 / .29)


@pytest.mark.parametrize(("hazard", "pct", "acute", "chronic"), [
    (H("aquatic_acute", "1"), 25, ["1"], []),
    (H("aquatic_acute", "1", m_factor_acute=10), 2.5, ["1"], []),
    (H("aquatic_acute", "1", m_factor_acute=10), 2.4, ["2"], []),   # 10·24 = 240 ≥ 25
    (H("aquatic_acute", "2"), 24, ["3"], []),                        # 10·24 = 240 ≥ 25
    (H("aquatic_chronic", "1"), 25, [], ["1"]),
    (H("aquatic_chronic", "1"), 2.5, [], ["2"]),
    (H("aquatic_chronic", "3"), 25, [], ["3"]),
    (H("aquatic_chronic", "4"), 25, [], ["4"]),
    (H("aquatic_chronic", "4"), 24, [], []),
    (H("aquatic_chronic", "1"), 0.05, [], []),                       # below the 0.1 % cut-off
])
def test_aquatic_summation(hazard, pct, acute, chronic):
    r = mix((pct, [hazard]))
    assert cats(r, "aquatic_acute") == acute and cats(r, "aquatic_chronic") == chronic


def test_ozone():
    assert cats(mix((0.1, [H("ozone", "1")])), "ozone") == ["1"]
    assert cats(mix((0.09, [H("ozone", "1")])), "ozone") == []


# --------------------------------------------------------------------------- physical (GB 30000.7 / .17)


@pytest.mark.parametrize(("fp", "ibp", "sustained", "expected"), [
    (22, 35, None, ["1"]), (22, 36, None, ["2"]), (23, 120, None, ["3"]), (60, 150, None, ["3"]),
    (61, 150, None, ["4"]), (93, 200, None, ["4"]), (94, 200, None, []),
    (50, 150, False, []),      # 35 < FP ≤ 60 and no sustained combustion → exempt
    (30, 150, False, ["3"]),   # exemption only above 35 °C
])
def test_flammable_liquid(fp, ibp, sustained, expected):
    r = mix(props=MixtureProperties(flash_point_c=fp, initial_boiling_point_c=ibp, sustained_combustion=sustained))
    assert cats(r, "flammable_liquid") == expected


def test_flash_point_missing_is_blocking_when_flammable_ingredients_present():
    r = mix((30, [H("flammable_liquid", "2")]))
    gap = next(g for g in r.data_gaps if g.code == "flash_point_missing")
    assert gap.severity == "blocking" and cats(r, "flammable_liquid") == []


def test_solids_are_never_flammable_liquids():
    r = mix((30, [H("flammable_liquid", "2")]), props=MixtureProperties(phys_state="solid", flash_point_c=10))
    assert cats(r, "flammable_liquid") == []


@pytest.mark.parametrize(("rate", "expected"), [(7.0, ["1"]), (6.25, [])])
def test_metal_corrosion_only_from_test(rate, expected):
    assert cats(mix(props=MixtureProperties(metal_corrosion_rate_mm_per_year=rate)), "metal_corrosive") == expected


def test_metal_corrosion_is_not_inferred_from_ph_or_ingredients():
    r = mix((10, [H("metal_corrosive", "1")]), props=MixtureProperties(ph=1.0))
    assert cats(r, "metal_corrosive") == []
    assert any(g.code == "metal_corrosion_test_missing" for g in r.data_gaps)


def test_oxidizer_ingredient_requires_a_test():
    r = mix((20, [H("oxidizing_liquid", "2")]))
    assert cats(r, "oxidizing_liquid") == []
    assert any(g.code == "physical_test_required:oxidizing_liquid" and g.severity == "blocking" for g in r.data_gaps)


# --------------------------------------------------------------------------- label elements


def _label(*outcomes):
    return build_label([HazardOutcome(hc, cat, [h] if h else [], "calculation") for hc, cat, h in outcomes])


def test_skull_suppresses_exclamation_mark():
    lab = _label(("acute_tox_oral", "3", "H301"), ("skin_corrosion", "2", "H315"))
    assert [p["code"] for p in lab["pictograms"]] == ["GHS06"]
    assert lab["signal_word"] == "危险"


def test_corrosion_suppresses_exclamation_for_irritation_only():
    assert [p["code"] for p in _label(("skin_corrosion", "1B", "H314"), ("eye_damage", "2A", "H319"))
            ["pictograms"]] == ["GHS05"]
    kept = _label(("skin_corrosion", "1B", "H314"), ("skin_sensitizer", "1", "H317"))
    assert [p["code"] for p in kept["pictograms"]] == ["GHS05", "GHS07"]


def test_respiratory_sensitiser_suppresses_exclamation_for_skin_sensitisation():
    lab = _label(("resp_sensitizer", "1", "H334"), ("skin_sensitizer", "1", "H317"))
    assert [p["code"] for p in lab["pictograms"]] == ["GHS08"]


def test_warning_only_and_no_signal_word():
    assert _label(("flammable_liquid", "3", "H226"))["signal_word"] == "警告"
    assert _label(("aquatic_chronic", "3", "H412"))["signal_word"] is None
    assert _label(("aquatic_chronic", "3", "H412"))["pictograms"] == []


def test_more_urgent_response_replaces_milder_one():
    lab = _label(("eye_damage", "1", "H318"), ("acute_tox_oral", "4", "H302"))
    assert "P310" in lab["p_codes"] and "P312" not in lab["p_codes"]


def test_label_keeps_at_most_six_and_covers_each_kind():
    lab = _label(("flammable_liquid", "2", "H225"), ("skin_corrosion", "1B", "H314"),
                 ("carcinogenicity", "1B", "H350"), ("aquatic_chronic", "1", "H410"))
    assert len(lab["label_p_codes"]) == 6
    assert {p[1] for p in lab["label_p_codes"]} == {"2", "3", "4", "5"}
    assert lab["label_p_codes"][0] == "P210"


def _bundled_codes(name):
    rows = json.loads((RES / name).read_text(encoding="utf-8"))["statements"]
    from app.services.plm.regulatory.seed import _code

    return {_code(r["code"]) for r in rows}


def test_every_p_code_used_is_a_gb_statement():
    p_codes = _bundled_codes("precaution_statements.json")
    used = {p for ps in H_TO_P.values() for p in ps}
    assert used - p_codes == set()


def test_every_h_code_is_a_gb_statement():
    h_codes = _bundled_codes("hazard_statements.json")
    mapped = {h for hc in HAZARD_CLASSES for h in hc.h_codes.values() if h}
    assert mapped - h_codes == set()
    assert set(H_TO_P) - h_codes == set()
    assert {h for h in mapped if re.fullmatch(r"H\d{3}", h)} <= set(H_TO_P)


# --------------------------------------------------------------------------- engine behaviour


def test_same_substance_through_two_materials_is_summed():
    comps = [Component("甲醛", 0.06, "50-00-0", substance_id="s-hcho", hazards=[H("carcinogenicity", "1B")], via=["树脂A"]),
             Component("甲醛", 0.06, "50-00-0", substance_id="s-hcho", hazards=[H("carcinogenicity", "1B")], via=["树脂B"]),
             Component("水", 99.88, WATER)]
    r = classify(comps)
    assert cats(r, "carcinogenicity") == ["1B"]  # 0.12 % ≥ 0.1 % only when counted together
    assert comps[0].pct == 0.06  # inputs are not mutated


def test_inputs_hash_tracks_inputs_and_rule_set():
    comps = [Component("A", 10, hazards=[H("skin_corrosion", "2")]), Component("水", 90, WATER)]
    rs = load_ruleset()
    h1 = inputs_hash(comps, MixtureProperties(), rs)
    assert h1 == classify(comps).inputs_hash
    comps[0].pct = 11
    assert inputs_hash(comps, MixtureProperties(), rs) != h1


def test_composition_total_is_checked():
    r = classify([Component("A", 80, hazards=[])])
    gap = next(g for g in r.data_gaps if g.code == "composition_not_100")
    assert gap.severity == "blocking"
    upper = classify([Component("A", 60), Component("B", 55)], composition_basis="upper_bound")
    assert not any(g.code == "composition_not_100" for g in upper.data_gaps)


def test_disclosure_list_uses_lower_threshold_for_cmr_and_sensitisers():
    r = mix((0.5, [H("skin_sensitizer", "1")]), (0.5, [H("skin_corrosion", "2")]), (2, [H("skin_corrosion", "2")]))
    assert sorted(d["pct"] for d in r.disclosure) == [0.5, 2]


def test_every_trace_entry_cites_a_standard():
    r = mix((5, [H("acute_tox_oral", "2")]), (6, [H("skin_corrosion", "1B")]), (25, [H("aquatic_chronic", "2")]),
            props=MixtureProperties(flash_point_c=30, initial_boiling_point_c=140))
    assert r.trace and all(t.standard_ref.startswith("GB 30000") for t in r.trace)
    assert all(t.explanation for t in r.trace)


def test_rule_set_is_versioned_and_loadable():
    assert "gb30000-2013.v1" in available_rulesets()
    rs = load_ruleset("gb30000-2013.v1")
    assert rs.version == "gb30000-2013.v1" and rs.status == "draft"
    with pytest.raises(FileNotFoundError):
        load_ruleset("gb30000-1999")


# --------------------------------------------------------------------------- transport (GB 6944 / GB 12268)


def _transport(*outcomes, props=None, components=None):
    from app.services.plm.ghs.model import ClassificationResult
    from app.services.plm.transport import derive_transport

    result = ClassificationResult("x", hazards=[HazardOutcome(hc, c, [], "calculation") for hc, c in outcomes])
    return derive_transport(result, props or MixtureProperties(), components or [Component("溶剂", 100, hazards=[H("flammable_liquid", "3")])])


@pytest.mark.parametrize(("outcomes", "props", "un", "cls", "pg", "sub"), [
    ([("flammable_liquid", "3")], None, "1993", "3", "III", None),
    ([("flammable_liquid", "2")], MixtureProperties(product_category="paint"), "1263", "3", "II", None),
    ([("flammable_liquid", "2"), ("skin_corrosion", "1B")], None, "2924", "3", "II", "8"),
    ([("flammable_liquid", "3"), ("skin_corrosion", "1B")], None, "2920", "8", "II", "3"),
    ([("flammable_liquid", "3"), ("acute_tox_oral", "2")], None, "2929", "6.1", "II", "3"),
    ([("acute_tox_oral", "3")], None, "2810", "6.1", "III", None),
    ([("skin_corrosion", "1A")], MixtureProperties(phys_state="solid"), "1759", "8", "I", None),
    ([("aquatic_chronic", "2")], None, "3082", "9", "III", None),
    ([("aquatic_acute", "1")], MixtureProperties(phys_state="solid"), "3077", "9", "III", None),
])
def test_transport_entry(outcomes, props, un, cls, pg, sub):
    t = _transport(*outcomes, props=props)
    assert (t["un_number"], t["transport_class"], t["packing_group"], t["subsidiary_risk"]) == (un, cls, pg, sub)
    assert t["needs_review"] is True


def test_not_regulated_and_single_substance_entry():
    assert _transport(("skin_corrosion", "2"))["regulated"] is False
    from app.services.plm.ghs.model import ClassificationResult
    from app.services.plm.transport import derive_transport

    ethanol = Component("乙醇", 100, "64-17-5", substance_id="s-etoh", hazards=[H("flammable_liquid", "2")])
    result = ClassificationResult("x", hazards=[HazardOutcome("flammable_liquid", "2", [], "measured")])
    t = derive_transport(result, MixtureProperties(), [ethanol], substance_entries={
        "s-etoh": {"un_number": "1170", "proper_shipping_name_zh": "乙醇", "proper_shipping_name_en": "ETHANOL"}})
    assert t["un_number"] == "1170" and t["technical_names"] == []


def test_nos_entry_names_technical_constituents():
    comps = [Component("二甲苯", 40, hazards=[H("flammable_liquid", "3")]), Component("乙酸丁酯", 30,
             hazards=[H("flammable_liquid", "3")]), Component("树脂", 30)]
    t = _transport(("flammable_liquid", "3"), components=comps)
    assert t["technical_names"] == ["二甲苯", "乙酸丁酯"]


# --------------------------------------------------------------------------- an end-to-end example


def test_solventborne_epoxy_primer():
    comps = [
        Component("二甲苯", 25, "1330-20-7", hazards=[
            H("flammable_liquid", "3", "H226"), H("acute_tox_dermal", "4", "H312"), H("acute_tox_inhal", "4", "H332"),
            H("skin_corrosion", "2", "H315"), H("aspiration", "1", "H304"), H("stot_se", "3", "H335,H336")]),
        Component("环氧树脂", 60, "25068-38-6", hazards=[
            H("skin_corrosion", "2", "H315"), H("eye_damage", "2", "H319"), H("skin_sensitizer", "1", "H317"),
            H("aquatic_chronic", "2", "H411")]),
        Component("磷酸锌", 15, "7779-90-0", hazards=[H("aquatic_acute", "1", "H400"), H("aquatic_chronic", "1", "H410")]),
    ]
    r = classify(comps, MixtureProperties(flash_point_c=27, initial_boiling_point_c=138, kinematic_viscosity_40c=500))
    assert {(o.hazard_class, o.category) for o in r.hazards} == {
        ("flammable_liquid", "3"), ("skin_corrosion", "2"), ("eye_damage", "2A"), ("skin_sensitizer", "1"),
        ("stot_se", "3"), ("aquatic_acute", "2"), ("aquatic_chronic", "2"),
    }
    assert r.signal_word == "警告" and [p["code"] for p in r.pictograms] == ["GHS02", "GHS07", "GHS09"]
    assert r.h_codes[0] == "H226" and "H304" not in r.h_codes  # viscous: no aspiration hazard
    assert r.transport["un_number"] == "1993" and r.transport["environmentally_hazardous"] is True
