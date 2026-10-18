"""Optimizer acquisition functions and per-iteration acquisition history (方案 §5.4, 补齐 2-16).

Terminology: the *acquisition function* here is the Bayesian-optimization
criterion (UCB / Expected Improvement / LogEI) that scores candidate
experiment points. It is unrelated to the KB download-acquisition stats in
``services/kb_acquisition_trend.py`` (renamed from ``acquisition_trend.py``
precisely because the shared name misled readers).

Formulas (standard, Jones et al. 1998 for EI):
- UCB: ``a(x) = μ(x) + κ·σ(x)``.
- EI: ``a(x) = (μ − f*)·Φ(Z) + σ·φ(Z)`` with ``Z = (μ − f* − ξ)/σ``;
  ``a(x) = 0`` when ``σ = 0``.
- LogEI: ``log EI`` computed via a numerically stable log-Φ.

Honesty: only engines with a closed-form scalar acquisition report a value.
BoTorch's LogEI is maximised inside ``optimize_acqf`` and is not exposed
per-point without refitting the GP; Optuna's TPE has no scalar acquisition
at all. Those engines report ``acquisition=None`` with their name recorded,
rather than a fabricated proxy number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


def _phi(z: float) -> float:
    """Standard normal CDF Φ(z)."""
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def _phi_pdf(z: float) -> float:
    """Standard normal PDF φ(z)."""
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def _log_phi(z: float) -> float:
    """log Φ(z), stable for very negative z (asymptotic expansion).

    For z >= -10 the direct form is accurate; below that erfc saturates and
    the Mills-ratio expansion takes over.
    """
    if z >= -10.0:
        p = _phi(z)
        return math.log(p) if p > 0.0 else float("-inf")
    # log Φ(z) ≈ -z²/2 - log(-z) - 0.5·log(2π) + log(1 - 1/z² + 3/z⁴)
    z2 = z * z
    return -0.5 * z2 - math.log(-z) - 0.5 * math.log(2.0 * math.pi) + math.log(1.0 - 1.0 / z2 + 3.0 / (z2 * z2))


def ucb(mean: float, sigma: float, kappa: float = 1.5) -> float:
    """Upper confidence bound acquisition: μ + κ·σ."""
    return float(mean + kappa * sigma)


def expected_improvement(mean: float, sigma: float, best_f: float, xi: float = 0.01) -> float:
    """Expected Improvement (Jones et al. 1998). Zero when σ = 0."""
    if sigma <= 0:
        return 0.0
    z = (mean - best_f - xi) / sigma
    return float((mean - best_f - xi) * _phi(z) + sigma * _phi_pdf(z))


def log_expected_improvement(mean: float, sigma: float, best_f: float, xi: float = 0.01) -> float:
    """log EI, stable for large negative Z (where EI underflows to 0)."""
    if sigma <= 0:
        return float("-inf")
    z = (mean - best_f - xi) / sigma
    # log EI = log σ + log( Z·Φ(Z) + φ(Z) ); for Z << 0 the φ term dominates.
    inner = z * _phi(z) + _phi_pdf(z)
    if inner > 0:
        return float(math.log(sigma) + math.log(inner))
    # Z very negative: EI ≈ σ·φ(Z) → log EI ≈ log σ - Z²/2 - 0.5·log(2π)
    return float(math.log(sigma) - 0.5 * z * z - 0.5 * math.log(2.0 * math.pi))


@dataclass
class AcquisitionPoint:
    """One iteration's acquisition value (or None when the engine has no scalar acquisition)."""

    iteration: int
    acquisition: float | None
    acquisition_name: str
    best_score: float
    engine: str


@dataclass
class AcquisitionHistory:
    """Per-iteration acquisition values of one optimization run."""

    points: list[AcquisitionPoint] = field(default_factory=list)

    def append(self, point: AcquisitionPoint) -> None:
        self.points.append(point)

    def series(self) -> list[dict]:
        return [
            {"iteration": p.iteration, "acquisition": p.acquisition,
             "acquisition_name": p.acquisition_name, "best_score": p.best_score, "engine": p.engine}
            for p in self.points
        ]

    def summary(self) -> dict:
        """Trend: mean acquisition of the second half vs the first half.

        A falling acquisition curve is the healthy sign — the surrogate is
        becoming confident and there is less left to gain. A flat/rising
        curve late in the run suggests the search is still exploring.
        """
        vals = [p.acquisition for p in self.points if p.acquisition is not None]
        if len(vals) < 2:
            return {"trend": "insufficient_data", "points": len(self.points),
                    "reported": len(vals), "acquisition_name": self.points[0].acquisition_name if self.points else None}
        half = len(vals) // 2
        first = sum(vals[:half]) / half
        second = sum(vals[half:]) / (len(vals) - half)
        if second < first * 0.8:
            trend = "declining"
        elif second > first * 1.2:
            trend = "rising"
        else:
            trend = "flat"
        return {"trend": trend, "points": len(self.points), "reported": len(vals),
                "first_half_mean": round(first, 4), "second_half_mean": round(second, 4),
                "max_acquisition": round(max(vals), 4),
                "acquisition_name": self.points[0].acquisition_name if self.points else None}
