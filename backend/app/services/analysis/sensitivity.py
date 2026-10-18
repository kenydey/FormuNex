"""Sensitivity analysis for optimization points (补齐 2-19).

Two complementary, standard views over the *observed* points (factor values →
one objective):

1. Local sensitivity: the gradient of a fitted quadratic surrogate at a
   chosen point (finite, analytic for the quadratic form). Answers "if I
   nudge this factor a little here, which way does the objective move?"
2. First-order Sobol indices (Saltelli 2002): the share of the surrogate's
   output variance explained by each factor alone, estimated by Monte Carlo
   sampling of the surrogate over the factor box. Answers "which factor
   drives the objective overall?"

Honesty: both are *surrogate-based*. The quadratic surrogate is fitted by
least squares on the observed points, and its R² is always reported —
when R² is low the indices describe the surrogate, not the real process,
and the response says so. No claim is made about the true process without
enough data (fewer than 8 points → 422).
"""
from __future__ import annotations

import numpy as np

MIN_POINTS = 8


class SensitivityError(ValueError):
    pass


def _features(X: np.ndarray) -> np.ndarray:
    """[1, x_i, x_i², x_i·x_j (i<j)] design matrix for a full quadratic."""
    n, d = X.shape
    cols = [np.ones(n)]
    for i in range(d):
        cols.append(X[:, i])
    for i in range(d):
        cols.append(X[:, i] ** 2)
    for i in range(d):
        for j in range(i + 1, d):
            cols.append(X[:, i] * X[:, j])
    return np.column_stack(cols)


def fit_quadratic(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float]:
    """Least-squares quadratic surrogate. Returns (coefficients, R²)."""
    A = _features(X)
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ coef
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return coef, r2


def _gradient(coef: np.ndarray, x: np.ndarray, d: int) -> np.ndarray:
    """Analytic gradient of the quadratic surrogate at x (in original units)."""
    # coef layout: [const, linear(d), quad(d), cross(d*(d-1)/2)]
    grad = coef[1:1 + d].copy()
    grad += 2.0 * coef[1 + d:1 + 2 * d] * x
    k = 1 + 2 * d
    for i in range(d):
        for j in range(i + 1, d):
            c = coef[k]
            k += 1
            grad[i] += c * x[j]
            grad[j] += c * x[i]
    return grad


def _predict(coef: np.ndarray, X: np.ndarray) -> np.ndarray:
    return _features(X) @ coef


def analyze(
    X: np.ndarray,
    y: np.ndarray,
    factor_names: list[str],
    x0: np.ndarray | None = None,
    n_samples: int = 4096,
    seed: int = 0,
) -> dict:
    """Full sensitivity report for one objective over the observed points.

    X: (n, d) factor values, y: (n,) objective values. Factor ranges are
    taken from the observed min/max per factor. Returns local gradients at
    x0 (default: the observed best point), first-order Sobol indices, the
    surrogate R² and a reliability note.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if X.ndim != 2 or len(y) != len(X):
        raise SensitivityError("X 必须是 (n, d)，y 长度须与 X 行数一致")
    if len(y) < MIN_POINTS:
        raise SensitivityError(f"灵敏度分析至少需要 {MIN_POINTS} 个点，当前 {len(y)} 个")
    if len(factor_names) != X.shape[1]:
        raise SensitivityError("因子名数量须与 X 列数一致")
    d = X.shape[1]
    if d < 1:
        raise SensitivityError("至少需要 1 个因子")
    lo = X.min(axis=0)
    hi = X.max(axis=0)
    if np.any(hi <= lo):
        raise SensitivityError("存在取值恒定的因子，无法分析灵敏度")

    coef, r2 = fit_quadratic(X, y)
    if x0 is None:
        x0 = X[int(np.argmax(y))]
    x0 = np.asarray(x0, dtype=float)
    grad = _gradient(coef, x0, d)
    # Scale gradients by the factor range so they are comparable ("per full-range move").
    scaled = grad * (hi - lo)

    # First-order Sobol (Saltelli 2002): S_i = mean(f(B)·(f(A_Bi) − f(A))) / Var.
    rng = np.random.default_rng(seed)
    A = rng.uniform(lo, hi, size=(n_samples, d))
    B = rng.uniform(lo, hi, size=(n_samples, d))
    fA = _predict(coef, A)
    fB = _predict(coef, B)
    var = float(np.var(np.concatenate([fA, fB]), ddof=1))
    sobol = []
    for i in range(d):
        ABi = A.copy()
        ABi[:, i] = B[:, i]
        fABi = _predict(coef, ABi)
        s = float(np.mean(fB * (fABi - fA)) / var) if var > 0 else 0.0
        sobol.append(max(0.0, min(1.0, s)))  # Monte Carlo noise can push slightly out of [0,1]
    # Monte Carlo noise can push slightly out of [0,1]; clamp. Shares are kept
    # raw (their sum is the explained-variance share, < 1 when interactions matter).

    return {
        "factors": factor_names,
        "n_points": len(y),
        "surrogate": {"model": "quadratic", "r2": round(float(r2), 4),
                      "reliable": bool(r2 >= 0.7),
                      "note": ("代理模型 R² ≥ 0.7，结果可参考" if r2 >= 0.7
                               else "代理模型 R² < 0.7：观测点不足或关系非二次，灵敏度仅供参考，不代表真实过程")},
        "local": {"at": [round(float(v), 4) for v in x0],
                  "gradient": [round(float(v), 4) for v in grad],
                  "gradient_per_range": [round(float(v), 4) for v in scaled],
                  "note": "局部梯度 = 代理模型在该点的偏导；gradient_per_range 为因子走满全距时的目标变化量"},
        "sobol_first_order": [
            {"factor": name, "index": round(float(s), 4)} for name, s in zip(factor_names, sobol)
        ],
        "sobol_note": "一阶 Sobol 指数 = 该因子单独解释的代理模型输出方差占比；交互效应包含在二次交叉项中，未单独拆出",
    }
