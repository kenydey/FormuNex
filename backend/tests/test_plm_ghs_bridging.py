"""GB 30000 bridging principles (GHS 1.3.2.2): 5 principles x (applies + does-not-apply)."""
from __future__ import annotations

import pytest

from app.services.plm.ghs.bridging import BridgingInput, ReferenceMixture
from app.services.plm.ghs.engine import classify
from app.services.plm.ghs.model import Component, ComponentHazard, HazardOutcome, MixtureProperties


def _comp(name, pct, hazard_class=None, category=None):
    hazards = [ComponentHazard(hazard_class, category, f"H314")] if hazard_class else []
    return Component(name, pct, hazards=hazards)


def _ref(comps, hazard_class, category):
    return ReferenceMixture(
        components=comps,
        hazards=[HazardOutcome(hazard_class, category, ["H314"], "measured")],
        ref_id="ref-1",
    )


def _skin_cat(result):
    return [h for h in result.hazards if h.hazard_class == "skin_corrosion"]


def _assert_bridged(result, category):
    got = _skin_cat(result)
    assert len(got) == 1 and got[0].category == category and got[0].basis == "bridging"
    assert any(t.rule.startswith("bridging:") for t in result.trace)


def _assert_calculated(result):
    got = _skin_cat(result)
    assert got and all(h.basis != "bridging" for h in got)


# --------------------------------------------------------------------------- dilution
# Reference: 10 % NaOH (skin 1) + 90 % water. Target 3 % NaOH + 97 % water is a
# dilution (water is not classified in skin_corrosion); calculated alone it
# would be category 2 (3 % < 5 %), bridging takes the reference's category 1.


def test_dilution_applies():
    ref = _ref([_comp("氢氧化钠", 10, "skin_corrosion", "1"), _comp("水", 90)], "skin_corrosion", "1")
    result = classify([_comp("氢氧化钠", 3, "skin_corrosion", "1"), _comp("水", 97)],
                      MixtureProperties(), bridging=BridgingInput("dilution", [ref]))
    _assert_bridged(result, "1")


def test_dilution_not_applicable_when_diluent_more_hazardous():
    # Target = reference x 0.3 + diluent; the diluent KOH is category 1A, more
    # severe than the least hazardous original ingredient (category 1).
    ref = _ref([_comp("氢氧化钠", 10, "skin_corrosion", "1"), _comp("水", 90)], "skin_corrosion", "1")
    target = [_comp("氢氧化钠", 3, "skin_corrosion", "1"), _comp("水", 67), _comp("氢氧化钾", 30, "skin_corrosion", "1A")]
    result = classify(target, MixtureProperties(), bridging=BridgingInput("dilution", [ref]))
    _assert_calculated(result)


# --------------------------------------------------------------------------- batching


def test_batching_applies():
    ref = _ref([_comp("氢氧化钠", 10, "skin_corrosion", "1"), _comp("水", 90)], "skin_corrosion", "1")
    result = classify([_comp("氢氧化钠", 10, "skin_corrosion", "1"), _comp("水", 90)],
                      MixtureProperties(), bridging=BridgingInput("batching", [ref]))
    _assert_bridged(result, "1")


def test_batching_not_applicable_when_content_drift_exceeds_tolerance():
    ref = _ref([_comp("氢氧化钠", 10, "skin_corrosion", "1"), _comp("水", 90)], "skin_corrosion", "1")
    result = classify([_comp("氢氧化钠", 12, "skin_corrosion", "1"), _comp("水", 88)],
                      MixtureProperties(), bridging=BridgingInput("batching", [ref]))
    _assert_calculated(result)


# --------------------------------------------------------------------------- concentration


def test_concentration_applies():
    ref = _ref([_comp("氢氧化钠", 10, "skin_corrosion", "1"), _comp("水", 90)], "skin_corrosion", "1")
    result = classify([_comp("氢氧化钠", 15, "skin_corrosion", "1"), _comp("水", 85)],
                      MixtureProperties(), bridging=BridgingInput("concentration", [ref]))
    _assert_bridged(result, "1")


def test_concentration_not_applicable_when_reference_not_highest_category():
    ref = _ref([_comp("刺激剂", 10, "skin_corrosion", "2"), _comp("水", 90)], "skin_corrosion", "2")
    result = classify([_comp("刺激剂", 15, "skin_corrosion", "2"), _comp("水", 85)],
                      MixtureProperties(), bridging=BridgingInput("concentration", [ref]))
    _assert_calculated(result)


# --------------------------------------------------------------------------- interpolation


def test_interpolation_applies():
    a = _ref([_comp("氢氧化钠", 10, "skin_corrosion", "1"), _comp("水", 90)], "skin_corrosion", "1")
    b = _ref([_comp("氢氧化钠", 20, "skin_corrosion", "1"), _comp("水", 80)], "skin_corrosion", "1")
    result = classify([_comp("氢氧化钠", 15, "skin_corrosion", "1"), _comp("水", 85)],
                      MixtureProperties(), bridging=BridgingInput("interpolation", [a, b]))
    _assert_bridged(result, "1")


def test_interpolation_not_applicable_when_target_outside_range():
    a = _ref([_comp("氢氧化钠", 10, "skin_corrosion", "1"), _comp("水", 90)], "skin_corrosion", "1")
    b = _ref([_comp("氢氧化钠", 20, "skin_corrosion", "1"), _comp("水", 80)], "skin_corrosion", "1")
    result = classify([_comp("氢氧化钠", 25, "skin_corrosion", "1"), _comp("水", 75)],
                      MixtureProperties(), bridging=BridgingInput("interpolation", [a, b]))
    _assert_calculated(result)


# --------------------------------------------------------------------------- substantially similar


def test_substantially_similar_applies():
    ref = _ref([_comp("氢氧化钠", 10, "skin_corrosion", "1"), _comp("水", 90)], "skin_corrosion", "1")
    result = classify([_comp("氢氧化钾", 10, "skin_corrosion", "1"), _comp("水", 90)],
                      MixtureProperties(), bridging=BridgingInput("substantially_similar", [ref]))
    _assert_bridged(result, "1")


def test_substantially_similar_not_applicable_when_replacement_differs():
    ref = _ref([_comp("氢氧化钠", 10, "skin_corrosion", "1"), _comp("水", 90)], "skin_corrosion", "1")
    result = classify([_comp("氢氧化钾", 10, "skin_corrosion", "2"), _comp("水", 90)],
                      MixtureProperties(), bridging=BridgingInput("substantially_similar", [ref]))
    _assert_calculated(result)


def test_unknown_principle_rejected():
    with pytest.raises(ValueError, match="未知的桥接原则"):
        classify([_comp("水", 100)], MixtureProperties(),
                 bridging=BridgingInput("unknown", []))
