"""Flammable aerosol (GB 30000.4): categories 1 / 2 / 3 + not applicable."""
from __future__ import annotations

from app.services.plm.ghs.engine import classify
from app.services.plm.ghs.model import Component, ComponentHazard, MixtureProperties


def _butane(pct):
    return Component("丁烷", pct, "106-97-8", hazards=[ComponentHazard("flammable_gas", "1", "H220")])


def _aerosol(result):
    return [h for h in result.hazards if h.hazard_class == "aerosol"]


def test_aerosol_category_1_by_heat_of_combustion():
    result = classify([_butane(90), Component("水", 10, "7732-18-5")],
                      MixtureProperties(phys_state="aerosol", heat_of_combustion_kj_g=45.0))
    got = _aerosol(result)
    assert len(got) == 1 and got[0].category == "1" and got[0].h_codes == ["H222"]
    assert result.signal_word == "危险"
    assert any(p["code"] == "GHS02" for p in result.pictograms)
    assert not any(g.code == "gas_or_aerosol" for g in result.data_gaps)


def test_aerosol_category_2_by_heat_of_combustion():
    result = classify([_butane(50), Component("水", 50, "7732-18-5")],
                      MixtureProperties(phys_state="aerosol", heat_of_combustion_kj_g=25.0))
    got = _aerosol(result)
    assert len(got) == 1 and got[0].category == "2" and got[0].h_codes == ["H223"]
    assert result.signal_word == "警告"


def test_aerosol_category_3_low_flammable_content():
    result = classify([_butane(1), Component("水", 99, "7732-18-5")],
                      MixtureProperties(phys_state="aerosol", heat_of_combustion_kj_g=10.0))
    got = _aerosol(result)
    assert len(got) == 1 and got[0].category == "3" and got[0].h_codes == ["H229"]
    assert result.signal_word is None  # category 3 carries no signal word
    assert not any(p["code"] == "GHS02" for p in result.pictograms)  # nor a pictogram


def test_aerosol_not_applicable_when_no_criterion_met():
    # 50 % flammable with heat of combustion 10 kJ/g: not category 1 / 2, and
    # above the 1 % ceiling for category 3 → not classified.
    result = classify([_butane(50), Component("水", 50, "7732-18-5")],
                      MixtureProperties(phys_state="aerosol", heat_of_combustion_kj_g=10.0))
    assert _aerosol(result) == []
    assert not any(g.code == "gas_or_aerosol" for g in result.data_gaps)


def test_aerosol_missing_test_data_is_a_blocking_gap_not_the_old_gas_gap():
    result = classify([_butane(50), Component("水", 50, "7732-18-5")],
                      MixtureProperties(phys_state="aerosol"))
    assert any(g.code == "aerosol_test_data_missing" and g.severity == "blocking" for g in result.data_gaps)
    assert not any(g.code == "gas_or_aerosol" for g in result.data_gaps)


def test_gas_still_gets_the_blocking_gap():
    result = classify([_butane(50), Component("水", 50, "7732-18-5")],
                      MixtureProperties(phys_state="gas"))
    assert any(g.code == "gas_or_aerosol" and g.severity == "blocking" for g in result.data_gaps)


def test_aerosol_category_1_by_ignition_distance():
    result = classify([_butane(50), Component("水", 50, "7732-18-5")],
                      MixtureProperties(phys_state="aerosol", ignition_distance_cm=80.0))
    got = _aerosol(result)
    assert len(got) == 1 and got[0].category == "1"


def test_aerosol_category_2_by_foam_test():
    result = classify([_butane(50), Component("水", 50, "7732-18-5")],
                      MixtureProperties(phys_state="aerosol",
                                        foam_test_result={"flame_height_cm": 5.0, "flame_duration_s": 3.0}))
    got = _aerosol(result)
    assert len(got) == 1 and got[0].category == "2"
