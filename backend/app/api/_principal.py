"""The signed-in user behind a request (FastAPI dependency).

Endpoints that record *who* did something — sign-off, e-signatures, waivers,
account administration — depend on :func:`current_user`; a service API key
authenticates the caller to the platform but is not a person and cannot sign.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request

from ..services.identity.rbac import Principal


@dataclass
class CurrentUser:
    id: str
    username: str
    display_name: str
    session_id: str
    principal: Principal

    def require(self, permission: str, project_id: str | None = None) -> None:
        if not self.principal.can(permission, project_id):
            raise HTTPException(status_code=403, detail=f"缺少权限 {permission}")


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    parts = auth.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


def current_user(request: Request) -> CurrentUser:
    from ..db.database import default_session_factory
    from ..services.identity import accounts, tokens
    from ..services.identity.rbac import load_principal

    token = _bearer(request)
    if not token or not tokens.looks_like_access_token(token):
        raise HTTPException(status_code=401, detail="需要登录账号（服务 API Key 不能代表个人操作）",
                            headers={"WWW-Authenticate": "Bearer"})
    with default_session_factory()() as session:
        try:
            user, sid = accounts.active_user_for_token(session, token)
        except accounts.AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}) from None
        except tokens.SigningKeyMissing as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        return CurrentUser(id=user.id, username=user.username, display_name=user.display_name, session_id=sid,
                           principal=load_principal(session, user.id))


def optional_user(request: Request) -> CurrentUser | None:
    """The signed-in user when the request carries an access token, else None (service API key / dev mode)."""
    token = _bearer(request)
    if not token:
        return None
    from ..services.identity import tokens

    if not tokens.looks_like_access_token(token):
        return None
    return current_user(request)
