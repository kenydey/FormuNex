"""D-optimal point selection from a candidate set (Fedorov exchange) and the model matrices it needs.

A design point is a dict of factor values. A *model* turns a point into a row
of the model matrix; :func:`select` picks ``n`` candidate rows maximising
``det(XᵀX)``. Supported factor types:

* mixture components (Scheffé linear / quadratic / special-cubic on
  L-pseudo-components, so tight bounds do not ruin conditioning),
* continuous process factors (coded −1…1: linear, + interactions, + quadratic),
* categorical factors (effects coding, levels − 1 columns),

and their combination: additive, or a full mixture × process crossing
(every mixture term times ``1, z1, …, zk`` — the standard combined model).
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# --------------------------------------------------------------------------- model


@dataclass
class ContinuousFactor:
    name: str
    low: float
    high: float
    levels: int = 3  # candidate grid: 2 → {low, high}; 3 → {low, mid, high}; …

    def grid(self) -> list[float]:
        if self.levels < 2:
            raise ValueError(f"{self.name}: levels must be ≥ 2")
        return [self.low + (self.high - self.low) * i / (self.levels - 1) for i in range(self.levels)]

    def code(self, x: float) -> float:
        half = (self.high - self.low) / 2
        return 0.0 if half == 0 else (x - (self.low + self.high) / 2) / half


@dataclass
class CategoricalFactor:
    name: str
    levels: list[Any]


@dataclass
class MixtureSpace:
    names: list[str]
    lower: list[float]
    total: float

    def pseudo(self, point: dict[str, Any]) -> list[float]:
        span = self.total - sum(self.lower)
        return [(float(point[n]) - lo) / span for n, lo in zip(self.names, self.lower, strict=True)]


@dataclass
class Model:
    mixture: MixtureSpace | None = None
    mixture_order: str = "quadratic"  # linear | quadratic | special_cubic
    continuous: list[ContinuousFactor] = field(default_factory=list)
    process_order: str = "linear"  # linear | interactions | quadratic
    categorical: list[CategoricalFactor] = field(default_factory=list)
    cross: str = "full"  # mixture × process: full | additive

    def _mixture_terms(self, z: list[float]) -> tuple[list[float], list[str]]:
        names = self.mixture.names  # type: ignore[union-attr]
        vals, labels = list(z), list(names)
        if self.mixture_order in ("quadratic", "special_cubic"):
            for i, j in itertools.combinations(range(len(z)), 2):
                vals.append(z[i] * z[j])
                labels.append(f"{names[i]}·{names[j]}")
        if self.mixture_order == "special_cubic":
            for i, j, k in itertools.combinations(range(len(z)), 3):
                vals.append(z[i] * z[j] * z[k])
                labels.append(f"{names[i]}·{names[j]}·{names[k]}")
        return vals, labels

    def _process_terms(self, point: dict[str, Any]) -> tuple[list[float], list[str]]:
        zs = [f.code(float(point[f.name])) for f in self.continuous]
        vals, labels = list(zs), [f.name for f in self.continuous]
        if self.process_order in ("interactions", "quadratic"):
            for i, j in itertools.combinations(range(len(zs)), 2):
                vals.append(zs[i] * zs[j])
                labels.append(f"{self.continuous[i].name}·{self.continuous[j].name}")
        if self.process_order == "quadratic":
            for i, z in enumerate(zs):
                vals.append(z * z)
                labels.append(f"{self.continuous[i].name}²")
        for c in self.categorical:
            idx = c.levels.index(point[c.name])
            for k in range(len(c.levels) - 1):  # effects coding
                vals.append(1.0 if idx == k else (-1.0 if idx == len(c.levels) - 1 else 0.0))
                labels.append(f"{c.name}[{c.levels[k]}]")
        return vals, labels

    def row(self, point: dict[str, Any]) -> tuple[list[float], list[str]]:
        proc, plabels = self._process_terms(point)
        if self.mixture is None:
            return [1.0, *proc], ["(截距)", *plabels]
        mix, mlabels = self._mixture_terms(self.mixture.pseudo(point))
        if not proc:
            return mix, mlabels
        if self.cross == "full":
            vals, labels = list(mix), list(mlabels)
            for p, pl in zip(proc, plabels, strict=True):
                vals += [m * p for m in mix]
                labels += [f"{ml}×{pl}" for ml in mlabels]
            return vals, labels
        return [*mix, *proc], [*mlabels, *plabels]

    def matrix(self, points: list[dict[str, Any]]) -> tuple[np.ndarray, list[str]]:
        rows, labels = [], []
        for p in points:
            r, labels = self.row(p)
            rows.append(r)
        return np.array(rows, dtype=float), labels


# --------------------------------------------------------------------------- selection


@dataclass
class Selection:
    indices: list[int]
    log_det: float
    d_efficiency: float  # (det(XᵀX / n))^(1/p) × 100 — comparable across designs of one model
    max_prediction_variance: float  # max over candidates of n·f(x)ᵀ(XᵀX)⁻¹f(x); ≥ p, = p is G-optimal
    p: int
    terms: list[str]
    # Per-run information relative to running every candidate once: (det(XᵀX/n) / det(FᵀF/N))^(1/p) × 100.
    # Scale-free, so it is meaningful for mixtures too; a good selection is ≥ 100.
    d_efficiency_vs_candidates: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"log_det": self.log_det, "d_efficiency": self.d_efficiency,
                "d_efficiency_vs_candidates": self.d_efficiency_vs_candidates,
                "max_prediction_variance": self.max_prediction_variance, "parameters": self.p, "terms": self.terms}


def _logdet(X: np.ndarray) -> float:
    sign, ld = np.linalg.slogdet(X.T @ X)
    return ld if sign > 0 else -math.inf


def select(F: np.ndarray, n: int, *, seed: int = 0, starts: int = 8, allow_repeats: bool = False,
           max_iter: int = 200, include: list[int] | None = None, terms: list[str] | None = None) -> Selection:
    """Fedorov exchange from several random starts; *include* rows are forced into the design."""
    N, p = F.shape
    if n < p:
        raise ValueError(f"试验次数 {n} 少于模型参数个数 {p}，无法估计模型（至少需要 {p} 次）")
    if not allow_repeats and n > N:
        raise ValueError(f"候选点只有 {N} 个，少于要求的 {n} 次试验（可允许重复点）")
    include = list(include or [])
    rng = np.random.default_rng(seed)
    best: list[int] | None = None
    best_ld = -math.inf
    ridge = 1e-9 * np.eye(p)
    for _ in range(max(1, starts)):
        pool = [i for i in range(N) if i not in include]
        start = include + list(rng.choice(pool, size=n - len(include), replace=allow_repeats or len(pool) < n - len(include)))
        design = [int(i) for i in start]
        for _it in range(max_iter):
            X = F[design]
            M_inv = np.linalg.inv(X.T @ X + ridge)
            d_all = np.einsum("ij,jk,ik->i", F, M_inv, F)
            best_gain, swap = 1e-10, None
            for pos, i in enumerate(design):
                if pos < len(include):
                    continue
                fi = F[i]
                d_i = d_all[i]
                d_ij = F @ (M_inv @ fi)
                gain = d_all - (d_i * d_all - d_ij ** 2) - d_i  # Fedorov Δ(x_i → x_j)
                if not allow_repeats:
                    gain[design] = -np.inf
                j = int(np.argmax(gain))
                if gain[j] > best_gain:
                    best_gain, swap = gain[j], (pos, j)
            if swap is None:
                break
            design[swap[0]] = swap[1]
        ld = _logdet(F[design])
        if ld > best_ld:
            best_ld, best = ld, list(design)
    if best is None or best_ld == -math.inf:
        raise ValueError("候选点不足以支撑该模型（信息矩阵奇异）：请增加候选点或降低模型阶数")
    X = F[best]
    M_inv = np.linalg.inv(X.T @ X)
    d_eff = 100.0 * math.exp(best_ld / p) / n
    max_var = float(n * np.max(np.einsum("ij,jk,ik->i", F, M_inv, F)))
    ld_all = _logdet(F)
    rel = 100.0 * math.exp(((best_ld - p * math.log(n)) - (ld_all - p * math.log(N))) / p) if ld_all > -math.inf else 0.0
    return Selection(best, float(best_ld), float(d_eff), max_var, p, terms or [], float(rel))


def factorial_candidates(continuous: list[ContinuousFactor], categorical: list[CategoricalFactor]
                         ) -> list[dict[str, Any]]:
    axes = [[(f.name, v) for v in f.grid()] for f in continuous] + [[(c.name, v) for v in c.levels] for c in categorical]
    if not axes:
        return [{}]
    size = math.prod(len(a) for a in axes)
    if size > 50_000:
        raise ValueError(f"候选网格有 {size} 个点，过大：请减少因子或水平数")
    return [dict(combo) for combo in itertools.product(*axes)]
