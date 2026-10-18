"""Levenberg–Marquardt nonlinear least squares (pure numpy, no scipy).

Standard LM (Marquardt 1963): solve (JᵀJ + λ·diag(JᵀJ))·δ = Jᵀr each step,
accept the step when the cost decreases, adapt λ. Used by the rheology
flow-curve fits; kept generic so other small fits can reuse it.
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np


def levenberg_marquardt(
    model: Callable[[np.ndarray, np.ndarray], np.ndarray],
    x: np.ndarray,
    y: np.ndarray,
    p0: np.ndarray,
    bounds: tuple[np.ndarray, np.ndarray] | None = None,
    max_iter: int = 200,
    tol: float = 1e-10,
) -> tuple[np.ndarray, float]:
    """Fit ``model(p, x) ≈ y``. Returns (best params, final SSE).

    Numerical Jacobian (central differences). Parameters are clipped to
    ``bounds`` after each accepted step. Raises ValueError on bad input;
    returns the best-so-far on non-convergence instead of failing.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    p = np.asarray(p0, dtype=float).copy()
    if len(x) != len(y) or len(p) == 0:
        raise ValueError("x/y 长度须一致，p0 非空")
    if np.any(~np.isfinite(x)) or np.any(~np.isfinite(y)) or np.any(~np.isfinite(p)):
        raise ValueError("输入含非有限值")

    def sse(params: np.ndarray) -> float:
        r = model(params, x) - y
        return float(np.sum(r * r))

    lam = 1e-3
    cost = sse(p)
    for _ in range(max_iter):
        # Central-difference Jacobian.
        J = np.empty((len(x), len(p)))
        for j in range(len(p)):
            h = 1e-6 * max(1.0, abs(p[j]))
            pp = p.copy(); pp[j] += h
            pm = p.copy(); pm[j] -= h
            J[:, j] = (model(pp, x) - model(pm, x)) / (2.0 * h)
        r = model(p, x) - y
        JTJ = J.T @ J
        g = J.T @ r
        improved = False
        for _ in range(50):  # inner λ adaptation
            A = JTJ + lam * np.diag(np.maximum(np.diag(JTJ), 1e-12))
            try:
                step = np.linalg.solve(A, -g)
            except np.linalg.LinAlgError:
                lam *= 10.0
                continue
            trial = p + step
            if bounds is not None:
                trial = np.clip(trial, bounds[0], bounds[1])
            trial_cost = sse(trial)
            if trial_cost < cost:
                rel = (cost - trial_cost) / (cost + 1e-300)
                p, cost = trial, trial_cost
                lam = max(lam * 0.3, 1e-12)
                improved = True
                if rel < tol:
                    return p, cost
                break
            lam *= 10.0
            if lam > 1e12:
                break
        if not improved:
            break
    return p, cost


def r_squared(y: np.ndarray, y_pred: np.ndarray) -> float:
    """Coefficient of determination; 0 when y is constant."""
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    if ss_tot <= 0:
        return 0.0
    ss_res = float(np.sum((y - y_pred) ** 2))
    return max(0.0, 1.0 - ss_res / ss_tot)


def rmse(y: np.ndarray, y_pred: np.ndarray) -> float:
    return float(math.sqrt(np.mean((np.asarray(y) - np.asarray(y_pred)) ** 2)))
