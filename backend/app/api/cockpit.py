"""Optimisation cockpit (方案 §5.4, M6): conformal prediction intervals with coverage monitoring,
Pareto ranking with hazard as an objective, and “设为定型” (winning formulation → product revision)."""
from __future__ import annotations

import math
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ._principal import CurrentUser, current_user

router = APIRouter(prefix="/cockpit", tags=["cockpit"])


def _session():
    from ..db.database import default_session_factory

    return default_session_factory()()


def _doe_plan_row(session, plan_id: str):
    """DOE 计划 ORM 行（读写 parameters["point_reviews"] 用）。"""
    from ..db.models import DOEPlanRow

    return session.get(DOEPlanRow, plan_id)


class PairIn(BaseModel):
    predicted: float
    actual: float
    sigma: float | None = Field(default=None, gt=0)
    id: str | None = None


class PredictIn(BaseModel):
    predicted: float
    sigma: float | None = Field(default=None, gt=0)
    id: str | None = None


class ConformalIn(BaseModel):
    metric: str = ""
    pairs: list[PairIn] = Field(min_length=1, max_length=5000)  # in time order
    alpha: float = Field(default=0.1, gt=0, lt=1)
    normalized: bool = False
    window: int = Field(default=20, ge=5, le=500)
    predict: list[PredictIn] = Field(default_factory=list, max_length=1000)


def _finite(x: float) -> float | None:
    return x if math.isfinite(x) else None


@router.post("/conformal")
def conformal_intervals(body: ConformalIn) -> dict[str, Any]:
    """Calibrate ``ŷ ± q`` (or ``ŷ ± q·σ``) on past prediction / measurement pairs; interval for new
    predictions; sequential coverage check of the history."""
    from ..services.analysis import conformal as cf

    pairs = [cf.Pair(**p.model_dump()) for p in body.pairs]
    try:
        cal = cf.calibrate(pairs, body.alpha, normalized=body.normalized)
        mon = cf.monitor(pairs, body.alpha, normalized=body.normalized, window=body.window)
    except cf.ConformalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    intervals = []
    for p in body.predict:
        if body.normalized and not p.sigma:
            raise HTTPException(status_code=422, detail="归一化共形：每个预测点都需要 sigma")
        lo, hi = cf.interval(p.predicted, cal["q"], p.sigma if body.normalized else None)
        intervals.append({"id": p.id, "predicted": p.predicted, "lower": _finite(lo), "upper": _finite(hi)})
    cal["q"] = _finite(cal["q"])
    for h in mon["history"]:
        h["lower"], h["upper"] = _finite(h["lower"]), _finite(h["upper"])
    return {"metric": body.metric, "calibration": cal, "intervals": intervals, "monitor": mon}


class ConformalFromLabIn(BaseModel):
    metric: str = Field(min_length=1, max_length=80)
    doe_plan_id: str | None = None
    project_id: str | None = None
    alpha: float = Field(default=0.1, gt=0, lt=1)
    normalized: bool = False
    window: int = Field(default=20, ge=5, le=500)
    predict: list[PredictIn] = Field(default_factory=list, max_length=1000)


@router.post("/conformal/from-lab")
def conformal_from_lab(body: ConformalFromLabIn) -> dict[str, Any]:
    """Conformal intervals calibrated on lab tasks dispatched with predictions and their reviewed results."""
    from ..services.lab.candidates import conformal_pairs

    with _session() as session:
        pairs = conformal_pairs(session, body.metric, doe_plan_id=body.doe_plan_id, project_id=body.project_id)
    if not pairs:
        raise HTTPException(status_code=422, detail="没有带预测值且已复核的实验数据")
    if body.normalized:
        pairs = [p for p in pairs if p.get("sigma")]
    out = conformal_intervals(ConformalIn(metric=body.metric, pairs=[PairIn(**p) for p in pairs], alpha=body.alpha,
                                          normalized=body.normalized, window=body.window, predict=body.predict))
    out["source"] = {"pairs": len(pairs), "doe_plan_id": body.doe_plan_id, "project_id": body.project_id}
    return out


class ObjectiveIn(BaseModel):
    metric: str
    direction: Literal["maximize", "minimize", "match_target"] = "maximize"
    target_value: float | None = None


class CompositionItem(BaseModel):
    name: str = ""
    pct: float = Field(ge=0, le=100)
    cas: str | None = None
    material_id: str | None = None
    substance_id: str | None = None


