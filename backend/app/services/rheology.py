"""Rheological and glass-transition property estimation (v0.5).

Provides CPU-only, zero-dependency empirical formulas:
  - Fox equation for multi-component polymer Tg
  - Mooney-Rivlin-style relative viscosity from pigment volume loading
  - Viscoelastic index (dimensionless composite quality indicator)

补齐 2-20: flow-curve fitting — Power-law, Herschel–Bulkley and Cross models
(all textbook standard models; see module functions for references). Pure
numpy Levenberg–Marquardt (services/analysis/least_squares.py): the backend
venv has no scipy.

All functions degrade gracefully: if tg_k data is absent for any component
the function returns None rather than fabricating a value.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from .errors import degrade_return
from ..domain.schemas import Formulation

logger = logging.getLogger(__name__)

_POLYMER_ROLES = {"resin", "hardener"}
_PIGMENT_ROLES = {"pigment", "filler", "inhibitor"}

# Random-close-packing upper limit for Mooney model
_PHI_MAX = 0.64

# Room temperature reference (K) for viscoelastic index
_T_AMBIENT_K = 298.15


def fox_tg(form: Formulation) -> float | None:
    """Fox equation for a multi-component polymer blend (K).

    1/Tg_mix = Σ(w_i / Tg_i)

    Only polymer-role components (resin, hardener) with known tg_k contribute.
    Returns None when any polymer component lacks tg_k data.
    """
    from ..domain.knowledge import RAW_MATERIALS

    inv_tg = 0.0
    total_w = 0.0
    for ing in form.ingredients:
        if ing.role not in _POLYMER_ROLES:
            continue
        if ing.weight_pct <= 0:
            continue
        tg = RAW_MATERIALS.get(ing.name, {}).get("tg_k")
        if tg is None:
            return None  # graceful degradation: missing data → skip prediction
        inv_tg += ing.weight_pct / tg
        total_w += ing.weight_pct

    if total_w <= 0 or inv_tg <= 0:
        return None
    tg_mix_k = total_w / inv_tg
    return round(tg_mix_k, 2)


def fox_tg_celsius(form: Formulation) -> float | None:
    """Fox equation result in °C (None when data is missing)."""
    tg_k = fox_tg(form)
    return round(tg_k - 273.15, 2) if tg_k is not None else None


def mooney_viscosity(form: Formulation) -> float | None:
    """Mooney relative viscosity from pigment volume fraction.

    η_r = exp(2.5·φ / (1 - φ/φ_max))

    φ is derived from the PVC descriptor. Returns None when PVC is zero or
    when φ > 0.5 (outside the model's reliable range).
    """
    try:
        from ..domain.chemistry import pvc

        pvc_val = pvc(form)
    except Exception as exc:
        return degrade_return(logger, exc, "operation failed", None)

    if pvc_val <= 0:
        return None
    phi = pvc_val / 100.0  # convert % to fraction
    if phi >= _PHI_MAX:
        return None  # outside Mooney model range
    if phi > 0.5:
        return None  # model unreliable above 50 vol%
    import math

    eta_r = math.exp(2.5 * phi / (1.0 - phi / _PHI_MAX))
    return round(eta_r, 3)


def viscoelastic_index(form: Formulation) -> float:
    """Dimensionless viscoelastic quality index [0–1].

    Combines two signals:
      - How far the film Tg sits above ambient (higher = better barrier)
      - PVC/CPVC ratio (below 1 = good barrier film)

    When Tg data is unavailable the index is estimated from PVC alone.
    """
    from ..domain.chemistry import cpvc, pvc

    score = 0.5  # neutral default

    tg_k = fox_tg(form)
    if tg_k is not None:
        # Tg above ambient → better thermoset cure quality (capped at 80 K margin)
        delta_t = max(0.0, min(80.0, tg_k - _T_AMBIENT_K))
        score = delta_t / 80.0

    pvc_val = pvc(form)
    if pvc_val > 0:
        cpvc_val = cpvc(form)
        if cpvc_val:
            ratio = pvc_val / cpvc_val
            # ratio < 1 is good (dense film); contribution mapped [0, 1]
            pvc_contribution = max(0.0, 1.0 - ratio)
            score = (score + pvc_contribution) / 2.0

    return round(max(0.0, min(1.0, score)), 3)



# ---------------------------------------------------------------------------
# 补齐 2-20: 流变曲线拟合（标准模型，非经验公式）
# ---------------------------------------------------------------------------

class FitError(ValueError):
    """流变拟合输入/求解失败。"""


@dataclass
class FlowPoint:
    """一个流变测量点：剪切速率 + 黏度或剪切应力（二选一，都有时以应力为准）。"""
    shear_rate: float
    viscosity: float | None = None
    shear_stress: float | None = None

    def stress(self) -> float:
        if self.shear_stress is not None:
            return float(self.shear_stress)
        if self.viscosity is not None:
            return float(self.shear_rate) * float(self.viscosity)
        raise FitError("FlowPoint 须给 viscosity 或 shear_stress 之一")

    def eta(self) -> float:
        if self.viscosity is not None:
            return float(self.viscosity)
        if self.shear_stress is not None:
            return float(self.shear_stress) / float(self.shear_rate)
        raise FitError("FlowPoint 须给 viscosity 或 shear_stress 之一")


_MIN_FIT_POINTS = 4


def _as_arrays(points: list[FlowPoint]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回 (shear_rate, shear_stress, viscosity)，按剪切速率升序。"""
    if len(points) < _MIN_FIT_POINTS:
        raise FitError(f"拟合至少需要 {_MIN_FIT_POINTS} 个点，当前 {len(points)}")
    sr = np.array([float(p.shear_rate) for p in points], dtype=float)
    ss = np.array([p.stress() for p in points], dtype=float)
    eta = np.array([p.eta() for p in points], dtype=float)
    if np.any(~np.isfinite(sr)) or np.any(~np.isfinite(ss)) or np.any(~np.isfinite(eta)):
        raise FitError("剪切速率/应力/黏度含非有限值")
    if np.any(sr <= 0):
        raise FitError("剪切速率须 > 0")
    if np.any(ss < 0) or np.any(eta <= 0):
        raise FitError("剪切应力须 ≥ 0，黏度须 > 0")
    order = np.argsort(sr)
    return sr[order], ss[order], eta[order]


def _result(model: str, params: dict[str, float], y: np.ndarray, pred: np.ndarray) -> dict[str, object]:
    from .analysis.least_squares import r_squared, rmse

    return {"model": model, "params": params,
            "r2": float(r_squared(y, pred)), "rmse": float(rmse(y, pred))}


def fit_power_law(points: list[FlowPoint]) -> dict[str, object]:
    """Power-law（Ostwald–de Waele）: τ = K · γ̇ⁿ。

    K 一致性系数（Pa·sⁿ），n 流动指数（n<1 剪切变稀，n=1 牛顿，n>1 剪切增稠）。
    """
    from .analysis.least_squares import levenberg_marquardt

    sr, ss, _ = _as_arrays(points)

    def model(params: np.ndarray, x: np.ndarray) -> np.ndarray:
        K, n = params
        return K * np.power(np.maximum(x, 1e-12), n)

    log_x, log_y = np.log(sr), np.log(np.maximum(ss, 1e-12))
    A = np.vstack([np.ones_like(log_x), log_x]).T
    coef, *_ = np.linalg.lstsq(A, log_y, rcond=None)
    x0 = np.array([math.exp(coef[0]), max(0.1, min(2.0, coef[1]))])
    params, _ = levenberg_marquardt(model, sr, ss, x0,
                                    bounds=(np.array([1e-9, 0.05]), np.array([1e6, 3.0])))
    return _result("power_law", {"K": float(params[0]), "n": float(params[1])}, ss, model(params, sr))


def fit_herschel_bulkley(points: list[FlowPoint]) -> dict[str, object]:
    """Herschel–Bulkley: τ = τ₀ + K · γ̇ⁿ。τ₀ 屈服应力（Pa）；τ₀=0 时退化为 Power-law。"""
    from .analysis.least_squares import levenberg_marquardt

    sr, ss, _ = _as_arrays(points)

    def model(params: np.ndarray, x: np.ndarray) -> np.ndarray:
        tau0, K, n = params
        return tau0 + K * np.power(np.maximum(x, 1e-12), n)

    x0 = np.array([max(0.0, float(np.min(ss)) * 0.2), 1.0, 0.7])
    params, _ = levenberg_marquardt(model, sr, ss, x0,
                                    bounds=(np.array([0.0, 1e-9, 0.05]),
                                            np.array([float(np.max(ss)), 1e6, 3.0])))
    return _result("herschel_bulkley",
                   {"tau0": float(params[0]), "K": float(params[1]), "n": float(params[2])},
                   ss, model(params, sr))


def fit_cross(points: list[FlowPoint]) -> dict[str, object]:
    """Cross 模型: η = η∞ + (η₀ − η∞) / (1 + (λγ̇)ᵐ)。

    η₀ 零剪切黏度，η∞ 无限剪切黏度，λ 松弛时间（s），m 无量纲指数。
    拟合在黏度域进行（模型本身是黏度模型），r2 也按黏度域计算。
    """
    from .analysis.least_squares import levenberg_marquardt

    sr, _, eta = _as_arrays(points)

    def model(params: np.ndarray, x: np.ndarray) -> np.ndarray:
        eta0, etainf, lam, m = params
        return etainf + (eta0 - etainf) / (1.0 + np.power(lam * x, m))

    x0 = np.array([max(float(np.max(eta)), 1e-6), max(float(np.min(eta)) * 0.5, 1e-9), 1.0, 0.8])
    params, _ = levenberg_marquardt(model, sr, eta, x0,
                                    bounds=(np.array([1e-9, 0.0, 1e-6, 0.05]),
                                            np.array([1e9, 1e9, 1e4, 3.0])))
    return _result("cross",
                   {"eta0": float(params[0]), "eta_inf": float(params[1]),
                    "lambda": float(params[2]), "m": float(params[3])},
                   eta, model(params, sr))


def fit_flow_curve(points: list[FlowPoint], model: str = "auto") -> dict[str, object]:
    """拟合流变曲线。model="auto" 时三模型都拟合，返回 R² 最高者 + 对比表。"""
    if model == "power_law":
        out = fit_power_law(points)
        out["comparison"] = {"power_law": {"r2": out["r2"], "rmse": out["rmse"]}}
        return out
    if model == "herschel_bulkley":
        out = fit_herschel_bulkley(points)
        out["comparison"] = {"herschel_bulkley": {"r2": out["r2"], "rmse": out["rmse"]}}
        return out
    if model == "cross":
        out = fit_cross(points)
        out["comparison"] = {"cross": {"r2": out["r2"], "rmse": out["rmse"]}}
        return out
    if model != "auto":
        raise FitError(f"未知模型 {model!r}，可选 auto/power_law/herschel_bulkley/cross")
    fits = [fit_power_law(points), fit_herschel_bulkley(points), fit_cross(points)]
    comparison = {str(f["model"]): {"r2": f["r2"], "rmse": f["rmse"]} for f in fits}
    best = max(fits, key=lambda f: float(f["r2"]))
    best["comparison"] = comparison
    return best


def thixotropy_index(eta_low: float, eta_high: float) -> dict[str, float]:
    """触变指数（两点筛查定义）：η(低剪切) / η(高剪切)。

    仅为快速筛查指标：严格触变需循环剪切曲线的滞后环面积（见 hysteresis_area）。
    """
    if eta_low <= 0 or eta_high <= 0:
        raise FitError("黏度须 > 0")
    return {"thixotropy_index": float(eta_low / eta_high)}


def hysteresis_area(up: list[FlowPoint], down: list[FlowPoint]) -> dict[str, float]:
    """滞后环面积：上行/下行曲线在 τ–γ̇ 平面间的积分面积（Pa/s）。

    两条曲线在 γ̇ 并集网格上插值后取 |τ_up − τ_down| 梯形积分。
    """
    if len(up) < 2 or len(down) < 2:
        raise FitError("滞后环至少需要上行/下行各 2 个点")
    up_sr = np.array([float(p.shear_rate) for p in up])
    up_ss = np.array([p.stress() for p in up])
    dn_sr = np.array([float(p.shear_rate) for p in down])
    dn_ss = np.array([p.stress() for p in down])
    grid = np.unique(np.concatenate([up_sr, dn_sr]))
    if grid.size < 2:
        return {"hysteresis_area_pa_per_s": 0.0}
    up_i = np.interp(grid, up_sr[np.argsort(up_sr)], up_ss[np.argsort(up_sr)])
    dn_i = np.interp(grid, dn_sr[np.argsort(dn_sr)], dn_ss[np.argsort(dn_sr)])
    return {"hysteresis_area_pa_per_s": float(np.trapezoid(np.abs(up_i - dn_i), grid))}


def shear_thinning_assessment(n: float, viscosity: np.ndarray | None = None) -> dict[str, object]:
    """剪切变稀评估：以 Power-law 流动指数 n 为主判据。

    n<1 变稀、≈1 牛顿、>1 增稠；黏度比值与 n 矛盾时以 n 为准并注明。
    """
    eta = None if viscosity is None else np.asarray(viscosity, dtype=float).ravel()
    if n < 0.95:
        assessment = "shear_thinning"
    elif n <= 1.05:
        assessment = "newtonian"
    else:
        assessment = "shear_thickening"
    ratio = None
    note = ""
    if eta is not None and eta.size >= 2 and np.all(eta > 0):
        ratio = float(eta[0] / eta[-1])
        ratio_verdict = "shear_thinning" if ratio > 1.05 else ("shear_thickening" if ratio < 0.95 else "newtonian")
        if ratio_verdict != assessment:
            note = "黏度首末比与 n 判据矛盾（可能为屈服/触变或噪声），以 n 为准"
    return {"assessment": assessment, "n": float(n), "viscosity_ratio": ratio, "note": note}
