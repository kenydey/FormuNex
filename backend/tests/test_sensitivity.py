"""补齐 2-19: 灵敏度分析（代理模型局部梯度 + 一阶 Sobol）+ API。"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.services.analysis.sensitivity import SensitivityError, analyze


def _linear_data():
    # y = 3·x1 + 0.1·x2 — factor 1 dominates; quadratic surrogate fits exactly.
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 10, size=(40, 2))
    y = 3.0 * X[:, 0] + 0.1 * X[:, 1]
    return X, y


def test_sobol_recovers_dominant_factor():
    X, y = _linear_data()
    out = analyze(X, y, ["x1", "x2"])
    assert out["surrogate"]["r2"] == pytest.approx(1.0, abs=1e-6)
    assert out["surrogate"]["reliable"] is True
    sobol = {s["factor"]: s["index"] for s in out["sobol_first_order"]}
    assert sobol["x1"] > 0.99
    assert sobol["x2"] < 0.01


def test_local_gradient_matches_analytic():
    X, y = _linear_data()
    out = analyze(X, y, ["x1", "x2"], x0=np.array([5.0, 5.0]))
    assert out["local"]["gradient"] == pytest.approx([3.0, 0.1], abs=1e-6)
    # Per-range: gradient × OBSERVED range (not the nominal [0,10] sampler range).
    ranges = X.max(axis=0) - X.min(axis=0)
    assert out["local"]["gradient_per_range"] == pytest.approx([3.0 * ranges[0], 0.1 * ranges[1]], abs=1e-4)


def test_quadratic_term_detected():
    rng = np.random.default_rng(1)
    X = rng.uniform(0, 5, size=(60, 2))
    y = X[:, 0] ** 2 + 0.5 * X[:, 1]
    out = analyze(X, y, ["x1", "x2"], x0=np.array([2.0, 2.0]))
    assert out["surrogate"]["r2"] == pytest.approx(1.0, abs=1e-6)
    assert out["local"]["gradient"][0] == pytest.approx(4.0, abs=1e-4)  # d/dx1 (x1²) = 2·x1


def test_too_few_points_rejected():
    with pytest.raises(SensitivityError):
        analyze(np.ones((5, 2)), np.ones(5), ["a", "b"])


def test_constant_factor_rejected():
    X = np.column_stack([np.linspace(0, 10, 20), np.ones(20)])
    with pytest.raises(SensitivityError):
        analyze(X, np.linspace(0, 1, 20), ["a", "b"])


def _api_points():
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 10, size=(40, 2))
    y = 3.0 * X[:, 0] + 0.1 * X[:, 1]
    return [{"id": f"p{i}", "factors": {"x1": float(a), "x2": float(b)}, "objective": float(v)}
            for i, ((a, b), v) in enumerate(zip(X, y))]


def test_sensitivity_api():
    from app.main import app

    client = TestClient(app)
    r = client.post("/api/cockpit/sensitivity", json={"points": _api_points()})
    assert r.status_code == 200
    body = r.json()
    assert body["surrogate"]["r2"] == pytest.approx(1.0, abs=1e-6)
    assert body["sobol_first_order"][0]["factor"] == "x1"
    assert body["sobol_first_order"][0]["index"] > 0.99


def test_sensitivity_api_rejects_too_few():
    from app.main import app

    client = TestClient(app)
    pts = _api_points()[:5]
    r = client.post("/api/cockpit/sensitivity", json={"points": pts})
    assert r.status_code == 422


def test_sensitivity_api_selected_point():
    from app.main import app

    client = TestClient(app)
    pts = _api_points()
    r = client.post("/api/cockpit/sensitivity", json={"points": pts, "selected_id": pts[3]["id"]})
    assert r.status_code == 200
    # API rounds `at` to 4 decimals; raw floats need an absolute tolerance.
    assert r.json()["local"]["at"] == pytest.approx([pts[3]["factors"]["x1"], pts[3]["factors"]["x2"]], abs=1e-4)
