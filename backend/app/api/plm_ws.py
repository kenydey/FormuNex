"""WebSocket real-time compliance (stage 4 / SR-021: moved out of app/api/plm.py).

The WS handler authenticates itself before accept (see below) and never
persists anything: classification results are computed in-memory only.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from ._principal import CurrentUser
from .plm import ClassifyRequest, _redact_classification_response

ws_router = APIRouter()


def _session():
    from ..db.database import default_session_factory

    return default_session_factory()()


# --------------------------------------------------------------------------- WebSocket: real-time compliance




def _classify_ws_sync(payload: dict[str, Any]) -> dict[str, Any]:
    """Classification for the WebSocket path (runs in a worker thread so the event loop stays responsive).

    Same response structure as POST /api/plm/ghs/classify, via the cached
    lookup path the real-time editor needs to meet its 150 ms budget.
    """
    from ..services.plm.ghs import service

    req = ClassifyRequest(**payload)
    with _session() as session:
        result, _built = service.build_and_classify(
            session, [c.model_dump(exclude_none=True) for c in req.components],
            req.properties.model_dump(), mode=req.mode, include_transport=req.include_transport, cached=True)
        return service.to_response(session, result, cached=True)


@ws_router.websocket("/ws/formulations/{formulation_id}/compliance")
async def formulation_compliance_ws(websocket: WebSocket, formulation_id: str) -> None:
    """Real-time GB 30000 compliance over WebSocket (方案 §4.6 / §3.6).

    Protocol (JSON text frames):
      client → {"type": "classify", "components": [...], "properties": {...},
                "mode": "max", "include_transport": true}
        ``components`` omitted → classify the saved formulation version
        ``formulation_id`` (``"new"`` = unsaved editor: components required).
      client → {"type": "ping"} → server {"type": "pong"}
      server → {"type": "classification", ...same fields as POST /api/plm/ghs/classify...}
      server → {"type": "error", "detail": "..."} (the socket stays open)

    The socket never persists anything: classification results are computed
    in-memory only; saving still goes through the REST endpoints.

    Authentication (P0-1 fix): the handshake is authenticated *before*
    accept — a missing/invalid token closes the socket with 4401, a token
    without plm.document.write with 4403. Callers without
    formulation.read_exact receive a banded response (exact pct values are
    replaced by concentration bands); computation itself still uses the
    exact values.
    """
    from ..services.plm.ghs import service

    try:
        _user, can_read_exact = _ws_authenticate(websocket)
    except _WsAuthFailure as exc:
        await websocket.close(code=exc.code, reason=exc.detail)
        return
    await websocket.accept()
    while True:
        try:
            msg = await websocket.receive_json()
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001 - malformed frame: tell the client, keep the socket open
            try:
                await websocket.send_json({"type": "error", "detail": "消息必须是 JSON 对象"})
            except Exception:  # noqa: BLE001 - client went away mid-send
                return
            continue
        if not isinstance(msg, dict):
            await websocket.send_json({"type": "error", "detail": "消息必须是 JSON 对象"})
            continue
        mtype = msg.get("type", "classify")
        if mtype == "ping":
            await websocket.send_json({"type": "pong"})
            continue
        if mtype != "classify":
            await websocket.send_json({"type": "error", "detail": f"未知消息类型：{mtype}"})
            continue
        try:
            payload = {"components": msg.get("components"), "properties": msg.get("properties") or {},
                       "mode": msg.get("mode", "max"), "include_transport": msg.get("include_transport", True)}
            if payload["components"] is None:
                if formulation_id == "new":
                    raise ValueError("未保存的配方请提供组分（components）")
                with _session() as session:
                    try:
                        payload["components"] = service.formulation_version_items(session, formulation_id)
                    except KeyError:
                        raise ValueError("配方版本不存在") from None
                if not payload["components"]:
                    raise ValueError("该配方版本没有组分")
            response = await asyncio.to_thread(_classify_ws_sync, payload)
        except ValidationError as exc:
            await websocket.send_json({"type": "error", "detail": f"请求校验失败：{exc.errors()}"})
            continue
        except ValueError as exc:
            await websocket.send_json({"type": "error", "detail": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 - classification must never kill the socket
            await websocket.send_json({"type": "error", "detail": f"分类失败：{exc}"})
            continue
        if not can_read_exact:
            response = _redact_classification_response(response)
        await websocket.send_json({"type": "classification", **response})


class _WsAuthFailure(Exception):
    """Handshake rejection: close code 4401 (unauthenticated) / 4403 (forbidden) / 1011 (server error)."""

    def __init__(self, code: int, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _ws_token(websocket: WebSocket) -> str | None:
    """Bearer <redacted> or ?token= — browsers cannot set headers on a WS handshake."""
    query = websocket.query_params.get("token")
    if query and query.strip():
        return query.strip()
    auth = websocket.headers.get("authorization") or ""
    parts = auth.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


def _ws_authenticate(websocket: WebSocket) -> tuple[CurrentUser | None, bool]:
    """Authenticate the WS handshake; returns (user, can_read_exact).

    Parity rules (mirroring the REST path):
    - Auth disabled → open, exact values allowed (same as the middleware
      passing everything and _can_read_exact(None) == True in formulations).
    - Operator credential (FORMUNEX_API_TOKEN / multi-user owner token) →
      open, exact values allowed (the guard's service-key exemption).
    - Personal access token → must hold plm.document.write (the gate the
      guard applies to POST /api/plm/ghs/*); exact values only with
      formulation.read_exact.
    Raises _WsAuthFailure on rejection; the caller closes the socket.
    """
    from ..config import get_settings, multi_user_enabled
    from ..middleware.api_auth import _owner_of_token, _safe_eq, resolve_api_token
    from ..services.identity import accounts, tokens

    settings = get_settings()
    if not settings.api_auth_enabled:
        return None, True
    token = _ws_token(websocket)
    if not token:
        raise _WsAuthFailure(4401, "需要登录（WebSocket 握手需携带 ?token= 或 Authorization Bearer）")
    if multi_user_enabled() and _owner_of_token(token) is not None:
        return None, True
    configured = resolve_api_token(settings)
    if configured is not None and _safe_eq(token, configured):
        return None, True
    if not tokens.looks_like_access_token(token):
        raise _WsAuthFailure(4401, "无效的访问令牌")
    with _session() as session:
        try:
            user_row, sid = accounts.active_user_for_token(session, token)
        except accounts.AuthError as exc:
            raise _WsAuthFailure(4401, str(exc)) from None
        except tokens.SigningKeyMissing as exc:
            raise _WsAuthFailure(1011, str(exc)) from None
        from ..services.identity.rbac import load_principal

        user = CurrentUser(id=user_row.id, username=user_row.username, display_name=user_row.display_name,
                           session_id=sid, principal=load_principal(session, user_row.id))
    if not user.principal.can("plm.document.write"):
        raise _WsAuthFailure(4403, "缺少权限 plm.document.write")
    return user, user.principal.can("formulation.read_exact")
