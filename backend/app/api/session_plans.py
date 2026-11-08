"""Session Plan API — structured plans for long tasks (DOE design, multi-round optimization).

Auth is enforced by the global bearer-token middleware (see middleware/api_auth),
same as the other routers; no per-endpoint auth code here.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..services import session_plan as plan_svc
from ._principal import CurrentUser, current_user

router = APIRouter(prefix="/api/session-plans", tags=["session-plans"])

logger = logging.getLogger(__name__)


class PhaseIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    steps: list[str] = Field(..., min_length=1, max_length=64)


class SubmitPlanBody(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    phases: list[PhaseIn] = Field(..., min_length=1, max_length=32)


class DecideBody(BaseModel):
    approved: bool
    actor: str | None = Field(default=None, max_length=128)


class AdvanceBody(BaseModel):
    phase_name: str = Field(..., min_length=1, max_length=128)
    step_idx: int = Field(..., ge=0)


def _to_out(plan: plan_svc.Plan) -> dict:
    return plan.to_dict()


@router.post("", response_model=dict)
def submit_plan(body: SubmitPlanBody, user: CurrentUser = Depends(current_user)) -> dict:
    # P1-A5 (2026-11-04 deep review): submit used to accept any bearer
    # holder binding any session_id, with no owner recorded. The
    # submitter is now persisted for the pending/get/advance owner checks.
    try:
        plan = plan_svc.submit_plan(
            body.session_id,
            [{"name": p.name, "steps": p.steps} for p in body.phases],
            submitted_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _to_out(plan)


@router.get("/pending", response_model=dict)
def list_pending(user: CurrentUser = Depends(current_user)) -> dict:
    """Plans awaiting approval, oldest first (drives the frontend approval center)."""
    # P1-A5: only the submitter's own plans plus legacy plans (no
    # submitted_by on file, grandfathered so in-flight legacy plans stay
    # approvable from the approval center).
    items = [it for it in plan_svc.list_pending_plans()
             if it.get("submitted_by") in (None, user.id)]
    return {"items": items, "total": len(items)}


@router.get("/{plan_id}", response_model=dict)
def get_plan(plan_id: str, user: CurrentUser = Depends(current_user)) -> dict:
    try:
        plan = plan_svc.get_plan(plan_id)
    except ValueError as exc:
        logger.exception("session_plans get_plan failed: %s", exc)
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    # P1-A5: legacy plans (submitted_by None) stay readable by id; new
    # plans are readable only by their submitter.
    if plan.submitted_by is not None and plan.submitted_by != user.id:
        raise HTTPException(status_code=403, detail="只能查看自己提交的计划")
    return _to_out(plan)


@router.post("/{plan_id}/decide", response_model=dict)
def decide_plan(plan_id: str, body: DecideBody, user: CurrentUser = Depends(current_user)) -> dict:
    # Stage 2 / SR-040: the decider is the signed-in account, never a
    # client-supplied actor string. DecideBody.actor stays accepted for
    # backward compatibility but is ignored.
    try:
        plan = plan_svc.decide_plan(plan_id, body.approved, actor=user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_out(plan)


@router.post("/{plan_id}/advance", response_model=dict)
def advance_plan(plan_id: str, body: AdvanceBody, user: CurrentUser = Depends(current_user)) -> dict:
    # P1-A5: advancing used to let any bearer holder mark any plan's
    # steps done and persist them. Pre-load for the owner check first
    # (same 404/409 mapping the advance path already had).
    try:
        existing = plan_svc.get_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if existing is None:
        raise HTTPException(status_code=404, detail=f"plan not found: {plan_id}")
    if existing.submitted_by is not None and existing.submitted_by != user.id:
        raise HTTPException(status_code=403, detail="只能推进自己提交的计划")
    try:
        plan = plan_svc.advance(plan_id, body.phase_name, body.step_idx)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _to_out(plan)