class PointIn(BaseModel):
    id: str
    values: dict[str, float] = Field(default_factory=dict)
    composition: list[CompositionItem] | None = None  # needed when "hazard_score" is an objective


class ParetoIn(BaseModel):
    points: list[PointIn] = Field(min_length=1, max_length=2000)
    objectives: list[ObjectiveIn] = Field(min_length=1, max_length=6)
    properties: dict[str, Any] | None = None


@router.post("/pareto")
def pareto(body: ParetoIn) -> dict[str, Any]:
    """Non-dominated sorting; the objective ``hazard_score`` (minimise) is computed from each point's
    composition with the GB 30000 engine when not supplied."""
    from ..domain.schemas import ObjectiveSpec
    from ..services.plm.ghs import service as ghs_service
    from ..services.plm.ghs.hazard_score import hazard_score
    from ..services.tradeoff_analysis import compute_pareto_ranks

    metrics = [o.metric for o in body.objectives]
    hazards: dict[str, dict[str, Any]] = {}
    if "hazard_score" in metrics:
        with _session() as session:
            for p in body.points:
                if "hazard_score" in p.values:
                    continue
                if not p.composition:
                    raise HTTPException(status_code=422, detail=f"点 {p.id} 缺少组成，无法计算危害评分")
                res = ghs_service.classify_items(session, [c.model_dump(exclude_none=True) for c in p.composition],
                                                 body.properties, include_transport=False).to_dict()
                hazards[p.id] = {"hazard_score": hazard_score(res), "signal_word": res.get("signal_word"),
                                 "pictograms": [x["code"] for x in res.get("pictograms", [])]}
    rows, missing = [], []
    for p in body.points:
        vals = {**p.values, **({"hazard_score": hazards[p.id]["hazard_score"]} if p.id in hazards else {})}
        absent = [m for m in metrics if m not in vals]
        if absent:
            missing.append({"id": p.id, "missing": absent})
        rows.append([float(vals.get(m, math.nan)) for m in metrics])
    if missing:
        raise HTTPException(status_code=422, detail={"message": "部分点缺少目标值", "points": missing})
    objectives = [ObjectiveSpec(metric=o.metric, direction=o.direction, target_value=o.target_value)
                  for o in body.objectives]
    ranks = compute_pareto_ranks(rows, objectives)
    out = [{"id": p.id, "rank": r, "values": dict(zip(metrics, row, strict=True)), **hazards.get(p.id, {})}
           for p, r, row in zip(body.points, ranks, rows, strict=True)]
    return {"objectives": [o.model_dump() for o in body.objectives], "points": out,
            "frontier": [x["id"] for x in out if x["rank"] == 0]}


class WinningIn(BaseModel):
    product_id: str
    formulation_version_id: str
    experiment_id: int
    spec_snapshot: dict[str, Any] | None = None
    note: str = Field(default="", max_length=2000)


