"""Active Learning DOE advisor (v0.5).

Uses the currently trained surrogate models (or the empirical predictor when
no lab data is available) to score un-run DOE grid points by Expected
Improvement, then returns the N most informative experiments. Replaces the
random sampling in standard DOE with surrogate-guided selection.

v0.7 adds optional baybe Campaign engine for continuous constrained recommendations.
"""
from __future__ import annotations

import uuid

from loguru import logger

from ..domain.schemas import ActiveDoeResult, DOEPlan, DOERun, ExperimentRecord, ProductDomain, Requirement
from . import failure_memory


_MIN_TRAIN_SAMPLES = 3  # minimum records needed before surrogate-guided selection


def _surrogate_score(
    natural: dict[str, float],
    domain: ProductDomain,
    existing: list[ExperimentRecord],
    objective_metric: str,
) -> tuple[float, float]:
    """Return (mean, std) estimate for a candidate point.

    Uses trained registry when ≥ _MIN_TRAIN_SAMPLES records exist; otherwise
    falls back to the empirical predictor + a fixed nominal uncertainty.
    """
    from ..domain import features, knowledge
    from ..domain.schemas import Formulation
    from ..pipeline import reconstruct
    from ..services import predictor
    from ..services.training import registry

    try:
        form: Formulation = reconstruct.formulation_from_factors(domain, natural)
    except Exception:
        form = knowledge.baseline_formulation(Requirement(domain=domain))

    props = predictor.predict(form)
    mean = props.get(objective_metric, 0.0)
    if objective_metric not in props or mean is None:
        # P3-3(b)：predictor 对不可算指标可能留 None（键存在但值为空）；
        # 旧代码把 None 带进下面的 abs(mean) 抛 TypeError，整个
        # suggest_next_experiments 崩掉。指标缺失/None 时记一条 warning
        # （与 P2-7 的 warning 合并处理）；返回语义仍为 (0.0, 0.0)，是否
        # 改返回/改 ai_suggested 标注是 P2-7 的范围，本项不动。
        logger.warning(
            "active learning: predictor has no value for objective metric "
            f"{objective_metric!r} (domain={domain}); returning (0.0, 0.0), "
            "EI will be 0 for this candidate"
        )
        return 0.0, 0.0

    if len(existing) >= _MIN_TRAIN_SAMPLES:
        vec = features.vector(form, None)
        out = registry.predict_with_std(domain, objective_metric, vec)
        if out is not None:
            mean, std, _ = out
            return float(mean), float(std)

    # 2026-09-04 (P4): 训练样本不足时的伪不确定度不再用固定 20% + 绝对
    # 1e-3 地板 —— 量纲极小属性(mean~1e-4)会被 1e-3 地板主导, std≫signal
    # 使 EI/UCB 趋平 → 探索停滞退化为随机。
    #   ① 同属性实测 ≥3 条 → 用实测离散度 pstdev(真实不确定度);
    #   ② 不足 → 比例法, 地板改为相对量纲(mean 的 5%, 下限 1e-6)。
    try:
        measured_rows = [
            float(r.measured[objective_metric])
            for r in existing
            if objective_metric in (r.measured or {})
        ]
    except Exception:
        measured_rows = []
    if len(measured_rows) >= 3:
        import statistics

        spread = statistics.pstdev(measured_rows)
        # 实测有离散度就用实测; pstdev=0(重复实验全同)才回退比例下限。
        std = spread if spread > 0 else abs(mean) * 0.05
    else:
        floor = max(abs(mean) * 0.05, 1e-6)
        std = abs(mean) * 0.20 + floor
    return float(mean), float(std)


def _ei_acquisition(
    mean: float, std: float, y_best: float, kappa: float = 1.0,
    direction: str = "maximize",
) -> float:
    """Expected Improvement acquisition (Gaussian, analytic).

    Falls back to an improvement-magnitude approximation in EI units when
    scipy is unavailable: ``max(0, mean - y_best) + kappa·std`` (maximize)
    or ``max(0, y_best - mean) + kappa·std`` (minimize) — the fallback keeps
    the EI semantics of "improvement over the current best" instead of the
    old y_best-free UCB form.

    v27 P1-10: 支持 minimize —— 此时 improvement = y_best - mean
   （y_best 为已观测最小值）。
    """
    if std < 1e-9:
        return 0.0
    minimize = (direction or "maximize") == "minimize"
    try:
        from scipy import stats  # type: ignore

        if minimize:
            z = (y_best - mean) / std
            return float((y_best - mean) * stats.norm.cdf(z) + std * stats.norm.pdf(z))
        z = (mean - y_best) / std
        return float((mean - y_best) * stats.norm.cdf(z) + std * stats.norm.pdf(z))
    except Exception:
        # P3-3(a)：无 scipy 时回退为与 EI 同量纲的改进量近似（含 y_best）；
        # 旧回退与 y_best 无关（minimize 下回退值甚至不随历史最优变化），
        # 与 EI“相对当前最优的改进量”语义脱节。maximize 对称处理。
        if minimize:
            return float(max(0.0, y_best - mean) + kappa * std)
        return float(max(0.0, mean - y_best) + kappa * std)


