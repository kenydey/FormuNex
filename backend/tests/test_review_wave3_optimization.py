"""审查 2026-11-26 第三波（寻优域）：P3-1、P3-3 定向测试。

P3-1（`optimizer.should_stop_early` 死代码）：本文件不含删除断言——
全仓 grep 核实 `test_95_upgrade.py::test_optimizer_early_stopping` 仍在
直接调用该方法（两处调用方），按任务纪律删除已停止，见汇报。
本文件只覆盖 P3-3。

P3-3(a)：`_ei_acquisition` 无 scipy 回退忽略 `y_best`——回退改为与 EI
同量纲的改进量近似（maximize `max(0, mean-y_best)+kappa·std`，minimize
对称）。scipy 缺失用 `sys.modules["scipy"]=None`（连同 `scipy.stats`
一并封堵，见 test_vision_reaction_eval.py 的教训：已缓存的子模块会让
`from scipy import stats` 绕过单点封堵）。

P3-3(b)：`_surrogate_score` 中 predictor 返回 None 值时不崩溃——mean
取值后 None 即返回 (0.0, 0.0)，并记一条 warning（与 P2-7 的 warning
合并处理；只加 warning，不改 (0.0, 0.0) 返回语义与 ai_suggested 标注，
那是 P2-7 的范围）。
"""
from __future__ import annotations

import sys

import pytest

from app.domain.schemas import DOEPlan, DOERun, ProductDomain
from app.services.active_learning import (
    _ei_acquisition,
    _surrogate_score,
    suggest_next_experiments,
)


@pytest.fixture()
def no_scipy(monkeypatch):
    """封堵 scipy（含已缓存的 scipy.stats 子模块），迫使走回退分支。"""
    monkeypatch.setitem(sys.modules, "scipy", None)
    monkeypatch.setitem(sys.modules, "scipy.stats", None)


@pytest.fixture()
def captured_warnings():
    """捕获 loguru WARNING 级消息（loguru 不走 stdlib logging，caplog 抓不到）。"""
    from loguru import logger

    messages: list[str] = []
    handler_id = logger.add(
        lambda m: messages.append(m), level="WARNING", format="{message}"
    )
    try:
        yield messages
    finally:
        logger.remove(handler_id)


# ── P3-3(a)：无 scipy 回退含 y_best ──────────────────────────────────────


def test_fallback_maximize_includes_y_best(no_scipy):
    """maximize 回退 = max(0, mean-y_best) + kappa·std，随 y_best 变化。"""
    assert _ei_acquisition(mean=30.0, std=1.0, y_best=20.0) == pytest.approx(11.0)
    assert _ei_acquisition(mean=30.0, std=1.0, y_best=25.0) == pytest.approx(6.0)
    # 均值低于当前最优 → 改进量为 0，只剩探索项 kappa·std。
    assert _ei_acquisition(mean=30.0, std=1.0, y_best=35.0) == pytest.approx(1.0)


def test_fallback_minimize_includes_y_best(no_scipy):
    """minimize 回退 = max(0, y_best-mean) + kappa·std，随 y_best 变化。

    旧回退 `kappa·std - mean` 与 y_best 无关：y_best 从 20 变 15 时
    旧值纹丝不动，新值应从 11.0 变为 6.0。
    """
    assert _ei_acquisition(mean=10.0, std=1.0, y_best=20.0, direction="minimize") == pytest.approx(11.0)
    assert _ei_acquisition(mean=10.0, std=1.0, y_best=15.0, direction="minimize") == pytest.approx(6.0)
    # 均值高于当前最优（cost 更差）→ 改进量为 0，只剩探索项。
    assert _ei_acquisition(mean=30.0, std=1.0, y_best=20.0, direction="minimize") == pytest.approx(1.0)
    # 排序语义：cost 越低回退值越高，与 EI 方向一致。
    low = _ei_acquisition(mean=10.0, std=1.0, y_best=20.0, direction="minimize")
    high = _ei_acquisition(mean=30.0, std=1.0, y_best=20.0, direction="minimize")
    assert low > high


def test_fallback_respects_kappa(no_scipy):
    """回退的探索项按 kappa 缩放（两个方向各一例）。"""
    assert _ei_acquisition(mean=30.0, std=1.0, y_best=20.0, kappa=2.0) == pytest.approx(12.0)
    assert (
        _ei_acquisition(mean=10.0, std=1.0, y_best=20.0, kappa=2.0, direction="minimize")
        == pytest.approx(12.0)
    )


# ── P3-3(b)：mean 为 None 时不崩溃且有 warning ──────────────────────────


def test_surrogate_score_none_metric_returns_zero_with_warning(
    monkeypatch, captured_warnings
):
    """predictor 返回 {metric: None}：不抛 TypeError，返回 (0,0) 且有 warning。"""
    monkeypatch.setattr(
        "app.services.predictor.predict", lambda form: {"salt_spray_hours": None}
    )
    mean, std = _surrogate_score({}, "autodeposition_coating", [], "salt_spray_hours")
    assert (mean, std) == (0.0, 0.0)
    assert len(captured_warnings) == 1
    assert "salt_spray_hours" in captured_warnings[0]


def test_surrogate_score_missing_metric_warns_but_keeps_zero_return(
    monkeypatch, captured_warnings
):
    """指标缺失（P2-7 情形）：返回语义仍为 (0,0)（本项只合并 warning，不改返回）。"""
    monkeypatch.setattr(
        "app.services.predictor.predict", lambda form: {"other_metric": 0.5}
    )
    mean, std = _surrogate_score({}, "autodeposition_coating", [], "salt_spray_hours")
    assert (mean, std) == (0.0, 0.0)
    assert len(captured_warnings) == 1
    assert "salt_spray_hours" in captured_warnings[0]


def test_suggest_does_not_crash_when_predictor_returns_none(
    monkeypatch, captured_warnings
):
    """端到端：predictor 全 None 时 suggest_next_experiments 不崩溃。

    ai_suggested 标注保持原样（仍为 True）：是否虚假标注是 P2-7 的范围，
    本项只保证不崩溃 + 有 warning。
    """
    import app.services.active_learning as al

    monkeypatch.setattr(
        "app.services.predictor.predict", lambda form: {"salt_spray_hours": None}
    )
    monkeypatch.setattr(al, "_known_failures", lambda domain: [])
    runs = [DOERun(run_id=i, coded={}, natural={"x": float(v)}) for i, v in enumerate([1.0, 2.0, 3.0], start=1)]
    plan = DOEPlan(
        design="lhs", factors=[], runs=runs, domain=ProductDomain.anticorrosion_coating
    )
    got = suggest_next_experiments(
        plan, [], n_suggest=2, objective_metric="salt_spray_hours"
    )
    assert len(got) == 2
    assert all(r.ai_suggested for r in got)
    assert len(captured_warnings) == 3  # 每个候选点各一条
    assert all("salt_spray_hours" in w for w in captured_warnings)
