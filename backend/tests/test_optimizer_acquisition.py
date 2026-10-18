"""补齐 2-16: 采集函数公式 + 逐迭代历史 + 优化器接线。"""
from __future__ import annotations

import math

import pytest

from app.services.optimizer_acquisition import (
    AcquisitionHistory,
    AcquisitionPoint,
    expected_improvement,
    log_expected_improvement,
    ucb,
)


def test_ucb_formula():
    assert ucb(1.0, 0.5, kappa=1.5) == pytest.approx(1.75)


def test_ei_at_mean_equals_best():
    # EI(μ=f*, σ=1, ξ=0) = σ·φ(0) = 1/√(2π).
    assert expected_improvement(0.0, 1.0, 0.0, xi=0.0) == pytest.approx(1.0 / math.sqrt(2 * math.pi), rel=1e-9)


def test_ei_zero_sigma_is_zero():
    assert expected_improvement(5.0, 0.0, 1.0) == 0.0


def test_ei_increases_with_uncertainty():
    assert expected_improvement(0.0, 2.0, 0.0, xi=0.0) > expected_improvement(0.0, 1.0, 0.0, xi=0.0)


def test_log_ei_consistent_with_ei():
    ei = expected_improvement(0.5, 1.0, 0.0, xi=0.01)
    assert math.exp(log_expected_improvement(0.5, 1.0, 0.0, xi=0.01)) == pytest.approx(ei, rel=1e-6)


def test_log_ei_very_negative_z_stays_finite():
    # Far below best: EI underflows to 0 in double, log EI must stay finite.
    v = log_expected_improvement(-50.0, 1.0, 0.0, xi=0.0)
    assert math.isfinite(v) and v < -100


def test_history_summary_declining():
    h = AcquisitionHistory()
    for i, a in enumerate([10.0, 9.0, 8.0, 2.0, 1.0, 0.5]):
        h.append(AcquisitionPoint(iteration=i + 1, acquisition=a, acquisition_name="UCB",
                                  best_score=0.5, engine="numpy-ucb"))
    s = h.summary()
    assert s["trend"] == "declining"
    assert s["max_acquisition"] == pytest.approx(10.0)


def test_history_summary_insufficient_when_all_none():
    h = AcquisitionHistory()
    h.append(AcquisitionPoint(iteration=1, acquisition=None, acquisition_name="TPE",
                              best_score=0.5, engine="optuna-tpe"))
    assert h.summary()["trend"] == "insufficient_data"


def test_numpy_optimizer_records_last_acquisition():
    from app.services.optimizer import BayesianOptimizer, Factor

    opt = BayesianOptimizer(factors=[Factor("a", 0.0, 10.0), Factor("b", 0.0, 10.0)], seed=0)
    for i in range(6):
        opt.observe([float(i), float(i)], float(i))
    x, acq = opt.suggest_with_acquisition()
    assert len(x) == 2
    assert acq is not None and math.isfinite(acq)
    assert opt.acquisition_name == "UCB"


def test_workflow_run_records_acquisition_history():
    from app.domain.schemas import ProductDomain, Requirement
    from app.pipeline import workflow

    req = Requirement(domain=ProductDomain.anticorrosion_coating)
    # 8 iterations: the first few are warm-up random draws (acquisition None)
    # until enough observations exist for the UCB surrogate; later ones report
    # a real UCB value.
    res = workflow.run_optimization(req, iterations=8, engine="numpy")
    assert len(res.acquisition_history) == 8
    assert res.acquisition_history[0]["iteration"] == 1
    # Best score is non-decreasing across iterations.
    scores = [p["best_score"] for p in res.acquisition_history]
    assert scores == sorted(scores)
    # The numpy engine reports a real UCB value; other engines report None
    # with their name (no fabricated proxy) — both are honest, assert the shape.
    if res.engine == "numpy-ucb":
        assert all(p["acquisition_name"] == "UCB" for p in res.acquisition_history)
        assert res.acquisition_history[-1]["acquisition"] is not None
    else:
        assert all(p["acquisition"] is None for p in res.acquisition_history)
