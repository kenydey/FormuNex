"""MCP approval REST API (W3-11 / P1-3 UI).

Backs the frontend McpApprovalDialog: list pending approval requests and
submit allow/deny decisions. The service layer (services/mcp_approval) holds
all business logic; this module is a thin HTTP adapter.

W0 (2026-10-27): both endpoints are personal actions — they require
``current_user`` (a service API key cannot decide for a person). The caller
only sees / decides requests attributable to their own projects (directly,
or via the chat session the request was raised from); ``scope=global`` is
restricted to ``admin.connectors``. The layer is endpoint-enforced in
``_rbac_guard.ENDPOINT_ENFORCED``, not policy-exempt.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..services import mcp_approval
from ..services.mcp_approval import Actor
from ._principal import CurrentUser, current_user

router = APIRouter(prefix="/api/mcp/approvals", tags=["mcp-approvals"])


class DecideRequest(BaseModel):
    decision: str = Field(pattern="^(allow|deny)$")
    scope: str = Field(default="once", pattern="^(once|session|project|global)$")


def _actor(user: CurrentUser) -> Actor:
    return Actor(
        user_id=user.id,
        is_admin=user.principal.can("admin.connectors"),
        project_ids=frozenset(user.principal.project_permissions.keys()),
    )


@router.get("/pending", response_model=dict)
def list_pending(user: CurrentUser = Depends(current_user)) -> dict:
    """Return the caller's decidable pending MCP tool approval requests, with their (redacted) tool-call arguments."""
    return mcp_approval.list_pending(actor=_actor(user))


def _require_admin(user: CurrentUser) -> None:
    # Policy rows are platform-wide config: only admin.connectors may
    # list, revoke, or backfill them (mirrors the global-scope rule in
    # decide_approval).
    if not _actor(user).is_admin:
        raise HTTPException(status_code=403, detail="not_authorized")


@router.get("/policies", response_model=dict)
def list_policies(user: CurrentUser = Depends(current_user)) -> dict:
    """Management view of all MCP tool policies, with expiry state."""
    _require_admin(user)
    return {"policies": mcp_approval.list_policies()}


@router.delete("/policies/{server_id}/{tool_name}/{scope}", response_model=dict)
def revoke_policy(server_id: str, tool_name: str, scope: str,
                  user: CurrentUser = Depends(current_user)) -> dict:
    """Revoke one policy row (allow/ask/block, any scope)."""
    _require_admin(user)
    if scope not in mcp_approval.SCOPES:
        raise HTTPException(status_code=400, detail="scope 非法")
    if not mcp_approval.revoke_policy(server_id, tool_name, scope):
        raise HTTPException(status_code=404, detail="policy_not_found")
    return {"ok": True}


@router.post("/policies/backfill-expiry", response_model=dict)
def backfill_expiry(user: CurrentUser = Depends(current_user)) -> dict:
    """One-time cleanup for pre-0090 allow rows: materialize expires_at
    from created_at + scope TTL, delete already-expired rows."""
    _require_admin(user)
    return mcp_approval.backfill_allow_expiry()


@router.post("/{request_id}/decide", response_model=dict)
def decide(request_id: str, req: DecideRequest, user: CurrentUser = Depends(current_user)) -> dict:
    """Submit an allow/deny decision for a pending approval request."""
    try:
        result = mcp_approval.decide_approval(
            request_id, req.decision, scope=req.scope, actor=_actor(user)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        error = str(result.get("error", "unknown"))
        if error == "request_expired":
            raise HTTPException(status_code=410, detail=error)
        if error == "not_authorized":
            raise HTTPException(status_code=403, detail=error)
        raise HTTPException(status_code=404, detail=error)
    return result
