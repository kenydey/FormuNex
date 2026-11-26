"""Constrained mixture designs (方案 §5.3, M6): extreme vertices + D-optimal selection, with optional
process and categorical factors crossed in.

Components carry lower / upper bounds and must sum to ``total`` (100 % of the
formulation, or of the varied part when other ingredients are fixed). The
candidate set follows McLean & Anderson (1966):

* extreme vertices — every combination of all-but-one components at a bound,
  the remaining component taking up the balance when that is within its bounds;
* edge midpoints and thirds — pairs of vertices sharing all but one active bound;
* facet centroids — the mean of the vertices on each bound face;
* the overall centroid, and axial check blends halfway between each vertex
  and the centroid.

:func:`design` then selects ``n`` runs D-optimally for a Scheffé model
(optionally × process factors) — the usual route when bounds make the simplex
designs (lattice / centroid) infeasible.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .d_optimal import CategoricalFactor, ContinuousFactor, MixtureSpace, Model, factorial_candidates, select

TOL = 1e-9
MAX_COMPONENTS = 12


class MixtureError(ValueError):
    pass


@dataclass
class Component:
    name: str
    low: float
    high: float
    cas: str | None = None
    material_id: str | None = None


def check_bounds(components: list[Component], total: float) -> list[Component]:
    """Validate and tighten bounds (implied bounds: hi_i ≤ total − Σlo_others, lo_i ≥ total − Σhi_others)."""
    if len(components) < 2:
        raise MixtureError("混料设计至少需要 2 个组分")
    if len(components) > MAX_COMPONENTS:
        raise MixtureError(f"混料设计最多 {MAX_COMPONENTS} 个组分")
    if len({c.name for c in components}) != len(components):
        raise MixtureError("组分名重复")
    for c in components:
        if c.low < 0 or c.high < c.low:
            raise MixtureError(f"{c.name}：上下限无效（{c.low}～{c.high}）")
    lo_sum, hi_sum = sum(c.low for c in components), sum(c.high for c in components)
    if lo_sum > total + TOL or hi_sum < total - TOL:
        raise MixtureError(f"约束不相容：下限合计 {lo_sum:g}、上限合计 {hi_sum:g}，总量须为 {total:g}")
    out = []
    for c in components:
        others_lo = lo_sum - c.low
        others_hi = hi_sum - c.high
        out.append(Component(c.name, max(c.low, total - others_hi), min(c.high, total - others_lo), c.cas, c.material_id))
    return out


def extreme_vertices(components: list[Component], total: float) -> list[tuple[float, ...]]:
    q = len(components)
    verts: set[tuple[float, ...]] = set()
    for free in range(q):
        others = [i for i in range(q) if i != free]
        for bounds in itertools.product((0, 1), repeat=q - 1):
            x = [0.0] * q
            for i, b in zip(others, bounds, strict=True):
                x[i] = components[i].high if b else components[i].low
            x[free] = total - sum(x[i] for i in others)
            c = components[free]
            if c.low - TOL <= x[free] <= c.high + TOL:
                x[free] = min(max(x[free], c.low), c.high)
                verts.add(tuple(round(v, 9) for v in x))
    return sorted(verts)


def _active(v: tuple[float, ...], components: list[Component]) -> set[tuple[int, int]]:
    out = set()
    for i, (x, c) in enumerate(zip(v, components, strict=True)):
        if abs(x - c.low) <= 1e-7:
            out.add((i, 0))
        if abs(x - c.high) <= 1e-7:
            out.add((i, 1))
    return out


def candidate_points(components: list[Component], total: float) -> list[dict[str, Any]]:
    comps = check_bounds(components, total)
    verts = extreme_vertices(comps, total)
    if not verts:
        raise MixtureError("约束区域为空")
    q = len(comps)
    kinds: dict[tuple[float, ...], str] = {v: "vertex" for v in verts}
    active = [_active(v, comps) for v in verts]
    for a, b in itertools.combinations(range(len(verts)), 2):
        if len(active[a] & active[b]) >= q - 2:
            mid = tuple(round((x + y) / 2, 9) for x, y in zip(verts[a], verts[b], strict=True))
            kinds.setdefault(mid, "edge_midpoint")
            for t in (1 / 3, 2 / 3):  # edge thirds: room for quadratic / cubic terms in few-component mixtures
                pt = tuple(round(x + t * (y - x), 9) for x, y in zip(verts[a], verts[b], strict=True))
                kinds.setdefault(pt, "edge_third")
    for i, c in enumerate(comps):
        for side in (0, 1):
            face = [v for v, act in zip(verts, active, strict=True) if (i, side) in act]
            if len(face) >= 2:
                cen = tuple(round(sum(col) / len(face), 9) for col in zip(*face, strict=True))
                kinds.setdefault(cen, "facet_centroid")
    centroid = tuple(round(sum(col) / len(verts), 9) for col in zip(*verts, strict=True))
    kinds.setdefault(centroid, "centroid")
    for v in verts:
        axial = tuple(round((x + y) / 2, 9) for x, y in zip(v, centroid, strict=True))
        kinds.setdefault(axial, "axial_check_blend")
    names = [c.name for c in comps]
    return [{**dict(zip(names, p, strict=True)), "_kind": k} for p, k in kinds.items()]


@dataclass
class MixtureDesign:
    runs: list[dict[str, Any]]
    candidates: int
    vertices: int
    model: dict[str, Any]
    bounds: dict[str, list[float]]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"runs": self.runs, "candidates": self.candidates, "vertices": self.vertices, "model": self.model,
                "bounds": self.bounds, "notes": self.notes}


def _centroid_index(cands: list[dict[str, Any]], names: list[str]) -> int | None:
    """Index of the overall centroid, matched by coordinates, not by tag.

    P1-C15/P2-C27: the centroid point is always present in the candidate
    set, but ``setdefault`` keeps an earlier kind tag when its coordinates
    coincide with another point (two-component mixtures: the centroid IS
    the edge midpoint). Tag matching then misses it and ``next()`` raises
    StopIteration. Vertices are always tagged, so recompute the centroid
    as their mean and match within 1e-6.
    """
    verts = [c for c in cands if c.get("_kind") == "vertex"]
    if not verts:
        return None
    cen = {n: sum(c[n] for c in verts) / len(verts) for n in names}
    for i, c in enumerate(cands):
        if all(abs(c[n] - cen[n]) < 1e-6 for n in names):
            return i
    return None


def design(components: list[Component], *, total: float = 100.0, n_runs: int | None = None,
           mixture_order: str = "quadratic", process: list[ContinuousFactor] | None = None,
           process_order: str = "linear", categorical: list[CategoricalFactor] | None = None,
           cross: str = "full", seed: int = 0, include_centroid: bool = True, replicate_centroid: int = 0
           ) -> MixtureDesign:
    if mixture_order not in ("linear", "quadratic", "special_cubic"):
        raise MixtureError("mixture_order 只能是 linear / quadratic / special_cubic")
    process, categorical = process or [], categorical or []
    comps = check_bounds(components, total)
    names = [c.name for c in comps]
    mix_points = candidate_points(comps, total)
    n_vertices = sum(1 for p in mix_points if p["_kind"] == "vertex")
    other_points = factorial_candidates(process, categorical)
    cands = [{**m, **o} for m in mix_points for o in other_points]
    model = Model(mixture=MixtureSpace(names, [c.low for c in comps], total), mixture_order=mixture_order,
                  continuous=process, process_order=process_order, categorical=categorical, cross=cross)
    F, terms = model.matrix(cands)
    p = F.shape[1]
    span = total - sum(c.low for c in comps)
    if span <= TOL and len(cands) == 1:
        # D6-4：span==0 即全部组分钉死在一点（P1-C16 只修了 pseudo 的
        # 除零，design 仍走 select，默认 n=8 对 1 个候选点报 422）。
        # 候选集只剩唯一可行点时，在默认 n 计算之前短路，直接返回
        # 单点设计，不经过 select；模型参数不可估，在 model 与 notes
        # 中同时注明。len(cands)==1 的守卫：若还交叉了 process /
        # categorical 因子，候选集不止一个，仍走正常 D-最优选择。
        run = dict(cands[0])
        run["_run"] = 1
        run["_kind"] = run.pop("_kind")
        for nme in names:
            run[nme] = round(float(run[nme]), 6)
        return MixtureDesign(
            runs=[run], candidates=1, vertices=n_vertices,
            model={"mixture_order": mixture_order, "process_order": process_order, "cross": cross,
                   "parameters": p, "terms": terms, "runs": 1, "estimable": False},
            bounds={c.name: [c.low, c.high] for c in comps},
            notes=["组分已全部钉死：唯一可行配方",
                   f"模型参数不可估：仅 1 个候选点，无法估计 {p} 个模型参数"],
        )
    n = n_runs or (p + max(5, p // 2))  # parameters + a few degrees of freedom for lack of fit / error
    capped = False
    if n_runs is None and n > len(cands):
        # D6-1: 默认 n 从未按候选集规模封顶 —— 2 组分二次混料默认 n=8，
        # 候选集只有 7 个点，默认调用直接 422。显式 n_runs 不封顶（仍由
        # select 按原分支报错），只有默认 n 才封顶到候选集规模；封顶后
        # 若 n < p，仍由 select 抛“参数过多”（真不可估，非候选集不足）。
        n = len(cands)
        capped = True
    include = []
    centroid_idx = _centroid_index(cands, names) if (include_centroid and not process and not categorical) else None
    if centroid_idx is not None:
        include = [centroid_idx]
    sel = select(F, n, seed=seed, include=include, terms=terms)
    runs = [dict(cands[i]) for i in sel.indices]
    if replicate_centroid and not process and not categorical and centroid_idx is not None:
        runs += [dict(cands[centroid_idx]) for _ in range(replicate_centroid)]
    for k, r in enumerate(runs, start=1):
        r["_run"] = k
        r["_kind"] = r.pop("_kind")
        for nme in names:
            r[nme] = round(float(r[nme]), 6)
    notes = []
    if capped:
        notes.append(f"默认试验次数按候选点封顶为 {n} 次（候选点共 {len(cands)} 个）")
    if replicate_centroid and not process and not categorical and centroid_idx is None:
        notes.append("未找到中心点候选，跳过中心点重复")
    tightened = [c.name for c, o in zip(comps, components, strict=True) if (c.low, c.high) != (o.low, o.high)]
    if tightened:
        notes.append("按总量约束收紧了上下限：" + "、".join(tightened))
    if sel.d_efficiency_vs_candidates < 90:
        notes.append("D 效率低于候选点全集：可增加试验次数或降低模型阶数")
    return MixtureDesign(runs=runs, candidates=len(cands), vertices=n_vertices,
                         model={"mixture_order": mixture_order, "process_order": process_order, "cross": cross,
                                **sel.to_dict(), "runs": len(runs)},
                         bounds={c.name: [c.low, c.high] for c in comps}, notes=notes)


def factorial_d_optimal(continuous: list[ContinuousFactor], categorical: list[CategoricalFactor], *,
                        order: str = "interactions", n_runs: int | None = None, seed: int = 0) -> dict[str, Any]:
    """D-optimal design for process / categorical factors without a mixture."""
    if not continuous and not categorical:
        raise MixtureError("至少需要一个因子")
    cands = factorial_candidates(continuous, categorical)
    model = Model(continuous=continuous, process_order=order, categorical=categorical)
    F, terms = model.matrix(cands)
    n = n_runs or (F.shape[1] + max(4, F.shape[1] // 2))
    if n_runs is None and n > len(cands):
        # D6-5：与 D6-1 同根因 —— 单连续因子默认 n=6，3 水平网格只有
        # 3 个候选点，默认调用直接 422。仅默认 n 封顶到候选集规模；
        # 显式 n_runs 不封顶，封顶后 n < p 仍由 select 抛“参数过多”。
        n = len(cands)
    sel = select(F, n, seed=seed, terms=terms)
    runs = [{**cands[i], "_run": k} for k, i in enumerate(sel.indices, start=1)]
    return {"runs": runs, "candidates": len(cands), "model": {"order": order, **sel.to_dict(), "runs": len(runs)}}


def as_matrix(runs: list[dict[str, Any]], names: list[str]) -> np.ndarray:
    return np.array([[r[n] for n in names] for r in runs], dtype=float)
