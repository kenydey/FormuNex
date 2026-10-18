"""Tests for the colorimetry service — CIE76 ΔE*ab + CIEDE2000 (2-21 口径统一)."""
import pytest

from app.domain import knowledge
from app.domain.schemas import ProductDomain, Requirement
from app.services.colorimetry import (
    WHITE_LAB,
    _colour_available,
    color_metrics,
    delta_e,
    delta_e_2000,
    delta_e_ab,
    mixture_lab,
)


def test_delta_e_ab_identical_is_zero():
    assert delta_e_ab((50.0, 20.0, -10.0), (50.0, 20.0, -10.0)) == pytest.approx(0.0, abs=1e-6)


def test_delta_e_ab_white_vs_black_large():
    de = delta_e_ab(WHITE_LAB, (0.0, 0.0, 0.0))
    assert de > 50.0, f"Expected large ΔE, got {de}"


def test_delta_e_ab_symmetry():
    a, b = (80.0, 5.0, -3.0), (70.0, -2.0, 10.0)
    assert delta_e_ab(a, b) == pytest.approx(delta_e_ab(b, a), abs=0.01)


def test_delta_e_ab_is_euclidean():
    # CIE76 = Euclidean distance in L*a*b*: (ΔL, Δa, Δb) = (3, 0, 4) → 5.
    assert delta_e_ab((50.0, 0.0, 0.0), (53.0, 0.0, 4.0)) == pytest.approx(5.0, abs=1e-9)


def test_delta_e_method_validation():
    with pytest.raises(ValueError):
        delta_e((50.0, 0.0, 0.0), (60.0, 5.0, 5.0), method="bogus")
    assert delta_e((50.0, 0.0, 0.0), (53.0, 0.0, 4.0), method="cie76") == pytest.approx(5.0, abs=1e-9)


def test_mixture_lab_pigmented_formula():
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.anticorrosion_coating))
    lab = mixture_lab(form)
    assert lab is not None, "Expected Lab value for pigmented primer"
    L, _, _ = lab
    assert 80.0 < L <= 100.0, f"Bright white primer L* should be high, got {L}"


def test_mixture_lab_pigment_free_is_none():
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.degreaser))
    assert mixture_lab(form) is None


def test_color_metrics_pigmented_formula_has_both_metrics():
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.anticorrosion_coating))
    metrics = color_metrics(form)
    assert "delta_e_00" in metrics and "delta_e_ab" in metrics and "delta_e" in metrics
    assert "delta_e_method" in metrics
    assert "L" in metrics and "a" in metrics and "b" in metrics
    assert "lab_L" in metrics and "lab_a" in metrics and "lab_b" in metrics
    assert metrics["delta_e_ab"] >= 0.0
    assert metrics["delta_e"] >= 0.0  # 兼容键恒为 float
    if metrics["delta_e_00"] is None:
        assert metrics["delta_e_method"] == "cie76"
        assert metrics["delta_e"] == metrics["delta_e_ab"]
    else:
        assert metrics["delta_e_method"] == "cie2000"
        assert metrics["delta_e"] == metrics["delta_e_00"]


def test_color_metrics_empty_for_pigment_free():
    form = knowledge.baseline_formulation(Requirement(domain=ProductDomain.degreaser))
    assert color_metrics(form) == {}


def test_colour_available_is_bool():
    assert isinstance(_colour_available(), bool)


# ── the optional colour-science dependency ──────────────────────────────────

def test_colour_availability_is_probed_once_and_logs_nothing(caplog, monkeypatch):
    """A default install has no colour-science. ``predict()`` reaches this on every candidate of an optimisation
    run, and the probe used to import-and-fail (logging a WARNING "optional feature check: No module named
    'colour'") each time — one log line per prediction."""
    import logging

    import app.services.colorimetry as colorimetry

    probes: list[str] = []
    monkeypatch.setattr(colorimetry, "optional_import", lambda name: probes.append(name) or False)
    colorimetry._colour_available.cache_clear()
    try:
        with caplog.at_level(logging.DEBUG):
            for _ in range(50):
                colorimetry.delta_e_2000((50.0, 0.0, 0.0), (60.0, 5.0, 5.0))
        assert probes == ["colour"], "probed more than once"
        assert not [r for r in caplog.records if "optional feature check" in r.getMessage()]
    finally:
        colorimetry._colour_available.cache_clear()


def test_without_colour_science_delta_e_2000_is_none_not_silent_cie76(monkeypatch):
    """2-21: 无 colour-science 时 delta_e_2000 返回 None，绝不静默 fallback 到 CIE76."""
    import app.services.colorimetry as colorimetry

    monkeypatch.setattr(colorimetry, "_colour_available", lambda: False)
    assert colorimetry.delta_e_2000((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485)) is None
    # CIE76 仍始终可用，且该点对的欧氏距离为 4.0。
    assert colorimetry.delta_e_ab((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485)) == pytest.approx(4.0, abs=0.01)
    with pytest.raises(ValueError):
        colorimetry.delta_e((50.0, 0.0, 0.0), (60.0, 5.0, 5.0), method="cie2000")


def test_ciede2000_matches_the_published_test_pair():
    """The branch that uses colour-science is otherwise never run (CI installs no ``color`` extra).
    Sharma, Wu & Dalal (2005), pair 1: ΔE00 = 2.0425."""
    pytest.importorskip("colour")
    import app.services.colorimetry as colorimetry

    colorimetry._colour_available.cache_clear()
    assert colorimetry._colour_available()
    assert delta_e_2000((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485)) == pytest.approx(2.0425, abs=1e-3)
