"""Builtin connectors + MCP settings / import API."""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import connectors_builtin, mcp_client, mcp_import
from ._principal import CurrentUser, current_user
from ._uploads import read_upload_capped

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


class ConnectorToggle(BaseModel):
    enabled: bool


class McpServerIn(BaseModel):
    id: str
    command: str
    args: list[str] = Field(default_factory=list)
    enabled: bool = True
    env: dict[str, str] = Field(default_factory=dict)
    transport: str = "stdio"


class McpServersReplace(BaseModel):
    servers: list[McpServerIn]


class McpCallRequest(BaseModel):
    server_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # Attribution for the approval gate: the chat session / project this call
    # runs in. Carried into the pending request so only its owner/project may
    # decide it. Absent attribution stays admin-only at decide time.
    session_id: str | None = None
    project_id: str | None = None


class McpImportJsonBody(BaseModel):
    json_text: str | None = None
    config: dict[str, Any] | None = None
    dry_run: bool = True


class McpImportGithubBody(BaseModel):
    url: str
    ref: str | None = None
    path: str | None = None
    dry_run: bool = True


class McpImportConfirmBody(BaseModel):
    import_id: str


class McpServerEnableBody(BaseModel):
    enabled: bool


def _import_error(payload: dict) -> HTTPException:
    msg = str(payload.get("detail") or "导入失败")
    return HTTPException(status_code=400, detail={"message": msg, **payload})


def _mask_mcp_env(servers: list[dict]) -> list[dict]:
    """Mask MCP env values in HTTP responses (SR-001).

    Internal callers use ``mcp_client.list_mcp_servers()`` directly and keep
    real env; only the HTTP presentation layer masks. The settings UI only
    ever renders env keys (see ``McpServerConfig.env``: "only the keys are
    ever shown"), so masking values changes no UI. Write responses
    (PUT/PATCH/DELETE) stay admin-gated by ``admin_guard`` and are untouched.
    """
    masked: list[dict] = []
    for server in servers:
        server = dict(server)
        env = server.get("env")
        if isinstance(env, dict):
            server["env"] = {str(k): "••••" for k in env}
        masked.append(server)
    return masked


@router.get("")
def list_all() -> dict:
    settings = get_settings()
    return {
        "builtin": connectors_builtin.list_connectors(settings=settings),
        "mcp": _mask_mcp_env(mcp_client.list_mcp_servers()),
        "mcp_client_enabled": settings.mcp_client_enabled,
        "connectors_builtin_enabled": settings.connectors_builtin_enabled,
    }


@router.post("/builtin/{connector_id}/toggle")
def toggle_builtin(connector_id: str, body: ConnectorToggle) -> dict:
    ids = {c["id"] for c in connectors_builtin.CONNECTOR_CATALOG}
    if connector_id not in ids:
        raise HTTPException(status_code=404, detail="unknown connector")
    from ..services.skills_store import load_prefs, save_prefs

    prefs = load_prefs()
    disabled = set(prefs.get("disabled_connector_ids") or [])
    if body.enabled:
        disabled.discard(connector_id)
    else:
        disabled.add(connector_id)
    save_prefs({"disabled_connector_ids": sorted(disabled)})
    return {"builtin": connectors_builtin.list_connectors(settings=get_settings())}


@router.post("/builtin/{connector_id}/search")
def search_builtin(connector_id: str, q: str = "", limit: int = 8) -> dict:
    settings = get_settings()
    if not settings.connectors_builtin_enabled:
        raise HTTPException(status_code=503, detail="builtin connectors disabled")
    if connector_id == "literature":
        rows = connectors_builtin.search_literature(q, limit=limit)
    elif connector_id == "chemistry":
        rows = connectors_builtin.lookup_chemistry(q, limit=limit)
    else:
        raise HTTPException(status_code=404, detail="unknown connector")
    return {"results": [r.model_dump() for r in rows]}


