"""Self-driving research loop orchestration (v0.6, P0).

Wires together the pieces that already exist independently into a single
one-click iteration:

    existing lab data  →  (auto-fetch from registry)
                       →  optimize with the freshly-blended models
                       →  generate the next active-learning DOE batch
                       →  report model RMSE/R² status

Pure CPU / pure Python: reuses workflow.run_optimization (which blends trained
models via predictor._blend_trained), active_learning.active_learning_doe, and
the training registry. Degrades gracefully when there is no lab data yet
(empirical prior + LHS point selection).
"""
from __future__ import annotations

from collections.abc import Callable

from ..config import get_settings
from ..domain.schemas import (
    DOEPlan,
    LoopReport,
    OptimizationResult,
    ProductDomain,
    Requirement,
)


def _rmse_by_metric(domain: ProductDomain) -> tuple[list, dict[str, float]]:
    """Return (model_info_for_domain, {metric: rmse}) from the trained registry."""
    from .training import registry

    infos = [m for m in registry.info() if m.domain == domain]
    rmse = {m.metric: m.rmse for m in infos}
    return infos, rmse


def _cost_summary(optimization: OptimizationResult) -> dict | None:
    """Average cost_cny_per_kg / voc_gpl across top formulations from predictions."""
    costs, vocs = [], []
    for f in optimization.top_formulations or []:
        pred = getattr(f, "predicted", None) or {}
        if pred.get("cost_cny_per_kg") is not None:
            try:
                costs.append(float(pred["cost_cny_per_kg"]))
            except (TypeError, ValueError):
                pass
        if pred.get("voc_gpl") is not None:
            try:
                vocs.append(float(pred["voc_gpl"]))
            except (TypeError, ValueError):
                pass
    if not costs and not vocs:
        return None
    return {
        "cost_cny_per_kg": round(sum(costs) / len(costs), 2) if costs else None,
        "voc_gpl": round(sum(vocs) / len(vocs), 1) if vocs else None,
        "n": len(costs) + len(vocs),
    }


# B-7: 收敛判定统一入口（services/convergence.py）。
# 以下名字保留为 re-export，兼容既有 import 方（tests / doe_cycle_service）。
from .convergence import (  # noqa: F401  (re-exports: tests import them from here)
    REASON_NONE,
    REASON_RMSE_PLATEAU,
    REASON_TARGET_ACHIEVED,
    best_objective_value,
    evaluate_convergence,
    primary_objective_spec,
    rmse_generation_stamp,
    rmse_plateau_detected,
    target_achieved,
)


def _stub_optimization(req: Requirement) -> OptimizationResult:
    from ..domain.project_spec import primary_objective

    return OptimizationResult(
        iterations=0,
        objective=primary_objective(req),
        history=[],
        top_formulations=[],
        engine="skipped-converged",
        measurement_source="skipped",
    )


def _stub_doe(req: Requirement, reason: str = "rmse_plateau") -> DOEPlan:
    """P3-2: 收敛 hold 计划 —— 已收敛即"无需新实验"，返回空 run。

    此前这里以"保留上一轮 DOE"名义返回一个从未存在过的合成中点单点
    计划；下游若把它当待执行实验消费，会调度一个无意义的中点实验。
    空计划 + 如实 notes 才是"无需新实验"。设计名 ``converged-hold``
    与 ``plan_id="converged"`` 保留（调用方与既有测试依赖该信号）。
    """
    from ..domain.project_spec import levers_to_doe_factors

    levers = req.levers or []
    factors = levers_to_doe_factors(levers[:6])
    notes = (
        "目标已达成 — 已收敛，未生成新实验"
        if reason == "target_achieved"
        else "模型 RMSE 已收敛 — 已收敛，未生成新实验"
    )
    return DOEPlan(
        design="converged-hold",
        factors=factors,
        runs=[],
        notes=notes,
        plan_id="converged",
        domain=req.domain,
    )


