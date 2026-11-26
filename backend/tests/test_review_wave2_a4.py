"""第二波（P2）A4 寻优域定向测试：报告 §8 批 3–批 5。

- P2-3：RMSE 快照加训练代际戳（convergence / auto_loop）
- P2-4：apply_budget_cap 优先保留建议点（active_learning）
- P2-5：BayBE 轮内早停依据披露（baybe_engine + OptimizationResult）
- P2-6：来源白名单（training / convergence / api/experiments）
- P2-2：虚拟 seed 点剔除/披露（baybe_engine）
- P2-1：BoTorch 失败记 warning + 降级标记 + 引擎名披露（optimizer）
- P3-2：_stub_doe 诚实化（auto_loop）

BayBE 真实路径测试用 ``pytest.importorskip("baybe")`` / ``baybe_available()``
跳过 —— 沙箱未装 BayBE，标"需 BayBE 环境验证"。
``OptimizationResult`` / ``BaybeRecommendResult`` 的新披露字段在
``doe.py``，由协调员落盘；字段未到之前相关断言以 skip 说明依赖，
日志与信封断言不受影响。
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.domain.schemas import (
    DOEPlan,
    DOERun,
    ExperimentRecord,
    ExperimentSubmission,
    ModelInfo,
    ObjectiveSpec,
    ProductDomain,
    Requirement,
)
from app.services.convergence import (
    best_objective_value,
    evaluate_convergence,
    rmse_generation_stamp,
    rmse_plateau_detected,
)

_REQ = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)


def _obj(target=1000.0, direction="maximize"):
    return ObjectiveSpec(metric="m", target_value=target, direction=direction)


def _model_info(**over):
    base = dict(
        domain=ProductDomain.anticorrosion_coating,
        metric="salt_spray_hours",
        backend="test",
        n_samples=8,
        r2=0.9,
        rmse=0.5,
    )
    base.update(over)
    return ModelInfo(**base)


# ── P2-3：RMSE 快照加训练代际戳 ──────────────────────────────────────

FLAT = [{"m": 1.0}, {"m": 1.0}, {"m": 1.0}]


def test_plateau_same_rmse_same_generation_is_not_convergence():
    """快照扁平但窗口内零重训 → 不判收敛（"没学"≠"学不动了"）。"""
    assert not rmse_plateau_detected(
        FLAT, eps=0.01, patience=2, generations=["g1", "g1", "g1"]
    )


def test_plateau_same_rmse_after_retrain_is_convergence():
    """快照扁平且窗口内发生过重训 → 判收敛。"""
    assert rmse_plateau_detected(
        FLAT, eps=0.01, patience=2, generations=["g1", "g1", "g2"]
    )


def test_plateau_without_generations_keeps_legacy_semantics():
    """未传代际戳的旧调用方沿用旧语义（只看扁平）。"""
    assert rmse_plateau_detected(FLAT, eps=0.01, patience=2)


def test_plateau_generation_length_mismatch_falls_back_to_legacy():
    """代际戳与历史长度失配 → fail-open 沿用旧判定，不静默改判。"""
    assert rmse_plateau_detected(FLAT, eps=0.01, patience=2, generations=["g1"])


def test_plateau_unknown_generation_in_window_falls_back_to_legacy():
    """窗口内有代际未知（None）→ 无法证伪，沿用旧判定。"""
    assert rmse_plateau_detected(
        FLAT, eps=0.01, patience=2, generations=["g1", None, "g1"]
    )


def test_plateau_generation_gate_only_applies_to_flat_window():
    """代际变化不能把"正在改善"的 RMSE 判成平台期。"""
    improving = [{"m": 1.0}, {"m": 0.5}, {"m": 0.2}]
    assert not rmse_plateau_detected(
        improving, eps=0.01, patience=2, generations=["g1", "g2", "g3"]
    )


def test_rmse_generation_stamp_changes_on_retrain():
    infos = [_model_info(trained_at="2026-01-01T00:00:00+00:00")]
    stamp = rmse_generation_stamp(infos)
    assert stamp is not None
    assert rmse_generation_stamp(infos) == stamp  # 同一模型集合戳稳定
    assert rmse_generation_stamp(
        [_model_info(trained_at="2026-01-02T00:00:00+00:00")]
    ) != stamp  # 重训刷新 trained_at → 戳变
    assert rmse_generation_stamp([_model_info(n_samples=9)]) != stamp
    assert rmse_generation_stamp([]) is None
    assert rmse_generation_stamp(None) is None


def test_evaluate_convergence_generation_gate():
    base = dict(
        prior_rmse_history=[{"m": 1.0}, {"m": 1.0}],
        current_rmse={"m": 1.0},
        records=[],
        objective=_obj(),
        enabled=True,
        eps=0.01,
        patience=2,
    )
    # 零重训 → 不收敛
    assert evaluate_convergence(
        **base, prior_generations=["g", "g"], current_generation="g"
    ) == (False, "")
    # 窗口内重训过 → 平台期收敛
    assert evaluate_convergence(
        **base, prior_generations=["g", "g"], current_generation="g2"
    ) == (True, "rmse_plateau")
    # 未传代际 → 旧语义
    assert evaluate_convergence(**base) == (True, "rmse_plateau")


def test_loop_iterate_generation_gate(monkeypatch):
    """P2-3 接线：loop_iterate 收到同代际历史时不因扁平 RMSE 早停。"""
    monkeypatch.setenv("FORMUNEX_LOOP_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("FORMUNEX_LOOP_CONVERGENCE_EPS", "0.01")
    monkeypatch.setenv("FORMUNEX_LOOP_CONVERGENCE_PATIENCE", "2")
    get_settings.cache_clear()

    infos = [_model_info(trained_at="2026-01-01T00:00:00+00:00")]

    def fake_rmse(domain):
        return infos, {m.metric: m.rmse for m in infos}

    monkeypatch.setattr("app.services.auto_loop._rmse_by_metric", fake_rmse)
    monkeypatch.setattr(
        "app.services.training.registry.records_for", lambda domain: []
    )

    from app.services.auto_loop import loop_iterate

    stamp = rmse_generation_stamp(infos)
    rep = loop_iterate(
        _REQ,
        optimize_iterations=1,
        n_suggest=2,
        optimize_engine="numpy",
        doe_engine="legacy",
        prior_rmse_history=[
            {"salt_spray_hours": 0.50},
            {"salt_spray_hours": 0.50},
        ],
        prior_rmse_generations=[stamp, stamp],
    )
    assert rep.converged is False


# ── P2-4：apply_budget_cap 优先保留建议点 ────────────────────────────

from app.services.active_learning import apply_budget_cap  # noqa: E402


def _plan(n=6, suggested_idx=(3, 4)):
    runs = [
        DOERun(
            run_id=i + 1,
            coded={},
            natural={"x": float(i)},
            ai_suggested=(i in suggested_idx),
        )
        for i in range(n)
    ]
    return DOEPlan(design="lhs", factors=[], runs=runs, notes="test")


def test_apply_budget_cap_keeps_suggested_points():
    plan = _plan()
    out = apply_budget_cap(plan, 2)
    assert [r.run_id for r in out.runs] == [4, 5]
    assert all(r.ai_suggested for r in out.runs)
    assert "kept 2/2 ai_suggested" in out.notes


def test_apply_budget_cap_fills_shortfall_in_design_order():
    plan = _plan()
    out = apply_budget_cap(plan, 4)
    assert [r.run_id for r in out.runs] == [4, 5, 1, 2]
    assert "kept 2/2 ai_suggested" in out.notes


def test_apply_budget_cap_no_truncation_keeps_order_and_notes():
    plan = _plan()
    out = apply_budget_cap(plan, 6)
    assert [r.run_id for r in out.runs] == [1, 2, 3, 4, 5, 6]
    assert out.notes == "test"


def test_apply_budget_cap_none_budget_is_noop():
    plan = _plan()
    assert apply_budget_cap(plan, None) is plan


# ── P2-6：来源白名单 ────────────────────────────────────────────────

from app.services.training import TRAINABLE_SOURCES, ModelRegistry  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_model_artifacts(tmp_path, monkeypatch):
    """与 test_training.py 同款隔离：模型产物不写入仓库 data/ 树。"""
    monkeypatch.setenv("FORMUNEX_MODEL_ARTIFACTS_DIR", str(tmp_path / "model_artifacts"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _coating_record(measured, source="lab", zinc=8.0):
    return ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        factors={
            "Zinc phosphate": zinc,
            "Bisphenol-A epoxy (DGEBA)": 38.0,
            "Polyamide hardener": 14.0,
        },
        cure_temperature_c=80.0,
        measured=measured,
        source=source,
    )


def test_trainable_sources_include_csv_import_channel():
    """CSV 回灌是实测通道（io_export.csv_to_records 写 source="csv-import"），
    必须进训练；其余虚拟/未知来源不进。"""
    assert TRAINABLE_SOURCES == {"lab", "workbench", "csv-import"}


def test_registry_add_rejects_virtual_and_unknown_sources(tmp_path, caplog):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    with caplog.at_level(logging.WARNING, logger="app.services.training"):
        reg.add(
            [
                _coating_record({"salt_spray_hours": 500.0}, source="lab"),
                _coating_record({"salt_spray_hours": 99999.0}, source="predictor_virtual"),
                _coating_record({"salt_spray_hours": 1.0}, source="mystery_src"),
            ]
        )
    assert reg.total_records == 1  # 只有 lab 行入库
    assert reg.last_rejected_sources == {"predictor_virtual": 1, "mystery_src": 1}
    assert "rejected" in caplog.text
    assert "predictor_virtual" in caplog.text


def test_registry_add_accepts_csv_import_source(tmp_path):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    reg.add([_coating_record({"salt_spray_hours": 500.0}, source="csv-import")])
    assert reg.total_records == 1
    assert reg.last_rejected_sources == {}


def test_dataset_and_training_rows_exclude_legacy_virtual_rows(tmp_path):
    """存量库里已有的虚拟行（本修复前入库的）同样不进训练集。"""
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    for zinc in (4.0, 6.0, 8.0, 10.0):
        reg.add(
            [_coating_record({"salt_spray_hours": 200.0 + 80.0 * zinc}, zinc=zinc)],
            retrain=False,
        )
    # 模拟修复前已入库的虚拟行：直接进内存，不走 add()。
    reg._records.append(
        _coating_record({"salt_spray_hours": 99999.0}, source="predictor_virtual", zinc=12.0)
    )
    assert len(reg._records) == 5
    data = reg._dataset(ProductDomain.anticorrosion_coating, "salt_spray_hours")
    assert data is not None
    assert len(data[1]) == 4  # 虚拟行不在训练集里
    assert 99999.0 not in list(data[1])
    rows = reg._training_rows(
        ProductDomain.anticorrosion_coating, "salt_spray_hours", ""
    )
    assert len(rows) == 4


def test_best_objective_value_ignores_virtual_sources():
    def rec(val, source):
        r = SimpleNamespace(measured={"m": val})
        r.source = source
        return r

    records = [rec(300.0, "lab"), rec(99999.0, "predictor_virtual")]
    assert best_objective_value(records, "m", "maximize") == 300.0
    assert best_objective_value(records, "m", "minimize") == 300.0
    assert best_objective_value([rec(99999.0, "predictor_virtual")], "m") is None
    assert best_objective_value([rec(99999.0, "baybe_opt")], "m") is None
    # source 缺失按旧默认 "lab" 计（与 measurements_adapter 的 P0-5 口径一致）。
    assert best_objective_value([SimpleNamespace(measured={"m": 42.0})], "m") == 42.0


def test_evaluate_convergence_virtual_record_cannot_trigger_target():
    """P2-6 回归：虚拟源记录的超目标值不得停掉闭环。"""
    virtual = SimpleNamespace(measured={"m": 99999.0})
    virtual.source = "predictor_virtual"
    converged, reason = evaluate_convergence(
        prior_rmse_history=[],
        current_rmse={"m": 1.0},
        records=[virtual],
        objective=_obj(target=1000.0),
        enabled=True,
        eps=0.01,
        patience=2,
    )
    assert (converged, reason) == (False, "")


def test_api_source_validation_rejects_unknown_source():
    from fastapi import HTTPException

    from app.api.experiments import _validate_experiment_sources

    _validate_experiment_sources(
        [
            _coating_record({"salt_spray_hours": 1.0}, source="lab"),
            _coating_record({"salt_spray_hours": 1.0}, source="workbench"),
            _coating_record({"salt_spray_hours": 1.0}, source="baybe_opt"),
            _coating_record({"salt_spray_hours": 1.0}, source="predictor_virtual"),
            _coating_record({"salt_spray_hours": 1.0}, source=""),
        ]
    )  # 已知来源 + 空来源（按 lab 计）均放行
    with pytest.raises(HTTPException) as excinfo:
        _validate_experiment_sources(
            [_coating_record({"salt_spray_hours": 1.0}, source="mystery_src")]
        )
    assert excinfo.value.status_code == 422
    assert "mystery_src" in excinfo.value.detail


def test_submit_experiments_reports_rejected_virtual_records(monkeypatch):
    """虚拟源记录经 POST /api/experiments 提交后不入库，响应如实汇报。"""
    from app.api.experiments import submit_experiments

    class _FakeRegistry:
        def __init__(self):
            self.total_records = 0
            self.last_rejected_sources = {"predictor_virtual": 1}

        def add(self, records, retrain=True):
            return False

        def info(self):
            return []

    fake = _FakeRegistry()
    monkeypatch.setattr("app.api.experiments.registry", fake)
    report = submit_experiments(
        ExperimentSubmission(
            records=[_coating_record({"salt_spray_hours": 99999.0}, source="predictor_virtual")]
        )
    )
    assert report.total_records == 0
    assert "rejected" in report.message
    assert "predictor_virtual" in report.message


# ── P2-1：BoTorch 失败记 warning + 降级标记 + 引擎名披露 ─────────────

def _botorch_instance():
    """无 torch 环境下也可构造：绕过 __post_init__ 的 torch 导入。"""
    import numpy as np

    from app.services.optimizer import BotorchOptimizer, Factor

    obj = object.__new__(BotorchOptimizer)
    obj.factors = [Factor("x", 0, 10), Factor("y", 0, 10)]
    obj.seed = 0
    obj._X = [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0], [2.0, 5.0]]
    obj._y = [10.0, 10.0, 10.0, 10.0, 10.0]
    obj._rng = np.random.default_rng(0)
    obj._bounds = np.array([[0.0, 0.0], [10.0, 10.0]])
    obj.last_acquisition = None
    obj.acquisition_name = "LogEI"
    obj._degraded_random = False
    return obj


def test_botorch_engine_name_before_failure():
    assert _botorch_instance().engine == "botorch-ei"


def test_botorch_gp_failure_warns_and_marks_degraded(caplog):
    """沙箱无 torch：suggest() 走 except 分支（与 GP 拟合失败同一分支），
    断言日志 + 标记 + 引擎名披露。真 GP 失败分支需有 torch 的环境复核。"""
    obj = _botorch_instance()
    with caplog.at_level(logging.WARNING, logger="app.services.optimizer"):
        point = obj.suggest()
    assert len(point) == 2
    assert obj._degraded_random is True
    assert obj.engine == "botorch-ei(degraded-random)"
    assert "degraded to random sampling" in caplog.text
    assert "5 observed points" in caplog.text


# ── P2-5：BayBE 轮内早停依据披露（helper 层，沙箱可跑） ─────────────

from app.services.engines.baybe_engine import (  # noqa: E402
    _drop_fingerprinted_rows,
    _measurement_fingerprints,
    _predicted_early_stop,
    _unwrap_campaign_state,
    _wrap_campaign_state,
)


def test_predicted_early_stop_hit_and_miss():
    obj = ObjectiveSpec(metric="salt_spray_hours", direction="maximize", target_value=1000.0)
    ranked = [(0.9, SimpleNamespace(predicted={"salt_spray_hours": 1200.0}))]
    hit = _predicted_early_stop([obj], ranked, 2)
    assert hit == {
        "basis": "predicted",
        "round": 2,
        "metric": "salt_spray_hours",
        "value": 1200.0,
    }
    # 未达标 → None
    assert _predicted_early_stop(
        [obj], [(0.9, SimpleNamespace(predicted={"salt_spray_hours": 800.0}))], 2
    ) is None
    # minimize：低于目标才算达标
    obj_min = ObjectiveSpec(metric="cost_cny_per_kg", direction="minimize", target_value=100.0)
    assert _predicted_early_stop(
        [obj_min], [(0.9, SimpleNamespace(predicted={"cost_cny_per_kg": 80.0}))], 1
    )["basis"] == "predicted"
    assert _predicted_early_stop(
        [obj_min], [(0.9, SimpleNamespace(predicted={"cost_cny_per_kg": 120.0}))], 1
    ) is None
    # 缺数据 → None（fail-open，不断循环）
    assert _predicted_early_stop([], ranked, 1) is None
    assert _predicted_early_stop([obj], [], 1) is None
    assert _predicted_early_stop(
        [obj], [(0.9, SimpleNamespace(predicted={}))], 1
    ) is None


# ── P2-2：虚拟 seed 点剔除/披露（纯函数层，沙箱可跑） ────────────────

def test_campaign_state_envelope_roundtrip():
    fps = [("1.000000", "2.000000", "500.000000")]
    wrapped = _wrap_campaign_state('{"searchspace": 1}', fps)
    campaign_json, out = _unwrap_campaign_state(wrapped)
    assert campaign_json == '{"searchspace": 1}'
    assert out == fps


def test_campaign_state_unwrap_legacy_passthrough():
    raw = '{"searchspace": 1, "measurements": []}'
    assert _unwrap_campaign_state(raw) == (raw, [])
    assert _unwrap_campaign_state("not json{{") == ("not json{{", [])
    assert _unwrap_campaign_state("") == ("", [])


def test_drop_fingerprinted_rows_keeps_same_factor_lab_row():
    """指纹含指标列：同因子、不同实测值的真 lab 行不得被误杀。"""
    import pandas as pd

    df = pd.DataFrame(
        [
            {"x": 1.0, "y": 2.0, "m": 500.0},  # 虚拟 seed 行
            {"x": 1.0, "y": 2.0, "m": 480.0},  # 同因子真 lab 行 → 保留
            {"x": 3.0, "y": 4.0, "m": 600.0},
        ]
    )
    fps = _measurement_fingerprints(df.iloc[[0]], ["x", "y"], ["m"])
    kept, n = _drop_fingerprinted_rows(df, ["x", "y"], fps, metric_names=["m"])
    assert n == 1
    assert len(kept) == 2
    assert 500.0 not in list(kept["m"])
    assert 480.0 in list(kept["m"])
    assert 600.0 in list(kept["m"])


def test_measurement_fingerprints_deterministic():
    import pandas as pd

    df = pd.DataFrame([{"x": 1.0, "m": 500.0}, {"x": 1.0, "m": 500.0}])
    assert _measurement_fingerprints(df, ["x"], ["m"]) == [
        ("1.000000", "500.000000"),
        ("1.000000", "500.000000"),
    ]
    assert _measurement_fingerprints(df, ["x"], ["m"]) != _measurement_fingerprints(
        pd.DataFrame([{"x": 1.0, "m": 501.0}]), ["x"], ["m"]
    )


# ── P3-2：_stub_doe 诚实化 ──────────────────────────────────────────

from app.services.auto_loop import _stub_doe  # noqa: E402


def test_stub_doe_returns_empty_plan_with_honest_notes():
    plan = _stub_doe(_REQ)
    assert plan.design == "converged-hold"
    assert plan.runs == []
    assert "未生成新实验" in plan.notes
    assert "中点" not in plan.notes
    assert "保留上一轮" not in plan.notes
    plan2 = _stub_doe(_REQ, reason="target_achieved")
    assert plan2.runs == []
    assert "目标已达成" in plan2.notes
    assert "未生成新实验" in plan2.notes


# ── P2-2 / P2-5：BayBE 真实路径（需 BayBE 环境验证） ─────────────────

from app.services.engines.doe_registry import baybe_available  # noqa: E402


def _require_result_field(model, field: str):
    """新披露字段在 doe.py，由协调员落盘；未到之前跳过字段断言。"""
    if field not in model.model_fields:
        pytest.skip(f"{model.__name__}.{field} 待协调员落盘 doe.py 补丁")


@pytest.mark.skipif(not baybe_available(), reason="baybe not installed")
def test_baybe_virtual_seed_evicted_after_lab_arrives(caplog):
    """P2-2 roundtrip：第 1 轮零 lab → 3 个虚拟 seed；第 2 轮带 3 条 lab
    记录 → 虚拟点被剔除（重建 campaign），信封指纹清空。"""
    from app.domain.schemas import BaybeRecommendResult
    from app.services.engines.baybe_engine import BaybeCampaignEngine

    engine = BaybeCampaignEngine()
    with caplog.at_level(logging.INFO, logger="app.services.engines.baybe_engine"):
        r1 = engine.recommend(_REQ, batch_size=4)
    assert r1.engine == "baybe"
    assert r1.campaign_state
    assert "GP training set contains 3 virtual seed point(s)" in caplog.text
    _require_result_field(BaybeRecommendResult, "virtual_seed_points")
    assert r1.virtual_seed_points == 3

    lab_records = [
        ExperimentRecord(
            domain=_REQ.domain,
            factors=r1.plan.runs[i].natural,
            measured={
                "salt_spray_hours": 520.0,
                "cost_cny_per_kg": 18.5,
                "sustainability_idx": 0.72,
            },
            source="lab",
        )
        for i in range(3)
    ]
    with caplog.at_level(logging.INFO, logger="app.services.engines.baybe_engine"):
        r2 = engine.recommend(
            _REQ, campaign_state=r1.campaign_state, measurements=lab_records, batch_size=4
        )
    assert len(r2.plan.runs) == 4
    assert "evicted 3 virtual seed point(s)" in caplog.text
    _, fps = _unwrap_campaign_state(r2.campaign_state)
    assert fps == []
    _require_result_field(BaybeRecommendResult, "virtual_seed_removed")
    assert r2.virtual_seed_removed == 3
    assert r2.virtual_seed_points == 0


@pytest.mark.skipif(not baybe_available(), reason="baybe not installed")
def test_baybe_predicted_early_stop_disclosed(caplog):
    """P2-5 roundtrip：预测值恒超目标 → 第 1 轮即早停，且结果标明
    early_stop_basis="predicted"（预测达标，未经实测）。"""
    from app.domain.schemas import OptimizationResult
    from app.services.engines.baybe_engine import BaybeCampaignEngine

    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[
            ObjectiveSpec(metric="salt_spray_hours", direction="maximize", target_value=1.0)
        ],
    )
    engine = BaybeCampaignEngine()
    with caplog.at_level(logging.INFO, logger="app.services.engines.baybe_engine"):
        result = engine.run_optimization(req, iterations=24)
    # 满跑 6 轮 × 每轮 4 条 = 24 条 history；第 1 轮早停则至多 4 条。
    assert len(result.history) < 24
    assert len(result.history) >= 1
    assert "predicted value, not measured" in caplog.text
    _require_result_field(OptimizationResult, "early_stop_basis")
    assert result.early_stop_basis == "predicted"
    assert result.early_stop_round == 1
    assert result.early_stop_predicted_value is not None
