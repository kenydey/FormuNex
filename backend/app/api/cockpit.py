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