def loop_iterate(
    req: Requirement,
    optimize_iterations: int = 24,
    n_suggest: int = 4,
    progress_cb: Callable[[float, str], None] | None = None,
    *,
    optimize_engine: str = "auto",
    doe_engine: str = "auto",
    workbench_campaign_id: int | None = None,
    campaign_state: str | None = None,
    prior_rmse_history: list[dict[str, float]] | None = None,
    prior_optimization: OptimizationResult | None = None,
    prior_next_doe: DOEPlan | None = None,
    budget_remaining: int | None = None,
    seed: int | None = None,
    # P2-3: 与 prior_rmse_history 并行的训练代际戳（见
    # convergence.rmse_generation_stamp）；调用方未传时沿用旧语义。
    prior_rmse_generations: list[str | None] | None = None,
) -> LoopReport:
    """Run one full turn of the self-driving loop and bundle the result."""
    from . import active_learning
    from ..pipeline import workflow
    from .training import registry

    settings = get_settings()

    if progress_cb:
        progress_cb(0.05, "loading lab history")
    records = registry.records_for(req.domain)
    model_info, rmse = _rmse_by_metric(req.domain)
    # P2-3: 当前训练代际戳（随 LoopReport 透传；LoopReport.rmse_generation
    # 字段由协调员在 doe.py 落盘后生效，在此之前该 kwarg 被 pydantic 忽略）。
    current_generation = rmse_generation_stamp(model_info)

    history = list(prior_rmse_history or [])
    # B-7: 收敛判定统一走 services/convergence.evaluate_convergence
    #（目标达成优先于 RMSE 平台期；None 数据永不判收敛）。
    obj_spec = primary_objective_spec(req)
    converged, convergence_reason = evaluate_convergence(
        prior_rmse_history=history,
        current_rmse=rmse,
        records=records,
        objective=obj_spec,
        enabled=settings.loop_convergence_enabled,
        eps=settings.loop_convergence_eps,
        patience=settings.loop_convergence_patience,
        prior_generations=prior_rmse_generations,
        current_generation=current_generation,
    )
    best_measured = best_objective_value(
        records, obj_spec.metric, obj_spec.direction
    )

    chem = None

    if converged:
        if progress_cb:
            progress_cb(1.0, "converged — skipping optimize")
        optimization = prior_optimization or _stub_optimization(req)
        next_doe = prior_next_doe or _stub_doe(req, reason=convergence_reason)
        if convergence_reason == "target_achieved":
            loop_message = (
                f"目标已达成（{obj_spec.metric} 最优测量值 {best_measured:g}"
                f"已达到目标 {obj_spec.target_value:g}），建议停止闭环迭代"
            )
            next_action = "目标已达成，建议汇总结果并停止新增实验。"
        else:
            loop_message = "模型 RMSE 已进入平台期，建议停止闭环迭代"
            next_action = "模型 RMSE 已收敛，建议汇总结果并停止新增实验。"
        return LoopReport(
            domain=req.domain.value,
            total_records=len(records),
            model_info=model_info,
            rmse_by_metric=rmse,
            rmse_generation=current_generation,
            optimization=optimization,
            next_doe=next_doe,
            engine=optimization.engine,
            campaign_state=campaign_state,
            converged=True,
            loop_message=loop_message,
            recommended_next_action=next_action,
            chemical_feasibility=chem,
            cost_summary=_cost_summary(optimization),
        )

    if progress_cb:
        progress_cb(0.1, "optimizing with latest models")

    def _opt_progress(p: float, msg: str) -> None:
        if progress_cb:
            progress_cb(0.1 + p * 0.75, msg)

    optimization = workflow.run_optimization(
        req,
        iterations=optimize_iterations,
        progress_cb=_opt_progress,
        engine=optimize_engine,
        existing_records=records,
        campaign_state=campaign_state,
        workbench_campaign_id=workbench_campaign_id,
        seed=seed,
    )

    if progress_cb:
        progress_cb(0.9, "selecting next experiments")
    # v9: 离散因子项目不用 lhs —— native lhs 对离散是静默 clamp（P1-7），
    # 唯一真正支持离散的设计是 full_factorial。
    from ..domain.project_spec import resolve_levers

    _has_discrete = any(
        getattr(lv, "kind", "") == "discrete" for lv in resolve_levers(req)
    )
    next_result = active_learning.active_learning_doe(
        req,
        existing=records,
        n_suggest=n_suggest,
        design="full_factorial" if _has_discrete else "lhs",
        engine=doe_engine,
        doe_engine=doe_engine,
        campaign_state=campaign_state,
        workbench_campaign_id=workbench_campaign_id,
        budget_remaining=budget_remaining,
        seed=seed,
    )
    chem = getattr(next_result, "chemical_feasibility", None)
    phys = getattr(next_result, "physical_constraints", None)

    if progress_cb:
        progress_cb(1.0, "done")

    loop_message = (
        "⚠ 知识图谱检测到推荐配方骨架存在材料不相容，详见化学可行性字段"
        if chem and not chem.get("feasible")
        else ""
    )
    if phys and phys.get("status") == "infeasible":
        loop_message = "⚠ 物理约束检测到推荐配方骨架不可行（酸性稳定性/合规），详见物理约束字段"

    return LoopReport(
        domain=req.domain.value,
        total_records=len(records),
        model_info=model_info,
        rmse_by_metric=rmse,
        rmse_generation=current_generation,
        optimization=optimization,
        next_doe=next_result.plan,
        engine=optimization.engine,
        campaign_state=getattr(next_result, "campaign_state", None) or campaign_state,
        converged=False,
        loop_message=loop_message,
        strategy_label=next_result.strategy_label,
        strategy_rationale=next_result.strategy_rationale,
        run_explanations=next_result.run_explanations,
        anomalies=next_result.anomalies,
        recommended_next_action=next_result.recommended_next_action,
        budget_remaining=next_result.budget_remaining,
        chemical_feasibility=chem,
        physical_constraints=phys,
        cost_summary=_cost_summary(optimization),
    )
