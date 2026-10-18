"""MCP approval REST API (W3-11 / P1-3 UI).

Backs the frontend McpApprovalDialog: list pending approval requests and
submit allow/deny decisions. The service layer (services/mcp_approval) holds
all business logic; this module is a thin HTTP adapter. Auth is enforced by
the global bearer-token middleware, same as the other routers.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import mcp_approval

router = APIRouter(prefix="/api/mcp/approvals", tags=["mcp-approvals"])


class DecideRequest(BaseModel):
    decision: str = Field(pattern="^(allow|deny)$")
    scope: str = Field(default="once", pattern="^(once|session|project|global)$")


@router.get("/pending", response_model=dict)
def list_pending() -> dict:
    """Return currently pending MCP tool approval requests, with their (redacted) tool-call arguments."""
    return mcp_approval.list_pending()


@router.post("/{request_id}/decide", response_model=dict)
def decide(request_id: str, req: DecideRequest) -> dict:
    """Submit an allow/deny decision for a pending approval request."""
    try:
        result = mcp_approval.decide_approval(
            request_id, req.decision, scope=req.scope
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error", "unknown"))
    return result
