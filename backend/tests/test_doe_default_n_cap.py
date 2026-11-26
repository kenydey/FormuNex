"""D6-1 / D6-5：默认试验次数按候选集封顶（2026-11-26 审查第一波）。

根因：默认 n 公式（p + max(5, p//2) / p + max(4, p//2)）从未按候选集
规模封顶 —— 2 组分二次混料默认 n=8 但候选集只有 7 个点，单连续因子
默认 n=6 但 3 水平网格只有 3 个候选点，默认调用直接 422。
修法：仅默认 n（n_runs 未显式传）封顶为 len(cands)；显式 n_runs 仍由
select 按原分支报错；封顶后 n < p 仍抛“参数过多”。
"""
from __future__ import annotations

import math

import pytest

from app.services.doe_designs import constrained_mixture as cm
from app.services.doe_designs.constrained_mixture import factorial_d_optimal
from app.services.doe_designs.d_optimal import ContinuousFactor


def test_two_component_default_n_capped_to_candidates():
    """D6-1：2 组分二次混料默认调用可用 —— 7 runs、中心点在内。"""
    d = cm.design([cm.Component("A", 10, 90), cm.Component("B", 10, 90)], total=100)
    assert len(d.runs) == 7
    assert d.candidates == 7
    assert d.model["parameters"] == 3
    for r in d.runs:
        assert math.isclose(r["A"] + r["B"], 100, abs_tol=1e-4)
        assert 10 - 1e-6 <= r["A"] <= 90 + 1e-6
        assert 10 - 1e-6 <= r["B"] <= 90 + 1e-6
    # 中心点 (50, 50) 在内：断言坐标而非 _kind 标签 —— 2 组分时中心点与
    # 边中点重合，标签保留先入的 edge_midpoint（P1-C15 已知行为）。
    assert any(r["A"] == 50.0 and r["B"] == 50.0 for r in d.runs)
    assert any("封顶" in n for n in d.notes)


def test_three_component_default_n_unchanged():
    """D6-1 回归锚：候选集充足时默认 n 不变（3 组分二次：p=6 → n=11）。"""
    d = cm.design(
        [cm.Component("A", 0, 100), cm.Component("B", 0, 100), cm.Component("C", 0, 100)],
        total=100,
    )
    assert d.model["parameters"] == 6
    assert len(d.runs) == 11
    assert d.candidates == 16
    assert not any("封顶" in n for n in d.notes)


def test_single_factor_d_optimal_default_n_capped():
    """D6-5：单连续因子 factorial_d_optimal 默认调用可用 —— 3 runs 全网格。"""
    out = factorial_d_optimal([ContinuousFactor("t", 20, 80)], [])
    assert out["candidates"] == 3
    assert out["model"]["parameters"] == 2
    assert len(out["runs"]) == 3
    assert {r["t"] for r in out["runs"]} == {20.0, 50.0, 80.0}


def test_explicit_n_runs_above_candidates_still_errors():
    """契约保留：显式 n_runs 超过候选集仍报错，不被封顶悄悄改写。"""
    with pytest.raises(ValueError, match="候选点只有 7 个"):
        cm.design([cm.Component("A", 10, 90), cm.Component("B", 10, 90)],
                  total=100, n_runs=8)
    with pytest.raises(ValueError, match="候选点只有 3 个"):
        factorial_d_optimal([ContinuousFactor("t", 20, 80)], [], n_runs=6)
