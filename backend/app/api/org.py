"""Organization-level dashboard API for cross-project knowledge reuse."""
from __future__ import annotations

import copy
import logging
import threading
import time
from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from ..clock import utcnow
from ..db.models import Campaign, ExperimentRow, ProjectRow
from ..db.session import get_db_session
from ._principal import CurrentUser, optional_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/org", tags=["org"])

# P-1: dashboard aggregates full JSON columns (measured/factors/loop_history)
# per request. Cache the computed payload process-wide with a 120s TTL;
# experiment/campaign/project writes should call invalidate_org_dashboard_cache().
_ORG_DASHBOARD_TTL_S = 120.0
_dashboard_cache: dict = {"at": 0.0, "payload": None}
_dashboard_lock = threading.Lock()


def invalidate_org_dashboard_cache() -> None:
    """Drop the cached dashboard payload (call after org-level writes)."""
    with _dashboard_lock:
        _dashboard_cache["at"] = 0.0
        _dashboard_cache["payload"] = None


def _dashboard_cache_get() -> dict | None:
    with _dashboard_lock:
        if (
            _dashboard_cache["payload"] is not None
            and time.monotonic() - _dashboard_cache["at"] < _ORG_DASHBOARD_TTL_S
        ):
            return _dashboard_cache["payload"]
    return None


def _dashboard_cache_put(payload: dict) -> None:
    with _dashboard_lock:
        _dashboard_cache["at"] = time.monotonic()
        _dashboard_cache["payload"] = payload


@router.get("/dashboard")
def org_dashboard(user: CurrentUser | None = Depends(optional_user)) -> dict:
    # Direct calls (e.g. the TTL test) get the Depends sentinel instead of a
    # resolved user; treat that as the anonymous/service-key path, which
    # keeps the pre-stage-0 full payload. FastAPI always resolves a real
    # CurrentUser (which always carries .principal) or None here, so HTTP
    # behavior is unchanged. (This FastAPI's Depends is a factory function,
    # not a class, so isinstance against it is a TypeError.)
    if user is not None and not hasattr(user, "principal"):
        user = None
    # Stage 0 / SR-005: the cached payload is shared, so band per caller
    # (never mutate the cache). Mirrors formulations._can_read_exact:
    # service keys / dev mode (user None) keep exact values; a signed-in
    # account without formulation.read_exact sees no exact compositions —
    # formulation_preview blanked, avg_weight_pct nulled. Deviation from
    # the stage-0 plan's "current_user" wording, kept deliberately: the
    # formulations endpoints use this same optional_user doctrine, and
    # current_user would 401 operator service keys that read the dashboard.
    cached = _dashboard_cache_get()
    if cached is not None:
        payload = cached
    else:
        payload = _compute_org_dashboard()
        _dashboard_cache_put(payload)
    if user is not None and not user.principal.can("formulation.read_exact"):
        payload = _band_dashboard(payload)
    return payload


def _band_dashboard(payload: dict) -> dict:
    """Blank exact-composition fields for callers without read_exact."""
    banded = copy.deepcopy(payload)
    for performer in banded.get("top_performers") or []:
        performer["formulation_preview"] = ""
    for ingredient in banded.get("ingredient_frequency") or []:
        ingredient["avg_weight_pct"] = None
    return banded


def _compute_org_dashboard() -> dict:
    with get_db_session() as session:
        total_experiments = session.execute(select(func.count()).select_from(ExperimentRow)).scalar() or 0
        total_campaigns = session.execute(select(func.count()).select_from(Campaign)).scalar() or 0
        total_projects = session.execute(select(func.count()).select_from(ProjectRow)).scalar() or 0
        active_projects = session.execute(
            select(func.count()).select_from(ProjectRow).where(ProjectRow.is_archived == False)
        ).scalar() or 0

        domain_counts = session.execute(
            select(ExperimentRow.domain, func.count()).group_by(ExperimentRow.domain)
        ).all()
        by_domain = {d: c for d, c in domain_counts if d}

        top_performers = []
        all_measured = session.execute(select(ExperimentRow.measured)).scalars().all()
        metric_keys = set()
        for m in all_measured:
            if isinstance(m, dict):
                metric_keys.update(m.keys())
        for metric in list(metric_keys)[:5]:
            best = session.execute(
                select(ExperimentRow, ProjectRow)
                .join(ProjectRow, ExperimentRow.project_id == ProjectRow.id, isouter=True)
                .where(ExperimentRow.measured != None)
                .order_by(ExperimentRow.created_at.desc()).limit(50)
            ).all()
            best_exp, best_proj, best_val = None, None, None
            for exp, proj in best:
                if exp.measured and metric in exp.measured:
                    val = exp.measured[metric]
                    if isinstance(val, (int, float)):
                        if best_val is None or val > best_val:
                            best_val, best_exp, best_proj = val, exp, proj
            if best_exp and best_val is not None:
                factors_preview = ", ".join(f"{k}:{v}" for k, v in list(best_exp.factors.items())[:3]) if best_exp.factors else ""
                top_performers.append({
                    "metric": metric, "value": best_val, "experiment_id": best_exp.id,
                    "project_title": best_proj.title if best_proj else "",
                    "formulation_preview": factors_preview,
                    "measured_at": best_exp.created_at.isoformat() if best_exp.created_at else "",
                })

        all_factors = session.execute(select(ExperimentRow.factors)).scalars().all()
        ingredient_counts: dict[str, dict] = {}
        for f in all_factors:
            if isinstance(f, dict):
                for ing, wt in f.items():
                    ingredient_counts.setdefault(ing, {"count": 0, "total_wt": 0.0})
                    ingredient_counts[ing]["count"] += 1
                    ingredient_counts[ing]["total_wt"] += float(wt) if wt else 0
        ingredient_frequency = sorted([
            {"ingredient_name": name, "experiment_count": data["count"],
             "avg_weight_pct": round(data["total_wt"] / data["count"], 2), "best_result_metric": None}
            for name, data in ingredient_counts.items()
        ], key=lambda x: x["experiment_count"], reverse=True)[:20]

        campaigns = session.execute(select(Campaign)).scalars().all()
        converged_count = sum(1 for c in campaigns if c.loop_history and any(r.get("converged") for r in c.loop_history))
        convergence_rate = round(converged_count / len(campaigns), 2) if campaigns else 0.0
        avg_rounds = 0.0
        if campaigns:
            total_rounds = sum(len(c.loop_history) for c in campaigns if c.loop_history)
            total_with_history = sum(1 for c in campaigns if c.loop_history)
            avg_rounds = round(total_rounds / total_with_history, 1) if total_with_history else 0.0

        week_ago = utcnow() - timedelta(days=7)
        recent_experiments = session.execute(
            select(func.count()).select_from(ExperimentRow).where(ExperimentRow.created_at >= week_ago)
        ).scalar() or 0
        recent_campaigns = session.execute(
            select(func.count()).select_from(Campaign).where(Campaign.created_at >= week_ago)
        ).scalar() or 0

    return {
        "total_experiments": total_experiments, "total_campaigns": total_campaigns,
        "total_projects": total_projects, "active_projects": active_projects,
        "by_domain": by_domain, "top_performers": top_performers,
        "ingredient_frequency": ingredient_frequency,
        "convergence_rate": convergence_rate, "avg_rounds_to_converge": avg_rounds,
        "recent_activity": {"experiments_added": recent_experiments, "campaigns_created": recent_campaigns},
    }