# ── Import first (static paths before /mcp/{server_id}) ─────────────────────


@router.post("/mcp/import")
def mcp_import_json(body: McpImportJsonBody) -> dict:
    if body.config is not None:
        text = json.dumps(body.config, ensure_ascii=False)
    elif body.json_text:
        text = body.json_text
    else:
        raise HTTPException(status_code=400, detail="需要 json_text 或 config")
    result = mcp_import.import_from_json_text(text, dry_run=body.dry_run, source="local")
    payload = mcp_import.result_to_dict(result)
    if not result.ok:
        raise _import_error(payload)
    return payload


@router.post("/mcp/import/github")
def mcp_import_github(body: McpImportGithubBody) -> dict:
    result = mcp_import.import_from_github(
        body.url, ref=body.ref, path=body.path, dry_run=body.dry_run
    )
    payload = mcp_import.result_to_dict(result)
    if not result.ok:
        raise _import_error(payload)
    return payload


def _import_uploaded_json(data: bytes, dry_run: bool) -> dict:
    """Parse, validate and (unless dry-run) stage an uploaded MCP config — file I/O, off the loop."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"无法解码 JSON: {exc}") from exc
    result = mcp_import.import_from_json_text(text, dry_run=dry_run, source="local")
    payload = mcp_import.result_to_dict(result)
    if not result.ok:
        raise _import_error(payload)
    return payload


@router.post("/mcp/import/upload")
async def mcp_import_upload(
    file: UploadFile = File(...),
    dry_run: bool = True,
) -> dict:
    data = await read_upload_capped(file, file.filename or "mcp.json")
    return await run_in_threadpool(_import_uploaded_json, data, dry_run)


@router.post("/mcp/import/confirm")
def mcp_import_confirm(body: McpImportConfirmBody) -> dict:
    result = mcp_import.confirm_import(body.import_id)
    payload = mcp_import.result_to_dict(result)
    if not result.ok:
        raise _import_error(payload)
    return payload


@router.put("/mcp")
def replace_mcp(body: McpServersReplace) -> dict:
    settings = get_settings()
    if not settings.mcp_client_enabled:
        raise HTTPException(status_code=503, detail="mcp_client_enabled is off")
    # Stage 2 / SR-009: a full replace is explicit admin configuration —
    # every entry it carries is blessed for stdio.
    servers = mcp_client.save_mcp_servers(
        [s.model_dump() for s in body.servers],
        bless_ids={s.id for s in body.servers},
    )
    return {"mcp": servers}


@router.patch("/mcp/{server_id}")
def patch_mcp_server(server_id: str, body: McpServerEnableBody) -> dict:
    """Toggle enabled flag without requiring mcp_client_enabled (config-only)."""
    servers = mcp_client.list_mcp_servers()
    hit = next((s for s in servers if s.get("id") == server_id), None)
    if not hit:
        raise HTTPException(status_code=404, detail="server not found")
    hit["enabled"] = bool(body.enabled)
    saved = mcp_client.save_mcp_servers(servers)
    return {"mcp": saved}


@router.delete("/mcp/{server_id}")
def delete_mcp_server(server_id: str) -> dict:
    current = mcp_client.list_mcp_servers()
    if not any(s.get("id") == server_id for s in current):
        raise HTTPException(status_code=404, detail="server not found")
    saved = mcp_client.save_mcp_servers([s for s in current if s.get("id") != server_id])
    return {"mcp": saved}


@router.post("/mcp/{server_id}/probe")
def probe_mcp(server_id: str) -> dict:
    settings = get_settings()
    if not settings.mcp_client_enabled:
        raise HTTPException(status_code=503, detail="mcp_client_enabled is off")
    server = next((s for s in mcp_client.list_mcp_servers() if s.get("id") == server_id), None)
    if not server:
        raise HTTPException(status_code=404, detail="server not found")
    return mcp_client.probe_server(server)


@router.post("/mcp/call")
def call_mcp(body: McpCallRequest, user: CurrentUser = Depends(current_user)) -> dict:
    """Readonly MCP tool proxy (Stage 0 / SR-007).

    Previously this was doubly exempt (POLICY_EXEMPT + admin_guard) with
    the approval gate off by default, so any token holder could call any
    enabled server's read tools — including filesystem reads and
    credential-backed external reads — with no permission, no session
    attribution and no approval. Now: a signed-in account with
    ``knowledge.read``; the approval gate inside ``_call_tool`` still
    applies when ``mcp_approval_enabled``. Each call is logged with the
    caller's user id, and the audit middleware attributes JWT callers
    by login id (stage 2 / SR-042).
    """
    settings = get_settings()
    user.require("knowledge.read")
    out = mcp_client.call_tool_readonly(
        body.server_id,
        body.tool_name,
        body.arguments,
        settings=settings,
        session_id=body.session_id,
        project_id=body.project_id,
    )
    logger.info("mcp.call user=%s server=%s tool=%s ok=%s",
                user.id, body.server_id, body.tool_name, out.get("ok"))
    return out


class McpApproveSessionBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    tool_name: str | None = Field(default=None, max_length=120)
    until_session_end: bool = True


def _audit_session_grant(user: CurrentUser, action: str, server_id: str, body: McpApproveSessionBody) -> None:
    """Hash-chain audit for a session-grant decision, in its own transaction."""
    from ..db.database import default_session_factory
    from ..services.audit import hash_chain

    with default_session_factory()() as session:
        hash_chain.append(
            session, actor=user.id, action=action, entity_type="mcp_session_grant",
            entity_id=f"{server_id}:{body.session_id}",
            detail={"server_id": server_id, "session_id": body.session_id, "tool_name": body.tool_name},
        )
        session.commit()


@router.post("/mcp/{server_id}/approve-session")
def approve_mcp_session(server_id: str, body: McpApproveSessionBody,
                        user: CurrentUser = Depends(current_user)) -> dict:
    """Grant writeish MCP tools for one chat session (not global permanent).

    Personal action: the grant is bound to the granting login (owner), expires
    (24 h for session grants, 1 h otherwise), and is hash-chain audited.
    """
    settings = get_settings()
    if not settings.mcp_client_enabled:
        raise HTTPException(status_code=503, detail="mcp_client_enabled is off")
    from ..services.mcp_session_grants import approve_session, revoke_session

    try:
        result = approve_session(
            body.session_id,
            server_id,
            tool_name=body.tool_name,
            until_session_end=body.until_session_end,
            owner_user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        _audit_session_grant(user, "mcp.session.granted", server_id, body)
    except Exception:
        # Fail-closed: a grant without its audit row must not stand.
        revoke_session(body.session_id, server_id, tool_name=body.tool_name,
                       owner_user_id=user.id, is_admin=True)
        raise HTTPException(status_code=500, detail="mcp session audit failed; grant revoked") from None
    return result


class McpRevokeSessionBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    tool_name: str | None = Field(default=None, max_length=120)


@router.post("/mcp/{server_id}/revoke-session")
def revoke_mcp_session(server_id: str, body: McpRevokeSessionBody,
                       user: CurrentUser = Depends(current_user)) -> dict:
    """Revoke a chat session's MCP write grant(s). Owner-or-admin only."""
    from ..services.mcp_session_grants import revoke_session

    try:
        result = revoke_session(
            body.session_id,
            server_id,
            tool_name=body.tool_name,
            owner_user_id=user.id,
            is_admin=user.principal.can("admin.connectors"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=403, detail=result.get("error", "not_authorized"))
    if result.get("revoked"):
        _audit_session_grant(user, "mcp.session.revoked", server_id,
                             McpApproveSessionBody(session_id=body.session_id, tool_name=body.tool_name))
    return result
