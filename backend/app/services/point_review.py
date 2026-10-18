"""Per-point review of suggested experiment points + optimizer rule tuning (补齐 2-17).

Points = DOE plan runs suggested by the optimizer / DOE engine. Before they
become lab work, a human reviews each one: accept / modify (edit factor
values) / reject (with a reason). The review state is stored on the plan's
``parameters`` JSON (``parameters["point_reviews"]``), so no migration is
needed — DOEPlanRow.parameters is already a free JSON column.

Optimizer rules (tuning) are plain Settings fields
(``optimizer_ucb_kappa`` / ``optimizer_early_stop_patience`` /
``optimizer_early_stop_min_delta``) edited via
``GET/POST /api/cockpit/optimizer-rules``; the numpy-UCB engine reads the
effective kappa at suggest() time, and the early-stop mixin reads patience /
min_delta. Engines that do not use these rules (BoTorch LogEI, Optuna TPE,
BayBE) ignore them — the rules endpoint says so.
"""
from __future__ import annotations

from typing import Any

DECISIONS = ("accept", "modify", "reject")


class PointReviewError(ValueError):
    pass


def apply_reviews(runs: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply per-run review decisions. Pure function — no DB access.

    ``runs``: plan runs, each with at least ``run_id`` and ``natural`` (factor dict).
    ``reviews``: [{run_id, decision, factors?, note?}].
    Returns a new list: runs carry a ``review`` field
    {decision, note, modified_factors (only for modify), reviewed_at omitted —
    the caller stamps it}.
    A run with no review keeps ``review=None`` (pending). Re-reviewing a run
    overwrites its previous review. Rejected runs are kept (not deleted) so
    the audit trail shows what was dropped and why.
    """
    by_id = {r.get("run_id"): r for r in runs}
    out = [dict(r) for r in runs]
    for rev in reviews:
        run_id = rev.get("run_id")
        if run_id not in by_id:
            raise PointReviewError(f"run_id {run_id!r} 不在计划中")
        decision = rev.get("decision")
        if decision not in DECISIONS:
            raise PointReviewError(f"decision 必须是 {DECISIONS} 之一， got {decision!r}")
        target = next(o for o in out if o.get("run_id") == run_id)
        modified = None
        if decision == "modify":
            factors = rev.get("factors")
            if not isinstance(factors, dict) or not factors:
                raise PointReviewError(f"run {run_id}: modify 必须给出非空 factors")
            natural = target.get("natural") or {}
            unknown = sorted(set(factors) - set(natural))
            if unknown:
                raise PointReviewError(f"run {run_id}: factors 含计划外因子 {unknown}")
            for k, v in factors.items():
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    raise PointReviewError(f"run {run_id}: 因子 {k} 必须是数值，got {v!r}")
            modified = {k: float(v) for k, v in factors.items()}
        target["review"] = {
            "decision": decision,
            "note": str(rev.get("note") or ""),
            "modified_factors": modified,
        }
    return out


def review_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts + the effective runs (accepted + modified) that proceed to lab."""
    counts = {"pending": 0, "accept": 0, "modify": 0, "reject": 0}
    effective: list[dict[str, Any]] = []
    for r in runs:
        d = (r.get("review") or {}).get("decision")
        if d in counts and d != "pending":
            counts[d] += 1
        else:
            counts["pending"] += 1
        if d in ("accept", "modify"):
            eff = dict(r.get("natural") or {})
            if d == "modify":
                eff.update((r.get("review") or {}).get("modified_factors") or {})
            effective.append({"run_id": r.get("run_id"), "factors": eff})
    return {"counts": counts, "total": len(runs), "effective_runs": effective}


def effective_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Runs that survive review, with modified factor values applied."""
    return review_summary(runs)["effective_runs"]