@router.post("/winning", status_code=201)
def set_winning(body: WinningIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    """设为定型: new product revision (candidate) bound to the winning experiment and formulation version."""
    from ..services.plm.documents.service import DocumentError
    from ..services.plm.release import signoff
    from ..services.plm.winning import select_winning

    with _session() as session:
        try:
            rev, warnings = select_winning(session, product_id=body.product_id,
                                           formulation_version_id=body.formulation_version_id,
                                           experiment_id=body.experiment_id, user_id=user.id,
                                           principal=user.principal, spec_snapshot=body.spec_snapshot, note=body.note)
        except signoff.Forbidden as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from None
        except (signoff.ReleaseError, DocumentError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        session.commit()
        return {"revision_id": rev.id, "rev": rev.rev, "state": rev.state,
                "winning_experiment_id": rev.winning_experiment_id, "spec_snapshot": rev.spec_snapshot,
                "warnings": warnings}


# ---------------------------------------------------------------------------
# 补齐 2-17: 逐点审核 + 规则微调
# ---------------------------------------------------------------------------

class OptimizerRulesIn(BaseModel):
    """寻优规则：仅 numpy-UCB 引擎与早停判断读取。

    ucb_kappa: UCB 权重 μ + κσ（κ 越大越偏探索）；patience/min_delta: 早停。
    """

    ucb_kappa: float | None = Field(default=None, gt=0, le=10)
    early_stop_patience: int | None = Field(default=None, ge=1, le=100)
    early_stop_min_delta: float | None = Field(default=None, gt=0, le=1)


@router.get("/optimizer-rules")
def get_optimizer_rules() -> dict[str, Any]:
    """返回当前寻优规则（运行时 overlay 生效）。"""
    from ..config import get_settings
    from ..services.runtime_secrets import effective_setting

    s = get_settings()
    return {
        "ucb_kappa": float(effective_setting(s, "optimizer_ucb_kappa") or 1.5),
        "early_stop_patience": int(effective_setting(s, "optimizer_early_stop_patience") or 5),
        "early_stop_min_delta": float(effective_setting(s, "optimizer_early_stop_min_delta") or 1e-4),
    }


@router.post("/optimizer-rules")
def set_optimizer_rules(body: OptimizerRulesIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    """微调寻优规则：写 .env（FORMUNEX_OPTIMIZER_*）+ 运行时 overlay，立即生效。

    写入模式沿用 api-token 端点（write_env_updates + runtime set）。
    """
    from ..services.runtime_secrets import get_runtime_secrets
    from ..services.secrets_store import write_env_updates

    del user  # 权限由 RBAC guard 把关（admin.llm，写 .env 运营配置）；见 _rbac_guard.py。
    updates: dict[str, str] = {}
    if body.ucb_kappa is not None:
        updates["FORMUNEX_OPTIMIZER_UCB_KAPPA"] = repr(float(body.ucb_kappa))
    if body.early_stop_patience is not None:
        updates["FORMUNEX_OPTIMIZER_EARLY_STOP_PATIENCE"] = str(int(body.early_stop_patience))
    if body.early_stop_min_delta is not None:
        updates["FORMUNEX_OPTIMIZER_EARLY_STOP_MIN_DELTA"] = repr(float(body.early_stop_min_delta))
    if updates:
        write_env_updates(updates)
        secrets = get_runtime_secrets()
        if "FORMUNEX_OPTIMIZER_UCB_KAPPA" in updates:
            secrets.set("optimizer_ucb_kappa", float(body.ucb_kappa))  # type: ignore[arg-type]
        if "FORMUNEX_OPTIMIZER_EARLY_STOP_PATIENCE" in updates:
            secrets.set("optimizer_early_stop_patience", int(body.early_stop_patience))  # type: ignore[arg-type]
        if "FORMUNEX_OPTIMIZER_EARLY_STOP_MIN_DELTA" in updates:
            secrets.set("optimizer_early_stop_min_delta", float(body.early_stop_min_delta))  # type: ignore[arg-type]
    return get_optimizer_rules()


class PointReviewIn(BaseModel):
    run_id: int
    decision: Literal["accept", "modify", "reject"]
    factors: dict[str, float] | None = None
    note: str = ""


class PointReviewsIn(BaseModel):
    reviews: list[PointReviewIn] = Field(min_length=1)


@router.get("/doe/{plan_id}/points")
def doe_points(plan_id: str, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    """返回 DOE 计划逐点列表（带已存审核）。"""
    from ..db import doe_plan_store

    plan = None
    with _session() as session:
        plan = doe_plan_store.load(session, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="DOE 计划不存在")
    with _session() as session:
        row = _doe_plan_row(session, plan_id)
        stored: dict[str, dict[str, Any]] = {}
        if row is not None and isinstance(row.parameters, dict):
            stored = row.parameters.get("point_reviews") or {}
    runs = [dict(r) for r in plan.runs] if isinstance(plan.runs, list) else []
    for r in runs:
        key = str(r.get("run_id"))
        if key in stored:
            r["review"] = stored[key]
    from ..services.point_review import review_summary

    return {"plan_id": plan_id, "status": getattr(plan, "status", None),
            "runs": runs, "summary": review_summary(runs)}


@router.post("/doe/{plan_id}/points/review")
def review_doe_points(plan_id: str, body: PointReviewsIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    """逐点审核：accept / modify（改因子值）/ reject。

    驳回的点保留记录但不进入有效点；复审覆盖旧审核。
    """
    from ..db import doe_plan_store
    from ..services.point_review import apply_reviews, review_summary

    del user
    with _session() as session:
        plan = doe_plan_store.load(session, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="DOE 计划不存在")
    if getattr(plan, "status", None) not in ("draft", "active"):
        raise HTTPException(status_code=422, detail=f"计划状态 {getattr(plan, 'status', None)} 不可审核")
    runs = [dict(r) for r in plan.runs] if isinstance(plan.runs, list) else []
    try:
        reviewed = apply_reviews(runs, [rv.model_dump() for rv in body.reviews])
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    stored = {str(r.get("run_id")): r["review"] for r in reviewed if "review" in r}
    with _session() as session:
        row = _doe_plan_row(session, plan_id)
        if row is None:
            raise HTTPException(status_code=404, detail="DOE 计划不存在")
        params = dict(row.parameters or {})
        params["point_reviews"] = stored
        row.parameters = params  # 重新赋值触发 SQLAlchemy 脏标记
        session.commit()
    return {"plan_id": plan_id, "summary": review_summary(reviewed)}


# ---------------------------------------------------------------------------
# 补齐 2-19: 灵敏度分析
# ---------------------------------------------------------------------------

class SensitivityIn(BaseModel):
    points: list[dict[str, Any]] = Field(min_length=1)
    selected_id: str | int | None = None
    n_samples: int = Field(default=8192, ge=256, le=65536)


@router.post("/sensitivity")
def sensitivity(body: SensitivityIn) -> dict[str, Any]:
    """Sobol 一阶指数 + 局部梯度灵敏度。

    points: [{id, factors: {name: value}, objective: float}]。
    因子名取全部点因子并集排序；缺失因子 → 422（不静默补 0）。
    """
    from ..services.analysis.sensitivity import SensitivityError, analyze

    import numpy as np

    factor_names = sorted({k for p in body.points for k in (p.get("factors") or {})})
    if not factor_names:
        raise HTTPException(status_code=422, detail="points 缺少 factors")
    X, y, ids = [], [], []
    for p in body.points:
        factors = p.get("factors") or {}
        missing = [f for f in factor_names if f not in factors]
        if missing:
            raise HTTPException(status_code=422, detail=f"点 {p.get('id')} 缺因子 {missing}")
        if "objective" not in p:
            raise HTTPException(status_code=422, detail=f"点 {p.get('id')} 缺 objective")
        X.append([float(factors[f]) for f in factor_names])
        y.append(float(p["objective"]))
        ids.append(p.get("id"))
    selected = body.selected_id
    if selected is None:
        best_idx = int(np.argmax(np.asarray(y, dtype=float)))
        selected = ids[best_idx]
    try:
        idx = ids.index(selected)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"selected_id {selected} 不在 points 中") from None
    try:
        return analyze(
            np.asarray(X, dtype=float), np.asarray(y, dtype=float), factor_names,
            x0=np.asarray(X[idx], dtype=float), n_samples=int(body.n_samples),
        )
    except SensitivityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


# ---------------------------------------------------------------------------
# 补齐 2-20: 流变拟合
# ---------------------------------------------------------------------------

class RheologyFitIn(BaseModel):
    points: list[dict[str, float]] = Field(min_length=1)
    model: Literal["auto", "power_law", "herschel_bulkley", "cross"] = "auto"


@router.post("/rheology/fit")
def rheology_fit(body: RheologyFitIn) -> dict[str, Any]:
    """流变曲线拟合：Power-law / Herschel–Bulkley / Cross（标准模型）。

    points: [{shear_rate, viscosity}] 或 [{shear_rate, shear_stress}]。
    auto: 三模型都拟合，取 R² 最高者。
    """
    from ..services.rheology import FitError, FlowPoint, fit_flow_curve, fit_power_law, shear_thinning_assessment, thixotropy_index

    try:
        points = [FlowPoint(shear_rate=float(p["shear_rate"]),
                            viscosity=float(p["viscosity"]) if "viscosity" in p else None,
                            shear_stress=float(p["shear_stress"]) if "shear_stress" in p else None)
                  for p in body.points]
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"points 需含 shear_rate + viscosity（或 shear_stress）: {exc}") from None
    try:
        out = fit_flow_curve(points, model=body.model)
    except FitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    result: dict[str, Any] = {
        "model": out["model"], "params": out["params"], "r2": out["r2"],
        "rmse": out["rmse"], "comparison": out.get("comparison"),
    }
    if out["model"] == "cross":
        etas = [p.eta() for p in sorted(points, key=lambda p: p.shear_rate)]
        result.update(thixotropy_index(etas[0], etas[-1]))
        result["thixotropy_note"] = "两点筛查定义 η(低剪切)/η(高剪切)，非循环剪切曲线滞后面积"
    n = float(fit_power_law(points)["params"]["n"])  # type: ignore[index]
    etas = [p.eta() for p in sorted(points, key=lambda p: p.shear_rate)]
    import numpy as np

    result["shear_thinning"] = shear_thinning_assessment(n, np.asarray(etas, dtype=float))
    return result


# ---------------------------------------------------------------------------
# 补齐 2-21: 色度 / 酸稳定性
# ---------------------------------------------------------------------------

class AcidTrendIn(BaseModel):
    formulations: list[dict[str, Any]] = Field(min_length=1)
    ph_min: float = Field(default=2.0, ge=0, le=14)
    ph_max: float = Field(default=6.0, ge=0, le=14)
    ph_step: float = Field(default=0.5, gt=0, le=2)


@router.post("/acid-stability/trend")
def acid_stability_trend(body: AcidTrendIn) -> dict[str, Any]:
    """酸稳定性趋势：逐配方扫浴液 pH，返回每 pH 的 status/violations/reasons。"""
    from ..domain.schemas import Formulation, Ingredient, ProductDomain
    from ..services.acid_stability import check_acid_stability

    if body.ph_max < body.ph_min:
        raise HTTPException(status_code=422, detail="ph_max 须 ≥ ph_min")
    phs = []
    ph = body.ph_min
    while ph <= body.ph_max + 1e-9:
        phs.append(round(ph, 3))
        ph += body.ph_step
    out = []
    for f in body.formulations:
        try:
            form = Formulation(
                name=str(f.get("name", "未命名")),
                domain=ProductDomain.anticorrosion_coating,
                ingredients=[Ingredient(**ing) for ing in f.get("ingredients", [])],
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"配方 {f.get('name')} 组分无效: {exc}") from None
        series = []
        for ph in phs:
            r = check_acid_stability(form, ph)
            series.append({"ph": ph, "status": r.status, "stable": r.stable, "reasons": r.reasons})
        out.append({"name": form.name, "points": series})
    return {"ph_values": phs, "series": out}


class ColorIn(BaseModel):
    formulations: list[dict[str, Any]] = Field(min_length=1)
    reference_lab: tuple[float, float, float] | None = None


@router.post("/color")
def color_page(body: ColorIn) -> dict[str, Any]:
    """色度：各配方 Lab + 与参考色的双口径 ΔE（ΔE00 / ΔE*ab）。

    delta_e_00 为 CIEDE2000（colour-science 未安装时为 null，不静默 fallback）；
    delta_e_ab 为 CIE76 欧氏距离（始终可用）；delta_e 为兼容键，等于
    delta_e_00（有）或 delta_e_ab（无），delta_e_method 说明实际口径。
    无色素配方返回 L/a/b/delta_e_* 全部为 null、delta_e_method 为 null。
    """
    from ..domain.schemas import Formulation, Ingredient, ProductDomain
    from ..services.colorimetry import color_metrics, delta_e_2000, delta_e_ab

    out = []
    for f in body.formulations:
        try:
            form = Formulation(
                name=str(f.get("name", "未命名")),
                domain=ProductDomain.anticorrosion_coating,
                ingredients=[Ingredient(**ing) for ing in f.get("ingredients", [])],
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"配方 {f.get('name')} 组分无效: {exc}") from None
        m = color_metrics(form)
        item: dict[str, Any] = {"name": form.name, **m}
        if body.reference_lab is not None:
            # P1-15: 无色素配方的 L/a/b 为 null，不可算 ΔE——显式 null 而非 500。
            if m["L"] is None or m["a"] is None or m["b"] is None:
                item["delta_e_00_vs_reference"] = None
                item["delta_e_ab_vs_reference"] = None
            else:
                lab = (m["L"], m["a"], m["b"])
                item["delta_e_00_vs_reference"] = delta_e_2000(lab, tuple(body.reference_lab))
                item["delta_e_ab_vs_reference"] = delta_e_ab(lab, tuple(body.reference_lab))
        out.append(item)
    return {"formulations": out, "reference_lab": body.reference_lab}


@router.get("/color/materials")
def color_materials() -> dict[str, Any]:
    """带 CIELAB 值的原料清单（供前端组分选择）。

    与 /api/materials 同源：读 domain.knowledge.RAW_MATERIALS，
    只返回含 lab 值的原料。
    """
    from ..domain.knowledge import RAW_MATERIALS

    materials = []
    for name, spec in RAW_MATERIALS.items():
        lab = spec.get("lab")
        if not lab:
            continue
        materials.append({
            "name": name, "cas": spec.get("cas_no"), "role": spec.get("role"),
            "lab": [lab[0], lab[1], lab[2]],
            "L": lab[0], "a": lab[1], "b": lab[2],
        })
    materials.sort(key=lambda d: d["name"])
    return {"count": len(materials), "materials": materials}
