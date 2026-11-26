"""D6-2：legacy EI 的 y_best 跳过缺失指标记录（2026-11-26 审查第一波）。

根因：suggest_next_experiments 用 rec.measured.get(metric, 0.0)，一条缺失
指标的记录即把 minimize 的 y_best 拉成 0.0，全部候选点 EI≈0、选点退化
为计划顺序；与解释层已修复的 P3-C19（doe_explain._y_best）同款写法。
修法：与 _y_best 同款 —— 缺失记录跳过；全部记录都缺失时仍回 0.0
（确实无任何观测时的旧 fallback 语义）。
"""
from __future__ import annotations

import pytest

from app.domain.schemas import DOEPlan, DOERun, ExperimentRecord, ProductDomain
from app.services.active_learning import suggest_next_experiments


def _rec(measured: dict) -> ExperimentRecord:
    return ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        measured=measured,
        source="lab",
    )


def _plan() -> DOEPlan:
    # 计划顺序故意与 EI 顺序相反：候选均值 310 / 300 / 290（std 均为 10），
    # minimize 下正确 EI 顺序应为 290 > 300 > 310；若 y_best 被毒化为 0.0，
    # 全部 EI≈0，排序退化为计划顺序 310 / 300 / 290。
    runs = [
        DOERun(run_id=i, coded={}, natural={"x": v})
        for i, v in enumerate([310.0, 300.0, 290.0], start=1)
    ]
    return DOEPlan(design="lhs", factors=[], runs=runs,
                   domain=ProductDomain.anticorrosion_coating)


@pytest.fixture()
def _flat_scores(monkeypatch):
    import app.services.active_learning as al

    monkeypatch.setattr(al, "_surrogate_score",
                        lambda natural, *a, **k: (float(natural["x"]), 10.0))
    monkeypatch.setattr(al, "_known_failures", lambda domain: [])


def test_minimize_missing_record_does_not_poison_y_best(_flat_scores):
    """D6-2：minimize + 一条缺失记录，选点排序与全量记录一致。"""
    full = [_rec({"salt_spray_hours": 300.0})]
    poisoned = [_rec({"salt_spray_hours": 300.0}), _rec({})]
    got_full = suggest_next_experiments(_plan(), full, n_suggest=3,
                                        objective_metric="salt_spray_hours",
                                        direction="minimize")
    got_poisoned = suggest_next_experiments(_plan(), poisoned, n_suggest=3,
                                            objective_metric="salt_spray_hours",
                                            direction="minimize")
    assert [r.natural["x"] for r in got_full] == [290.0, 300.0, 310.0]
    assert [r.natural["x"] for r in got_poisoned] == [290.0, 300.0, 310.0]


def test_maximize_missing_record_does_not_change_y_best(_flat_scores):
    """maximize 下缺失记录同样跳过：y_best 取已观测最大值 300。"""
    existing = [_rec({"salt_spray_hours": 300.0}), _rec({})]
    got = suggest_next_experiments(_plan(), existing, n_suggest=3,
                                   objective_metric="salt_spray_hours",
                                   direction="maximize")
    # y_best=300 时 EI：均值 310 最高、300 次之、290 最低。
    assert [r.natural["x"] for r in got] == [310.0, 300.0, 290.0]


def test_all_records_missing_metric_does_not_crash(_flat_scores):
    """全部记录都缺失目标指标时不崩溃，仍返回 n_suggest 个 run。"""
    existing = [_rec({}), _rec({"other_metric": 1.0})]
    got = suggest_next_experiments(_plan(), existing, n_suggest=2,
                                   objective_metric="salt_spray_hours",
                                   direction="minimize")
    assert len(got) == 2
    assert all(r.ai_suggested for r in got)
