"""Batch 3 (GHS engine/rules): regressions for P1-C2~C7 and P2-C12."""
from __future__ import annotations

from app.services.plm.ghs.bridging import BridgingInput, ReferenceMixture
from app.services.plm.ghs.engine import classify
from app.services.plm.ghs.model import Component, ComponentHazard, HazardOutcome, MixtureProperties
from app.services.plm.regulatory.clp_atp import map_entry


def _cats(result, hazard_class):
    return sorted(o.category for o in result.hazards if o.hazard_class == hazard_class)


def _basis(result, hazard_class):
    return {o.category: o.basis for o in result.hazards if o.hazard_class == hazard_class}


# P1-C2: duplicate-component merge must union hazards, order-independent.
def test_merge_duplicates_unions_hazards_both_orders():
    for first, second in (
        ([], [ComponentHazard("skin_corrosion", "1", "H314")]),
        ([ComponentHazard("skin_corrosion", "1", "H314")], []),
    ):
        comps = [
            Component("X", 50, cas="123-45-6", hazards=list(first)),
            Component("X", 50, cas="123-45-6", hazards=list(second)),
        ]
        assert "1" in _cats(classify(comps, MixtureProperties()), "skin_corrosion")


# P1-C3: eye rule must apply the skin SCL fallback chain (1A=5 % at 4 %).
def test_eye_rule_uses_skin_subcategory_scl():
    h = ComponentHazard("skin_corrosion", "1A", "H314", scl=[{"category": "1A", "min_pct": 5.0}])
    result = classify([Component("A", 4, hazards=[h]), Component("水", 96, hazards=[])],
                      MixtureProperties())
    assert _cats(result, "skin_corrosion") == ["2"]
    assert "1" not in _cats(result, "eye_damage")


# P1-C4: generic-category-"1" SCL applies to 1A/1B in threshold classes.
def test_threshold_class_falls_back_to_generic_category_1_scl():
    h = ComponentHazard("carcinogenicity", "1A", "H350", scl=[{"category": "1", "min_pct": 0.01}])
    result = classify([Component("A", 0.05, hazards=[h]), Component("水", 99.95, hazards=[])],
                      MixtureProperties())
    assert _cats(result, "carcinogenicity") == ["1A"]
    plain = ComponentHazard("carcinogenicity", "1A", "H350")
    result2 = classify([Component("A", 0.05, hazards=[plain]), Component("水", 99.95, hazards=[])],
                       MixtureProperties())
    assert _cats(result2, "carcinogenicity") == []


# P1-C5: interpolation keeps every outcome of the reference class (1B + lact).
def test_interpolation_keeps_additional_categories():
    def ref(pct):
        return ReferenceMixture(
            components=[Component("A", pct, hazards=[]), Component("水", 100 - pct, hazards=[])],
            hazards=[HazardOutcome("reproductive", "1B", ["H360"], "measured"),
                     HazardOutcome("reproductive", "lact", ["H362"], "measured")],
            ref_id=f"ref-{pct}",
        )

    target = [Component("A", 15, hazards=[ComponentHazard("reproductive", "1B", "H360")]),
              Component("水", 85, hazards=[])]
    result = classify(target, MixtureProperties(),
                      bridging=BridgingInput("interpolation", [ref(10), ref(20)]))
    got = _basis(result, "reproductive")
    assert got.get("1B") == "bridging" and got.get("lact") == "bridging"


# P1-C6: dilution bridging must not wipe a physical determination (pH = 1).
def test_dilution_bridging_keeps_ph_determination():
    ref = ReferenceMixture(components=[Component("填充剂", 100, hazards=[])],
                           hazards=[], ref_id="ref-filler")
    target = [Component("填充剂", 90, hazards=[]), Component("稀释剂", 10, hazards=[])]
    result = classify(target, MixtureProperties(ph=1.0),
                      bridging=BridgingInput("dilution", [ref]))
    assert _basis(result, "skin_corrosion") == {"1": "ph"}
    assert _basis(result, "eye_damage") == {"1": "ph"}


# P1-C7: CLP ATP inhalation H-codes are H33x, not H32x.
def test_clp_atp_inhalation_codes_are_h33x():
    for code in ("H330", "H331", "H332"):
        route, _ = map_entry({"cas": "123-45-6", "hazard_class": "Acute Tox.",
                              "category": "1", "h_code": code})
        assert route == "acute_tox_inhal"
    try:
        map_entry({"cas": "123-45-6", "hazard_class": "Acute Tox.",
                   "category": "1", "h_code": "H320"})
    except ValueError:
        pass
    else:
        raise AssertionError("H320 must not route to inhalation")


# P2-C12: an ATE in the wrong unit records a data gap instead of silently
# falling back to the category point estimate.
def test_ate_unit_mismatch_records_gap():
    h = ComponentHazard("acute_tox_oral", "3", "H301", ate_value=100.0, ate_unit="ppmV")
    result = classify([Component("A", 50, hazards=[h]), Component("水", 50, hazards=[])],
                      MixtureProperties())
    assert any(g.code == "ate_unit_mismatch" for g in result.data_gaps)
