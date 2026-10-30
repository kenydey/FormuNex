"""Resolve DOE engine selection (native / pydoe / auto)."""
from __future__ import annotations

import logging
from ..errors import log_handled_exception
from ...domain.schemas import DOEFactor, DOEPlan
from .native_doe_engine import build_native_plan
from .pydoe_engine import (
    _MIXTURE_DESIGNS,
    PYDOE_DESIGNS,
    build_plan_with_fallback,
    pydoe_available,
)

logger = logging.getLogger(__name__)


def baybe_available() -> bool:
    try:
        import baybe  # noqa: F401

        return True
    except Exception as exc:
        log_handled_exception(logger, exc, "optional feature check")
        return False


def resolve_doe_engine(engine: str, design: str) -> str:
    """Return the concrete engine name that will be used."""
    eng = (engine or "auto").lower()
    if eng == "auto":
        if pydoe_available() and design in PYDOE_DESIGNS:
            return "pydoe"
        # Mixture designs must keep explicit-failure semantics even when pyDOE
        # is missing — never let native raise a generic "Unknown design".
        if design in _MIXTURE_DESIGNS:
            return "pydoe"
        return "native"
    if eng == "pydoe":
        # Non-mixture designs still fall back to native when pyDOE is absent
        # (preserves historical resolve behaviour). Mixture designs always route
        # through the pydoe helper so failures raise ValueError("混料…") instead
        # of a generic native "Unknown design".
        if design in _MIXTURE_DESIGNS:
            return "pydoe"
        return "pydoe" if pydoe_available() else "native"
    return "native"


def _is_face_centred(ccd_alpha) -> bool:
    """True for the default CCD (None / "" / "face" / alpha == 1): the one pyDOE can build."""
    if ccd_alpha is None or (isinstance(ccd_alpha, str) and ccd_alpha.strip().lower() in ("", "face", "faced")):
        return True
    try:
        return float(ccd_alpha) == 1.0
    except (TypeError, ValueError):
        return False


def apply_kg_chemical_gate(plan: DOEPlan, requirement) -> DOEPlan:
    """P1-5: KG chemical-compatibility gate, engine-agnostic.

    If the baseline formulation skeleton carries an INHIBITS relation, mark
    every run infeasible. Applied in build_doe_plan regardless of engine
    (pydoe/native), so the safety gate no longer depends on pydoe being
    installed.
    """
    if requirement is None:
        return plan
    try:
        from ..kg_chemical_check import check_formulation_chemistry
        from ...domain import knowledge

        skeleton = (
            requirement.active_formulation
            or knowledge.baseline_formulation(requirement)
        )
        if skeleton is not None:
            chk = check_formulation_chemistry(skeleton, include_synergies=False)
            if not chk.feasible:
                for run in plan.runs:
                    run.infeasible = True
                    run.infeasible_reason = (
                        "; ".join(chk.reasons)
                        or "知识图谱检测到材料不相容"
                    )
    except Exception as exc:
        # Gate must never break DOE generation.
        # v13-5: fail-open 不得静默——warning + plan 留痕，调用方可见安全检查没跑。
        logger = logging.getLogger(__name__)
        logger.warning("KG chemical gate skipped (%s); allowing", exc)
        note = f"KG chemical gate skipped: {exc}"
        plan.notes = f"{plan.notes}\n{note}".strip() if plan.notes else note
    return plan


def apply_wt_sum_gate(plan) -> object:
    """P0-2: flag runs whose continuous wt% factors sum above the cap.

    The cap reuses the BayBE-side ``_max_lever_sum`` (min(100, sum of
    highs)): a formulation cannot exceed 100 wt% because solvent absorbs
    the remainder. Runs already flagged (e.g. rotatable star points outside
    the box) keep their original reason — this gate only adds flags, so the
    "flagged == out-of-box" CCD contract is preserved whenever in-box sums
    stay within the cap. Plans without a factors list (e.g. test doubles)
    pass through untouched; the gate never breaks DOE generation.
    """
    factors = getattr(plan, "factors", None)
    if not factors:
        return plan
    wt_factors = [
        f for f in factors if f.unit == "wt%" and f.kind == "continuous"
    ]
    if len(wt_factors) < 2:
        return plan
    try:
        from .adapters.baybe_space_builder import _max_lever_sum

        cap = _max_lever_sum(factors)
    except Exception as exc:
        logger.warning("wt sum gate skipped (%s); allowing", exc)
        return plan
    names = {f.name for f in wt_factors}
    newly_flagged = 0
    for run in getattr(plan, "runs", []) or []:
        if run.infeasible:
            continue
        natural = getattr(run, "natural", None) or {}
        total = sum(
            v
            for name, v in natural.items()
            if name in names and isinstance(v, (int, float))
        )
        if total > cap + 1e-9:
            run.infeasible = True
            run.infeasible_reason = (
                f"连续 wt% 因子合计 {total:.1f} 超过上限 {cap:.1f}"
                "（配方总和不得超过 100 wt%，溶剂补余量不足）"
            )
            newly_flagged += 1
    if newly_flagged:
        note = (
            f"wt sum gate: {newly_flagged} run(s) marked infeasible "
            f"(continuous wt% sum above {cap:.1f})"
        )
        plan.notes = f"{plan.notes}\n{note}".strip() if plan.notes else note
    return plan


def build_doe_plan(
    factors: list[DOEFactor],
    design: str,
    *,
    engine: str = "auto",
    n: int | None = None,
    requirement=None,
    seed: int | None = None,
    ccd_alpha: str | float | None = None,
) -> DOEPlan:
    # v27 P2-23: n=0 不再静默替换为默认 run 数 —— 显式拒绝。
    # （API 层有 ge=2 防护，这里管内部调用。）
    if n is not None and n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    # v27 P1-11: 固定 run 数设计的 n-ignored 提示统一收口到此。
    # v23 只在 domain/doe.py 的 else 分支加了提示，native ccd / full_factorial
    # 分支和 pydoe 路径漏网。notes 已带提示的不重复加。
    _FIXED_N = frozenset(
        {
            "full_factorial",
            "fractional_factorial",
            "plackett_burman",
            "ccd",
            "simplex_lattice",
            "simplex_centroid",
        }
    )

    def _with_n_hint(plan: DOEPlan) -> DOEPlan:
        if n is not None and design in _FIXED_N and "ignored" not in (plan.notes or ""):
            hint = f"(n={n} ignored: {design} has fixed run count)"
            plan.notes = f"{plan.notes}\n{hint}".strip() if plan.notes else hint
        return plan

    if design == "ccd" and not _is_face_centred(ccd_alpha):
        # pyDOE cannot be asked for an arbitrary alpha, and the pyDOE adapter clips every star point into [low, high] -
        # which is face-centred by another name. A rotatable (or explicit-alpha) CCD keeps its star points outside the
        # box and flags them infeasible, so it is built by the native generator whichever engine was asked for.
        plan = build_native_plan(factors, design, n=n, ccd_alpha=ccd_alpha, seed=seed)
        return _with_n_hint(
            apply_wt_sum_gate(apply_kg_chemical_gate(plan, requirement))
        )
    resolved = resolve_doe_engine(engine, design)
    if resolved == "pydoe":
        plan = build_plan_with_fallback(
            factors, design, n=n, requirement=requirement, seed=seed
        )
    else:
        plan = build_native_plan(factors, design, n=n, ccd_alpha=ccd_alpha, seed=seed)
    # P1-5: gate runs for every engine, not just pydoe.
    return _with_n_hint(
        apply_wt_sum_gate(apply_kg_chemical_gate(plan, requirement))
    )