def _known_failures(domain) -> list:
    """Out-of-spec experiments for this domain. Empty when nothing has limits."""
    try:
        return failure_memory.failed_records(domain)
    except Exception as exc:
        logger.debug("active learning: failure memory unavailable (%s)", exc)
        return []


def suggest_next_experiments(
    plan: DOEPlan,
    existing: list[ExperimentRecord],
    n_suggest: int = 4,
    objective_metric: str | None = None,
    direction: str = "maximize",
) -> list[DOERun]:
    """Return the n_suggest most informative un-run DOE experiments.

    Scores all runs in the plan by EI, marks the top ones with
    ``ai_suggested=True``, and returns them in priority order.

    If training data is insufficient (< _MIN_TRAIN_SAMPLES), falls back
    to selecting the N runs with highest empirical-predictor variance
    (most spread in coded space from existing observations).
    """
    from ..pipeline.workflow import OBJECTIVE

    if plan.domain is None:
        return plan.runs[:n_suggest]

    obj_metric = objective_metric or OBJECTIVE.get(plan.domain, "salt_spray_hours")

    # v27 P1-10: minimize 时 y_best 取最小值。
    minimize = (direction or "maximize") == "minimize"
    y_best = 0.0
    if existing:
        # D6-2（与 doe_explain._y_best 的 P3-C19 同款修法）：缺失目标指标
        # 的记录跳过，不再默认 0.0 —— minimize 下一条缺失记录曾把
        # y_best 拉成 0.0（真实值 300），全部候选点 EI≈0、选点退化为
        # 计划顺序。全部记录都缺失时仍回 0.0（确实无任何观测时的旧
        # fallback 语义，与解释层一致）。
        vals = [(rec.measured or {}).get(obj_metric) for rec in existing]
        vals = [v for v in vals if v is not None]
        if vals:
            y_best = min(vals) if minimize else max(vals)

    # Runs that breached a spec are negative evidence. Expected improvement
    # cannot see them — a low value and a failed acceptance test look the same
    # to it — so without this the loop keeps proposing neighbours of a
    # composition already known not to work.
    failures = _known_failures(plan.domain)

    scored: list[tuple[float, DOERun]] = []
    for run in plan.runs:
        mean, std = _surrogate_score(run.natural, plan.domain, existing, obj_metric)
        acq = _ei_acquisition(mean, std, y_best, direction=direction)
        if failures:
            acq *= failure_memory.penalty_for(run.natural, failures)
        scored.append((acq, run))

    scored.sort(key=lambda t: t[0], reverse=True)
    result = []
    for _, run in scored[:n_suggest]:
        updated = DOERun(
            run_id=run.run_id,
            coded=run.coded,
            natural=run.natural,
            ai_suggested=True,
        )
        result.append(updated)
    return result


def _legacy_active_learning_doe(
    req: Requirement,
    existing: list[ExperimentRecord] | None,
    n_suggest: int,
    design: str,
    *,
    doe_engine: str = "auto",
    seed: int | None = None,
) -> DOEPlan:
    from ..pipeline.workflow import build_doe

    plan = build_doe(req, design=design, engine=doe_engine, seed=seed)
    plan.notes = f"engine=legacy; AI 主动选点 (n={n_suggest}, design={design})"

    # v27 P1-10: 从 req 目标推导主优化方向 —— 用户显式传 cost 类 minimize
    # 目标时 EI 不再反向推荐。默认目标的主 metric 与 OBJECTIVE 一致，行为不变。
    from ..domain.objective_contract import normalize_objective
    from ..pipeline.workflow import default_objectives

    _objs = [normalize_objective(o) for o in (req.objectives or default_objectives(req.domain))]
    _primary = max(_objs, key=lambda o: (o.weight or 0.0))
    suggested_ids = {
        r.run_id
        for r in suggest_next_experiments(
            plan,
            existing or [],
            n_suggest=n_suggest,
            objective_metric=_primary.metric,
            direction=_primary.direction or "maximize",
        )
    }
    plan.runs = [
        DOERun(
            run_id=r.run_id,
            coded=r.coded,
            natural=r.natural,
            ai_suggested=(r.run_id in suggested_ids),
            # v10: 重建时保留 infeasible 信号，否则 KG 不相容标记在此丢失。
            infeasible=r.infeasible,
            infeasible_reason=r.infeasible_reason,
        )
        for r in plan.runs
    ]
    return plan


