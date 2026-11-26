"""Regression tests for review 2026-11-26, optimization P1-1.

The numpy-UCB surrogate used to add the *global* y-spread to the uncertainty
(plus a distance term capped at 1.0), so the exploration bonus barely varied
with distance to the observations and the optimizer ended up *worse* than pure
random search on a smooth 2D quadratic (24 rounds, 5 seeds: UCB mean -2.95 vs
random -1.04). The fix makes the uncertainty distance-dominated
(``sigma_y * (1 - w_max)``) with an adaptive kernel bandwidth, and makes
``suggest()`` refuse near-duplicates of observed points. These tests pin that
behaviour: if the uncertainty regresses to a global-spread term, the quadratic
probe fails again.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.services.optimizer import BayesianOptimizer, Factor


def _quadratic_2d(x: list[float]) -> float:
    # Smooth bowl, optimum 0 at (3, 7).
    return -((x[0] - 3.0) ** 2 + (x[1] - 7.0) ** 2)


def _run_ucb(seed: int, rounds: int = 24) -> float:
    opt = BayesianOptimizer(
        factors=[Factor("x", 0.0, 10.0), Factor("y", 0.0, 10.0)], seed=seed
    )
    for _ in range(rounds):
        x = opt.suggest(kappa=1.5)
        opt.observe(x, _quadratic_2d(x))
    assert opt.best is not None
    return opt.best[1]


def _run_random(seed: int, rounds: int = 24) -> float:
    rng = np.random.default_rng(seed)
    best = -np.inf
    for _ in range(rounds):
        x = rng.uniform(0.0, 10.0, size=2)
        best = max(best, _quadratic_2d([float(x[0]), float(x[1])]))
    return float(best)


def test_ucb_beats_random_on_2d_quadratic():
    # Review probe, fixed seeds: 24 rounds on f=-((x-3)^2+(y-7)^2).
    # Before the fix the UCB mean over seeds 0..4 was -2.95 (random -1.04);
    # after the fix it is -0.36 (random -0.95 with the same paired protocol).
    seeds = range(5)
    ucb = [_run_ucb(s) for s in seeds]
    rnd = [_run_random(s) for s in seeds]
    mean_ucb = float(np.mean(ucb))
    mean_rnd = float(np.mean(rnd))
    assert mean_ucb > -1.0, f"UCB mean {mean_ucb:.2f} over {ucb} is not close to the optimum"
    assert mean_ucb > mean_rnd + 0.3, (
        f"UCB mean {mean_ucb:.2f} must beat paired random mean {mean_rnd:.2f} "
        f"by a clear margin (UCB {ucb}, random {rnd})"
    )


def test_uncertainty_is_zero_at_observed_point_and_large_far_away():
    opt = BayesianOptimizer(
        factors=[Factor("x", 0.0, 10.0), Factor("y", 0.0, 10.0)], seed=0
    )
    # Five observations clustered at (1, 1) with spread-out y values.
    for y in (-10.0, -20.0, -30.0, -40.0, -50.0):
        opt.observe([1.0, 1.0], y)
    _, unc_at = opt._surrogate(np.array([1.0, 1.0]))
    _, unc_far = opt._surrogate(np.array([9.0, 9.0]))
    assert unc_at == pytest.approx(0.0)
    # Far from every observation the uncertainty must approach the y scale,
    # not sit at the same level as next to the data.
    assert unc_far > 10.0
    assert unc_far > 100 * max(unc_at, 1e-12)


def test_uncertainty_grows_with_distance_from_observations():
    opt = BayesianOptimizer(
        factors=[Factor("x", 0.0, 10.0), Factor("y", 0.0, 10.0)], seed=0
    )
    # Observations clustered around (3, 7) with spread-out y values.
    for pt, y in [
        ([2.5, 6.5], -10.0),
        ([3.0, 7.0], -20.0),
        ([3.5, 7.5], -30.0),
        ([2.8, 7.2], -40.0),
        ([3.2, 6.8], -50.0),
        ([3.0, 6.5], -60.0),
    ]:
        opt.observe(pt, y)
    _, unc_near = opt._surrogate(np.array([3.0, 7.0]))
    _, unc_far = opt._surrogate(np.array([0.0, 0.0]))
    # The (3, 7) region is surrounded by observations; the corner is not.
    # With the old global-spread uncertainty these were the same order of
    # magnitude (11.27 vs 16.75 in the review probe); now they must differ
    # clearly.
    assert unc_near < 0.1 * unc_far
    assert unc_far > 5.0


def test_suggest_never_returns_near_duplicate_of_observed_point():
    opt = BayesianOptimizer(
        factors=[Factor("x", 0.0, 10.0), Factor("y", 0.0, 10.0)], seed=7
    )
    for _ in range(24):
        x = opt.suggest(kappa=1.5)
        if opt._X:
            scales = np.array([10.0, 10.0])
            d = np.linalg.norm((np.array(opt._X) - np.array(x)) / scales, axis=1)
            assert d.min() >= 0.01, f"suggested {x} within {d.min():.4f} of an observed point"
        opt.observe(x, _quadratic_2d(x))


def test_identical_observations_still_explore_by_distance():
    # All y identical: the old code returned std(y) + 1.0 everywhere, the new
    # one must keep a pure distance bonus (sigma floored to 1.0) instead of
    # collapsing the acquisition to a constant.
    opt = BayesianOptimizer(
        factors=[Factor("x", 0.0, 10.0), Factor("y", 0.0, 10.0)], seed=0
    )
    for i in range(5):
        opt.observe([float(i), float(i)], 42.0)
    _, unc_at = opt._surrogate(np.array([0.0, 0.0]))
    _, unc_far = opt._surrogate(np.array([9.0, 9.0]))
    assert unc_at == pytest.approx(0.0)
    # Distance bonus is alive and bounded by the floored sigma of 1.0.
    assert 0.5 < unc_far <= 1.0
