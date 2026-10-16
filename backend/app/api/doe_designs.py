"""DOE design generators added in M6 (方案 §5.3): Taguchi orthogonal arrays, constrained mixtures
(extreme vertices + D-optimal, optionally crossed with process / categorical factors), factorial
D-optimal designs, and the compliance pre-screen of design runs."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

router = APIRouter(prefix="/doe/designs", tags=["doe"])


def _session():
    from ..db.database import default_session_factory

    return default_session_factory()()


class LevelFactorIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    levels: list[float | str] = Field(min_length=2, max_length=5)
    unit: str = ""


class OrthogonalIn(BaseModel):
    factors: list[LevelFactorIn] = Field(min_length=1, max_length=40)
    interactions: list[tuple[str, str]] = Field(default_factory=list, max_length=40)
    array: str | None = None  # e.g. "L9"; default = smallest array that fits
    persist: bool = False
    project_id: str | None = None


class ComponentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    low: float = Field(ge=0)
    high: float = Field(ge=0)
    cas: str | None = None
    material_id: str | None = None
    substance_id: str | None = None


class FixedIngredientIn(BaseModel):
    name: str
    pct: float = Field(ge=0, le=100)
    cas: str | None = None
    material_id: str | None = None
    substance_id: str | None = None


class ProcessFactorIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    low: float
    high: float
    levels: int = Field(default=3, ge=2, le=7)
    unit: str = ""

    @model_validator(mode="after")
    def _range(self) -> ProcessFactorIn:
        if self.high <= self.low:
            raise ValueError(f"{self.name}: high must exceed low")
        return self


class CategoricalIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    levels: list[str] = Field(min_length=2, max_length=12)


class RedLinesIn(BaseModel):
    substances: list[dict[str, Any]] = Field(default_factory=list)  # {"cas", "max_pct", "name", "reason"}
    forbidden_pictograms: list[str] = Field(default_factory=list)
    forbidden_signal_word: Literal["危险", "警告"] | None = None
    max_hazard_score: float | None = None
    check_rohs: bool = True


class ScreenOptions(BaseModel):
    red_lines: RedLinesIn = Field(default_factory=RedLinesIn)
    properties: dict[str, Any] | None = None  # mixture properties for classification (flash point …)
    drop_flagged: bool = False


class MixtureIn(BaseModel):
    components: list[ComponentIn] = Field(min_length=2, max_length=12)
    fixed: list[FixedIngredientIn] = Field(default_factory=list)
    total: float | None = Field(default=None, gt=0, le=100)  # default: 100 − fixed
    mixture_order: Literal["linear", "quadratic", "special_cubic"] = "quadratic"
    process_factors: list[ProcessFactorIn] = Field(default_factory=list, max_length=6)
    process_order: Literal["linear", "interactions", "quadratic"] = "linear"
    categorical_factors: list[CategoricalIn] = Field(default_factory=list, max_length=4)
    cross: Literal["full", "additive"] = "full"
    n_runs: int | None = Field(default=None, ge=2, le=200)
    replicate_centroid: int = Field(default=0, ge=0, le=6)
    seed: int = 0
    screen: ScreenOptions | None = None
    persist: bool = False
    project_id: str | None = None


class DOptimalIn(BaseModel):
    process_factors: list[ProcessFactorIn] = Field(default_factory=list, max_length=10)
    categorical_factors: list[CategoricalIn] = Field(default_factory=list, max_length=6)
    order: Literal["linear", "interactions", "quadratic"] = "interactions"
    n_runs: int | None = Field(default=None, ge=2, le=200)
    seed: int = 0
    persist: bool = False
    project_id: str | None = None


class ScreenIn(ScreenOptions):
    components: list[ComponentIn] = Field(min_length=1, max_length=40)
    fixed: list[FixedIngredientIn] = Field(default_factory=list)
    runs: list[dict[str, Any]] = Field(min_length=1, max_length=500)


def _plan(design: str, factors: list[dict[str, Any]], runs: list[dict[str, Any]], notes: list[str],
          project_id: str | None):
    """Mirror the design into the existing ``DOEPlan`` store so worksheets / lab tasks can use it."""
    from ..domain.schemas import DOEFactor, DOEPlan, DOERun
    from .doe import _persist_doe_plan

    fs = [DOEFactor(**f) for f in factors]
    out_runs = []
    for i, r in enumerate(runs, start=1):
        natural = {f.name: r[f.name] for f in fs}
        coded = {}
        for f in fs:
            if f.kind == "discrete":
                coded[f.name] = float(f.levels.index(natural[f.name]))
            else:
                half = (f.high - f.low) / 2 or 1.0
                coded[f.name] = round((float(natural[f.name]) - (f.low + f.high) / 2) / half, 6)
        out_runs.append(DOERun(run_id=i, coded=coded, natural=natural))
    plan = DOEPlan(design=design, factors=fs, runs=out_runs, notes="\n".join(notes))
    _persist_doe_plan(plan, project_id=project_id)
    return plan.model_dump()


def _ingredients(components: list[ComponentIn]):
    from ..services.doe_designs.compliance_screen import Ingredient

    return {c.name: Ingredient(c.name, c.cas, c.material_id, c.substance_id) for c in components}


def _fixed(fixed: list[FixedIngredientIn]):
    from ..services.doe_designs.compliance_screen import Ingredient

    return [(Ingredient(f.name, f.cas, f.material_id, f.substance_id), f.pct) for f in fixed]


def _run_screen(components, fixed, runs, opts: ScreenOptions) -> dict[str, Any]:
    from ..services.doe_designs.compliance_screen import RedLines, screen

    with _session() as session:
        return screen(session, runs, _ingredients(components), fixed=_fixed(fixed),
                      red_lines=RedLines(**opts.red_lines.model_dump()), properties=opts.properties)


@router.get("/orthogonal-arrays")
def list_orthogonal_arrays() -> list[dict[str, Any]]:
    from ..services.doe_designs.orthogonal_arrays import catalogue

    return [{"name": a.name, "runs": a.runs, "columns": a.columns,
             "levels": sorted(set(a.levels)), "interactions": a.vectors is not None} for a in catalogue()]


@router.post("/orthogonal")
def orthogonal_design(body: OrthogonalIn) -> dict[str, Any]:
    from ..services.doe_designs import orthogonal_arrays as oa

    try:
        d = oa.design([oa.OAFactor(f.name, list(f.levels)) for f in body.factors],
                      interactions=[tuple(p) for p in body.interactions], array=body.array)
    except (oa.OrthogonalError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    out = d.to_dict()
    if body.persist:
        out["plan"] = _plan(f"orthogonal_{d.array}", [
            {"name": f.name, "low": 0, "high": len(f.levels) - 1, "unit": f.unit, "kind": "discrete",
             "levels": list(f.levels)} for f in body.factors], d.runs, d.notes, body.project_id)
    return out


@router.post("/mixture")
def mixture_design(body: MixtureIn) -> dict[str, Any]:
    from ..services.doe_designs import constrained_mixture as cm
    from ..services.doe_designs.d_optimal import CategoricalFactor, ContinuousFactor

    fixed_total = sum(f.pct for f in body.fixed)
    total = body.total if body.total is not None else 100.0 - fixed_total
    if abs(total + fixed_total - 100.0) > 0.05:
        raise HTTPException(status_code=422, detail=f"变化组分总量 {total:g}% + 固定组分 {fixed_total:g}% ≠ 100%")
    try:
        d = cm.design([cm.Component(c.name, c.low, c.high, c.cas, c.material_id) for c in body.components],
                      total=total, n_runs=body.n_runs, mixture_order=body.mixture_order,
                      process=[ContinuousFactor(p.name, p.low, p.high, p.levels) for p in body.process_factors],
                      process_order=body.process_order,
                      categorical=[CategoricalFactor(c.name, list(c.levels)) for c in body.categorical_factors],
                      cross=body.cross, seed=body.seed, replicate_centroid=body.replicate_centroid)
    except (cm.MixtureError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    out = d.to_dict()
    runs = d.runs
    if body.screen is not None:
        sc = _run_screen(body.components, body.fixed, runs, body.screen)
        out["screen"] = sc
        flagged = set(sc["flagged"])
        for r in runs:
            r["_compliance_ok"] = r["_run"] not in flagged
        if body.screen.drop_flagged:
            runs = [r for r in runs if r["_run"] not in flagged]
            out["runs"] = runs
            if flagged:
                out["notes"].append(f"已剔除不合规试验点 {sorted(flagged)}")
    if body.persist:
        factors = [{"name": c.name, "low": lo, "high": hi, "unit": "%"} for c, (lo, hi) in
                   zip(body.components, d.bounds.values(), strict=True)]
        factors += [{"name": p.name, "low": p.low, "high": p.high, "unit": p.unit} for p in body.process_factors]
        factors += [{"name": c.name, "low": 0, "high": len(c.levels) - 1, "kind": "discrete", "levels": c.levels}
                    for c in body.categorical_factors]
        out["plan"] = _plan(f"mixture_d_optimal_{body.mixture_order}", factors, runs, out["notes"], body.project_id)
    return out


@router.post("/d-optimal")
def d_optimal_design(body: DOptimalIn) -> dict[str, Any]:
    from ..services.doe_designs import constrained_mixture as cm
    from ..services.doe_designs.d_optimal import CategoricalFactor, ContinuousFactor

    try:
        out = cm.factorial_d_optimal(
            [ContinuousFactor(p.name, p.low, p.high, p.levels) for p in body.process_factors],
            [CategoricalFactor(c.name, list(c.levels)) for c in body.categorical_factors],
            order=body.order, n_runs=body.n_runs, seed=body.seed)
    except (cm.MixtureError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if body.persist:
        factors = [{"name": p.name, "low": p.low, "high": p.high, "unit": p.unit} for p in body.process_factors]
        factors += [{"name": c.name, "low": 0, "high": len(c.levels) - 1, "kind": "discrete", "levels": c.levels}
                    for c in body.categorical_factors]
        out["plan"] = _plan(f"d_optimal_{body.order}", factors, out["runs"], [], body.project_id)
    return out


@router.post("/screen")
def screen_runs(body: ScreenIn) -> dict[str, Any]:
    """Compliance pre-screen of arbitrary runs (component name → % of the formulation)."""
    names = {c.name for c in body.components}
    for i, r in enumerate(body.runs, start=1):
        missing = names - set(r)
        if missing:
            raise HTTPException(status_code=422, detail=f"第 {i} 个试验点缺少组分 {sorted(missing)}")
    return _run_screen(body.components, body.fixed, body.runs, body)
