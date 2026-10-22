"""补齐 2-20: 流变拟合（LM + 三标准模型）与 API；2-21 色度/酸稳定性 API。"""
from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from app.services.analysis.least_squares import levenberg_marquardt, r_squared
from app.services.rheology import (
    FitError,
    FlowPoint,
    fit_cross,
    fit_flow_curve,
    fit_herschel_bulkley,
    fit_power_law,
    hysteresis_area,
    shear_thinning_assessment,
    thixotropy_index,
)


def test_lm_recovers_quadratic():
    import numpy as np

    x = np.linspace(0, 5, 30)
    y = 2.0 + 3.0 * x + 0.5 * x ** 2
    p, sse = levenberg_marquardt(lambda p, x: p[0] + p[1] * x + p[2] * x ** 2,
                                  x, y, np.array([0.0, 0.0, 0.0]))
    assert p == pytest.approx([2.0, 3.0, 0.5], abs=1e-6)
    assert sse < 1e-9


def test_power_law_recovers_synthetic_params():
    pts = [FlowPoint(shear_rate=g, viscosity=2.0 * g ** (0.6 - 1)) for g in [1, 2, 5, 10, 20, 50, 100]]
    r = fit_power_law(pts)
    assert r["params"]["K"] == pytest.approx(2.0, rel=1e-3)
    assert r["params"]["n"] == pytest.approx(0.6, rel=1e-3)
    assert r["r2"] == pytest.approx(1.0, abs=1e-6)


def test_herschel_bulkley_recovers_yield_stress():
    pts = [FlowPoint(shear_rate=g, shear_stress=5.0 + 1.5 * g ** 0.7) for g in [1, 2, 5, 10, 20, 50, 100]]
    r = fit_herschel_bulkley(pts)
    assert r["params"]["tau0"] == pytest.approx(5.0, rel=1e-2)
    assert r["params"]["K"] == pytest.approx(1.5, rel=1e-2)
    assert r["params"]["n"] == pytest.approx(0.7, rel=1e-2)


def test_cross_recovers_synthetic_params():
    pts = [FlowPoint(shear_rate=g, viscosity=0.1 + (10 - 0.1) / (1 + (0.5 * g) ** 0.8))
           for g in [0.1, 0.5, 1, 2, 5, 10, 20, 50, 100]]
    r = fit_cross(pts)
    assert r["params"]["eta0"] == pytest.approx(10.0, rel=1e-2)
    assert r["params"]["eta_inf"] == pytest.approx(0.1, rel=0.15)
    assert r["params"]["lambda"] == pytest.approx(0.5, rel=1e-2)
    assert r["params"]["m"] == pytest.approx(0.8, rel=1e-2)


def test_auto_selects_best_model():
    # HB data: Herschel–Bulkley must win over Power-law (which cannot fit a yield stress).
    pts = [FlowPoint(shear_rate=g, shear_stress=5.0 + 1.5 * g ** 0.7) for g in [1, 2, 5, 10, 20, 50, 100]]
    r = fit_flow_curve(pts, "auto")
    assert r["model"] == "herschel_bulkley"
    assert r["comparison"]["herschel_bulkley"]["r2"] >= r["comparison"]["power_law"]["r2"]


def test_flow_fit_rejects_too_few_points():
    with pytest.raises(FitError):
        fit_power_law([FlowPoint(shear_rate=1.0, viscosity=1.0)])


def test_thixotropy_and_hysteresis():
    assert thixotropy_index(5.0, 1.0)["thixotropy_index"] == pytest.approx(5.0)
    up = [FlowPoint(shear_rate=g, shear_stress=2.0 * g ** 0.8) for g in [1, 5, 10]]
    down = [FlowPoint(shear_rate=g, shear_stress=1.5 * g ** 0.8) for g in [1, 5, 10]]
    assert hysteresis_area(up, down)["hysteresis_area_pa_per_s"] > 0


def test_shear_thinning_assessment():
    assert shear_thinning_assessment(0.6)["assessment"] == "shear_thinning"
    assert shear_thinning_assessment(1.0)["assessment"] == "newtonian"
    assert shear_thinning_assessment(1.3)["assessment"] == "shear_thickening"


def test_rheology_fit_api():
    from app.main import app

    client = TestClient(app)
    pts = [{"shear_rate": g, "shear_stress": 5.0 + 1.5 * g ** 0.7} for g in [1, 2, 5, 10, 20, 50, 100]]
    r = client.post("/api/cockpit/rheology/fit", json={"points": pts, "model": "auto"})
    assert r.status_code == 200
    assert r.json()["model"] == "herschel_bulkley"
    assert r.json()["shear_thinning"]["assessment"] == "shear_thinning"
    r = client.post("/api/cockpit/rheology/fit", json={"points": pts[:2], "model": "auto"})
    assert r.status_code == 422


def test_acid_trend_api():
    from app.main import app

    client = TestClient(app)
    # Zinc dust + acid → H₂ evolution rule fires at every pH (composition rule,
    # pH-independent); a plain resin-only formulation stays pass.
    zinc = {"name": "含锌粉", "ingredients": [{"name": "Zinc dust", "weight_pct": 10.0, "role": "pigment"}]}
    plain = {"name": "空白", "ingredients": [{"name": "Deionized water", "weight_pct": 100.0, "role": "solvent"}]}
    r = client.post("/api/cockpit/acid-stability/trend",
                    json={"formulations": [zinc, plain], "ph_min": 2.0, "ph_max": 3.0, "ph_step": 1.0})
    assert r.status_code == 200
    series = {s["name"]: s["points"] for s in r.json()["series"]}
    assert all(p["status"] in ("warn", "infeasible") for p in series["含锌粉"])
    assert all(p["status"] == "pass" for p in series["空白"])


def test_color_api_and_materials():
    from app.main import app

    client = TestClient(app)
    r = client.get("/api/cockpit/color/materials")
    assert r.status_code == 200
    assert r.json()["count"] > 0
    assert all("lab" in m for m in r.json()["materials"])
    # Titanium dioxide white vs carbon black: L* far apart, ΔE*ab large.
    r = client.post("/api/cockpit/color", json={"formulations": [
        {"name": "钛白", "ingredients": [{"name": "Titanium dioxide", "weight_pct": 20.0, "role": "pigment"}]},
        {"name": "炭黑", "ingredients": [{"name": "Carbon black", "weight_pct": 5.0, "role": "pigment"}]},
    ]})
    assert r.status_code == 200
    forms = {f["name"]: f for f in r.json()["formulations"]}
    assert forms["钛白"]["lab_L"] > forms["炭黑"]["lab_L"]
    assert "delta_e_ab" in forms["钛白"] and "delta_e_00" in forms["钛白"]
    assert forms["钛白"]["delta_e_method"] in ("cie2000", "cie76")


def test_color_api_pigment_free_returns_null_fields_not_crash():
    # P1-15: 无色素配方（含参考色时）也须 200 且字段显式为 null，不 500。
    from app.main import app

    client = TestClient(app)
    r = client.post("/api/cockpit/color", json={"formulations": [
        {"name": "无色素", "ingredients": [{"name": "自由输入名", "weight_pct": 100.0, "role": "solvent"}]},
    ], "reference_lab": [90.0, 0.0, 0.0]})
    assert r.status_code == 200
    f = r.json()["formulations"][0]
    assert f["L"] is None and f["a"] is None and f["b"] is None
    assert f["delta_e_ab"] is None and f["delta_e_method"] is None
    assert f["delta_e_00_vs_reference"] is None and f["delta_e_ab_vs_reference"] is None