def apply_budget_cap(plan, budget_remaining: int | None):
    """v27 P0-3: budget_remaining 非空时截断 plan runs 并在 notes 声明。

    `budget_remaining <= 0` 由各调用方的硬停处理（A-7）；这里只处理
    `0 < budget < len(runs)` 的静默超发。

    P2-4: 截断前把 ``ai_suggested=True`` 的 run 稳定排到前面 —— BayBE
    路径的 plan 顺序即推荐顺序（全是建议点，顺序不变）；legacy 路径的
    plan 顺序是 LHS 设计顺序，建议点散落各处，截前 N 会把刚选出的建议
    点静默换成设计表前几行。缺额再按原设计顺序补。notes 写明保留的
    建议点数。
    """
    if budget_remaining is None:
        return plan
    runs = list(plan.runs or [])
    if len(runs) > budget_remaining:
        ordered = sorted(
            runs, key=lambda r: not bool(getattr(r, "ai_suggested", False))
        )
        kept = ordered[:budget_remaining]
        n_suggested = sum(1 for r in runs if getattr(r, "ai_suggested", False))
        kept_suggested = sum(1 for r in kept if getattr(r, "ai_suggested", False))
        plan.runs = kept
        note = (
            f"budget_remaining={budget_remaining}: "
            f"truncated {len(runs)} runs to {budget_remaining} "
            f"(kept {kept_suggested}/{n_suggested} ai_suggested)"
        )
        plan.notes = f"{plan.notes}\n{note}".strip() if getattr(plan, "notes", None) else note
    return plan


def active_learning_doe(
    req: Requirement,
    existing: list[ExperimentRecord] | None = None,
    n_suggest: int = 4,
    design: str = "lhs",
    *,
    engine: str = "auto",
    campaign_state: str | None = None,
    doe_engine: str = "auto",
    workbench_campaign_id: int | None = None,
    budget_remaining: int | None = None,
    seed: int | None = None,
) -> ActiveDoeResult:
    """Generate a DOE plan and annotate the most informative runs."""
    eng = (engine or "auto").lower()
    existing = existing or []
    if not existing:
        from ..services.training import registry

        existing = list(registry.records_for(req.domain))

    if eng in ("baybe", "auto"):
        from ..services.engines.baybe_engine import BaybeCampaignEngine
        from ..services.engines.doe_registry import baybe_available

        if eng == "baybe" or baybe_available():
            try:
                baybe = BaybeCampaignEngine()
                if baybe.available():
                    result = baybe.recommend(
                        req,
                        campaign_state=campaign_state,
                        measurements=existing,
                        batch_size=n_suggest,
                        design=f"baybe_{design}",
                        workbench_campaign_id=workbench_campaign_id,
                        budget_remaining=budget_remaining,
                        seed=seed,
                    )
                    result.plan.plan_id = uuid.uuid4().hex
                    result.plan.domain = req.domain
                    # v27 P0-3: 预算截断（baybe 路径）。
                    apply_budget_cap(result.plan, budget_remaining)
                    from ..pipeline.workflow import _cache_plan

                    _cache_plan(result.plan)
                    return ActiveDoeResult(
                        plan=result.plan,
                        campaign_state=result.campaign_state,
                        engine="baybe",
                        strategy_label=result.strategy_label,
                        strategy_rationale=result.strategy_rationale,
                        run_explanations=result.run_explanations,
                        anomalies=result.anomalies,
                        recommended_next_action=result.recommended_next_action,
                        budget_remaining=result.budget_remaining,
                        chemical_feasibility=result.chemical_feasibility,
                        physical_constraints=result.physical_constraints,
                        # v16 P2-4: 血缘透传（BaybeRecommendResult 已有该字段）。
                        lab_points_used=getattr(result, "lab_points_used", 0) or 0,
                    )
                # P1 #18: package importable but engine reports unavailable.
                logger.warning(
                    "BayBE reported unavailable; falling back to legacy DOE (engine=%s)",
                    eng,
                )
            except Exception as exc:
                if eng == "baybe":
                    raise
                # P1 #18: engine=auto must not swallow BayBE failures silently.
                logger.warning(
                    "BayBE active-learning failed, falling back to legacy DOE: %s",
                    exc,
                )

    plan = _legacy_active_learning_doe(req, existing, n_suggest, design, doe_engine=doe_engine, seed=seed)
    # v27 P0-3: 预算截断（legacy 路径）。
    apply_budget_cap(plan, budget_remaining)
    from ..pipeline.workflow import _cache_plan
    from ..services.doe_adaptive import enrich_active_doe_result

    _cache_plan(plan)
    base = ActiveDoeResult(plan=plan, campaign_state=None, engine="legacy")
    return enrich_active_doe_result(base, req, existing, budget_remaining=budget_remaining)
