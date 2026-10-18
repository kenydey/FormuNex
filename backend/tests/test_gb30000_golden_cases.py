"""GB 30000 golden cases (plan 1-8): the EHS-reviewable classification baseline.

Cases live in ``tests/golden/gb30000_golden_cases.yaml`` so an EHS specialist can
read, check and sign them without touching code. Each case's expected
categories were hand-derived from the GB 30000 / GHS Rev.4 rule text
(derivation in the YAML) — never copied from engine output. A mismatch
between a case and the engine means either the case derivation or the engine
is wrong; do not "fix" the case to match the engine without checking the
rule text.

Status: PENDING EHS SIGN-OFF. Until the sign-off sheet
(``docs/ehs-signoff-gb30000.md``) is signed, this set is a draft, not an
approved baseline.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.services.plm.ghs.bridging import BridgingInput, ReferenceMixture
from app.services.plm.ghs.engine import classify
from app.services.plm.ghs.model import Component, ComponentHazard, HazardOutcome, MixtureProperties

DATA = Path(__file__).resolve().parent / "golden" / "gb30000_golden_cases.yaml"


def _load_cases():
    with DATA.open(encoding="utf-8") as fh:
        payload = yaml.safe_load(fh)
    cases = payload["cases"]
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate golden case ids"
    for c in cases:
        assert c.get("source"), f"{c['id']} missing rule source"
        assert c.get("derivation"), f"{c['id']} missing hand derivation"
        assert c.get("expected") is not None or c.get("absent"), f"{c['id']} has no expectation"
    return cases


CASES = _load_cases()


def _hazard(h: dict) -> ComponentHazard:
    return ComponentHazard(
        h["hazard_class"], str(h["category"]), h.get("h_code"),
        scl=h.get("scl"), m_factor_acute=h.get("m_factor_acute"),
        m_factor_chronic=h.get("m_factor_chronic"),
        ate_value=h.get("ate_value"), ate_unit=h.get("ate_unit"),
        non_additive=bool(h.get("non_additive", False)))


def _props(raw: dict | None) -> MixtureProperties:
    raw = raw or {}
    foam = raw.get("foam_test_result")
    return MixtureProperties(
        phys_state=raw.get("phys_state", "liquid"),
        flash_point_c=raw.get("flash_point_c"),
        initial_boiling_point_c=raw.get("initial_boiling_point_c"),
        ph=raw.get("ph"),
        kinematic_viscosity_40c=raw.get("kinematic_viscosity_40c"),
        metal_corrosion_rate_mm_per_year=raw.get("metal_corrosion_rate_mm_per_year"),
        sustained_combustion=raw.get("sustained_combustion"),
        heat_of_combustion_kj_g=raw.get("heat_of_combustion_kj_g"),
        ignition_distance_cm=raw.get("ignition_distance_cm"),
        foam_test_result=dict(foam) if foam else None)


def test_golden_set_has_enough_cases():
    assert len(CASES) >= 60, f"golden set has {len(CASES)} cases, plan 1-8 requires >= 60"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_golden_case(case):
    comps = [Component(c["name"], float(c["pct"]), c.get("cas"),
                        hazards=[_hazard(h) for h in c.get("hazards", [])],
                        assessed=bool(c.get("assessed", True)))
             for c in case["components"]]
    total = sum(c.pct for c in comps)
    assert abs(total - 100.0) < 0.01, f"{case['id']}: components sum to {total}, must be 100"
    result = classify(comps, _props(case.get("props")))
    actual: dict[str, list[str]] = {}
    for h in result.hazards:
        actual.setdefault(h.hazard_class, []).append(h.category)
    for hazard_class, expected in (case.get("expected") or {}).items():
        got = sorted(actual.get(hazard_class, []), key=str)
        assert got == sorted(str(x) for x in expected), (
            f"{case['id']} {hazard_class}: expected {expected}, got {got} "
            f"(derivation: {case['derivation']})")
    for hazard_class in case.get("absent") or []:
        assert hazard_class not in actual, (
            f"{case['id']} {hazard_class}: expected absent, got {actual[hazard_class]}")


# --------------------------------------------------------------------------- bridging (GB 30000 / GHS 1.3.2.2)
# Bridging inputs cannot be expressed in the YAML data file, so these two
# cases live here; derivations mirror the existing bridging tests.

def _comp(name, pct, hazard_class=None, category=None):
    hazards = [ComponentHazard(hazard_class, category, "H314")] if hazard_class else []
    return Component(name, pct, hazards=hazards)


def _ref(comps, hazard_class, category):
    return ReferenceMixture(
        components=comps,
        hazards=[HazardOutcome(hazard_class, category, ["H314"], "measured")],
        ref_id="ref-1")


def test_golden_bridging_dilution_applies():
    """Reference 10 % NaOH (skin 1) + water; target 3 % NaOH + water is a
    dilution — calculated alone it would be skin 2 (3 % < 5 %), bridging takes
    the reference's category 1 (GHS 1.3.2.2 dilution)."""
    ref = _ref([_comp("氢氧化钠", 10, "skin_corrosion", "1"), _comp("水", 90)], "skin_corrosion", "1")
    result = classify([_comp("氢氧化钠", 3, "skin_corrosion", "1"), _comp("水", 97)],
                      MixtureProperties(), bridging=BridgingInput("dilution", [ref]))
    got = [h for h in result.hazards if h.hazard_class == "skin_corrosion"]
    assert len(got) == 1 and got[0].category == "1" and got[0].basis == "bridging"


def test_golden_bridging_dilution_rejected_when_diluent_more_hazardous():
    """Dilution does not apply when the diluent (30 % KOH, skin 1A) is more
    severe than the least hazardous original ingredient — the engine falls
    back to the calculated classification."""
    ref = _ref([_comp("氢氧化钠", 10, "skin_corrosion", "1"), _comp("水", 90)], "skin_corrosion", "1")
    target = [_comp("氢氧化钠", 3, "skin_corrosion", "1"), _comp("水", 67),
              _comp("氢氧化钾", 30, "skin_corrosion", "1A")]
    result = classify(target, MixtureProperties(), bridging=BridgingInput("dilution", [ref]))
    got = [h for h in result.hazards if h.hazard_class == "skin_corrosion"]
    assert got and all(h.basis != "bridging" for h in got)
