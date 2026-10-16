"""Split conformal prediction intervals and coverage monitoring (方案 §5.4, M6).

Given past (prediction, measurement) pairs for one metric, the absolute
residuals calibrate an interval ``ŷ ± q`` that contains the next measurement
with probability ≥ 1 − α, for any predictor, assuming exchangeability. With a
per-point uncertainty ``σ`` (e.g. the GP standard deviation) the normalised
variant ``ŷ ± q·σ`` adapts the width to the model's own confidence.

:func:`monitor` replays the history in order — each point judged with an
interval calibrated only on the points before it — and reports the realised
coverage, so a drifting process (new raw-material lot, changed method) shows
up as coverage falling below target.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

MIN_CALIBRATION = 5


class ConformalError(ValueError):
    pass


@dataclass
class Pair:
    predicted: float
    actual: float
    sigma: float | None = None
    id: str | None = None


def _score(p: Pair, normalized: bool) -> float:
    r = abs(p.actual - p.predicted)
    if not normalized:
        return r
    if not p.sigma or p.sigma <= 0:
        raise ConformalError("归一化共形需要每个点的 sigma > 0")
    return r / p.sigma


def quantile(scores: list[float], alpha: float) -> float:
    """The ⌈(n+1)(1−α)⌉-th smallest score; +∞ when there are too few points for that level."""
    if not 0 < alpha < 1:
        raise ConformalError("alpha 必须在 (0, 1) 内")
    n = len(scores)
    k = math.ceil((n + 1) * (1 - alpha))
    if n == 0 or k > n:
        return math.inf
    return sorted(scores)[k - 1]


def calibrate(pairs: list[Pair], alpha: float = 0.1, *, normalized: bool = False) -> dict[str, Any]:
    if len(pairs) < MIN_CALIBRATION:
        raise ConformalError(f"至少需要 {MIN_CALIBRATION} 组预测-实测数据，当前 {len(pairs)} 组")
    scores = [_score(p, normalized) for p in pairs]
    q = quantile(scores, alpha)
    min_n = math.ceil(1 / alpha) - 1
    return {"q": q, "alpha": alpha, "n": len(pairs), "normalized": normalized, "finite": math.isfinite(q),
            "min_points_for_finite_interval": min_n,
            "mean_abs_residual": sum(abs(p.actual - p.predicted) for p in pairs) / len(pairs),
            "bias": sum(p.predicted - p.actual for p in pairs) / len(pairs)}


def interval(predicted: float, q: float, sigma: float | None = None) -> tuple[float, float]:
    width = q * (sigma if sigma is not None else 1.0)
    return predicted - width, predicted + width


def monitor(pairs: list[Pair], alpha: float = 0.1, *, normalized: bool = False, window: int = 20) -> dict[str, Any]:
    """Sequential coverage: point i is judged with an interval calibrated on points < i."""
    history = []
    hits: list[bool] = []
    for i, p in enumerate(pairs):
        if i < MIN_CALIBRATION:
            continue
        q = quantile([_score(x, normalized) for x in pairs[:i]], alpha)
        lo, hi = interval(p.predicted, q, p.sigma if normalized else None)
        covered = lo <= p.actual <= hi
        hits.append(covered)
        recent = hits[-window:]
        history.append({"index": i, "id": p.id, "predicted": p.predicted, "actual": p.actual, "lower": lo,
                        "upper": hi, "covered": covered, "rolling_coverage": sum(recent) / len(recent)})
    n = len(hits)
    coverage = sum(hits) / n if n else None
    target = 1 - alpha
    # One-sided binomial check: is the realised coverage implausibly low for the target? (normal approximation)
    alarm = False
    if n >= 10 and coverage is not None:
        se = math.sqrt(target * (1 - target) / n)
        alarm = coverage < target - 2 * se
    recent = hits[-window:]
    return {"target": target, "evaluated": n, "coverage": coverage,
            "rolling_coverage": (sum(recent) / len(recent)) if recent else None, "alarm": alarm,
            "message": ("实测覆盖率显著低于目标：预测模型可能已漂移（原料批次、测试方法或工艺变化），建议重新校准或复核数据"
                        if alarm else ""), "history": history}
