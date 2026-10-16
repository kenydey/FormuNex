"""Accounts & sessions (方案 §4.4): sign in, refresh, sign out, users and roles."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ._principal import CurrentUser, current_user

router = APIRouter(prefix="/identity", tags=["identity"])

REFRESH_COOKIE = "fnx_refresh"
_COOKIE_PATH = "/api/identity"


def _session():
    from ..db.database import default_session_factory

    return default_session_factory()()


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class PasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)
    display_name: str = ""
    email: str | None = None
    roles: list[str] = Field(default_factory=list)


class RolesIn(BaseModel):
    roles: list[str]


def _token_response(response: Response, request: Request, result, session) -> dict[str, Any]:
    from ..config import get_settings
    from ..services.identity.accounts import user_dict

    response.set_cookie(
        REFRESH_COOKIE, result.refresh_token, httponly=True, samesite="strict", path=_COOKIE_PATH,
        secure=request.url.scheme == "https", max_age=get_settings().auth_refresh_ttl_days * 86400,
    )
    # The refresh token is also returned in the body for the desktop app (Tauri keeps it in the OS
    # keychain); browsers use the httpOnly cookie and should ignore it.
    return {"access_token": result.access_token, "token_type": "bearer", "expires_in": result.expires_in,
            "refresh_token": result.refresh_token, "user": user_dict(session, result.user)}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    from ..services.identity import accounts, tokens

    with _session() as session:
        try:
            result = accounts.login(session, body.username, body.password,
                                    user_agent=request.headers.get("user-agent", ""))
        except accounts.AuthError as exc:
            session.commit()  # keep the failure count / lock-out and its audit record
            raise HTTPException(status_code=401, detail=str(exc)) from None
        except tokens.SigningKeyMissing as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        out = _token_response(response, request, result, session)
        session.commit()
    return out


@router.post("/refresh")
def refresh(request: Request, response: Response, body: dict[str, Any] | None = None) -> dict[str, Any]:
    from ..services.identity import accounts, tokens

    token = request.cookies.get(REFRESH_COOKIE) or (body or {}).get("refresh_token")
    if not token:
        raise HTTPException(status_code=401, detail="缺少刷新令牌")
    with _session() as session:
        try:
            result = accounts.refresh(session, str(token))
        except accounts.AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from None
        except tokens.SigningKeyMissing as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        out = _token_response(response, request, result, session)
        session.commit()
    return out


@router.post("/logout")
def logout(request: Request, response: Response, body: dict[str, Any] | None = None) -> dict[str, Any]:
    from sqlalchemy import select

    from ..db.models_identity import UserSessionRow
    from ..services.identity import accounts, tokens

    token = request.cookies.get(REFRESH_COOKIE) or (body or {}).get("refresh_token")
    if token:
        with _session() as session:
            row = session.execute(select(UserSessionRow).where(
                UserSessionRow.token_hash == tokens.hash_refresh_token(str(token)))).scalar_one_or_none()
            if row is not None:
                accounts.logout(session, row.id)
                session.commit()
    response.delete_cookie(REFRESH_COOKIE, path=_COOKIE_PATH)
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..db.models_identity import UserRow
    from ..services.identity.accounts import user_dict

    with _session() as session:
        out = user_dict(session, session.get(UserRow, user.id))
    out["permissions"] = sorted(user.principal.global_permissions)
    out["project_permissions"] = {k: sorted(v) for k, v in user.principal.project_permissions.items()}
    return out


@router.post("/password")
def change_password(body: PasswordIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.identity import accounts

    with _session() as session:
        try:
            accounts.change_password(session, user.id, body.old_password, body.new_password)
        except accounts.AuthError as exc:
            session.commit()
            raise HTTPException(status_code=401, detail=str(exc)) from None
        except accounts.AccountError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        session.commit()
    return {"ok": True, "detail": "密码已修改，其他会话已全部退出"}


@router.get("/roles")
def list_roles(user: CurrentUser = Depends(current_user)) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from ..db.models_identity import RoleRow

    with _session() as session:
        return [{"code": r.code, "name_zh": r.name_zh, "name_en": r.name_en, "permissions": sorted(r.permissions or []),
                 "builtin": r.builtin} for r in session.execute(select(RoleRow).order_by(RoleRow.code)).scalars()]


@router.get("/users")
def list_users(user: CurrentUser = Depends(current_user)) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from ..db.models_identity import UserRow
    from ..services.identity.accounts import user_dict

    user.require("admin.users")
    with _session() as session:
        return [user_dict(session, u) for u in session.execute(select(UserRow).order_by(UserRow.username)).scalars()]


@router.post("/users")
def create_user(body: UserIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.identity import accounts

    user.require("admin.users")
    with _session() as session:
        try:
            row = accounts.create_user(session, username=body.username, password=body.password,
                                       display_name=body.display_name, email=body.email, roles=body.roles,
                                       actor=user.id)
        except accounts.AccountError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        out = accounts.user_dict(session, row)
        session.commit()
    return out


@router.put("/users/{user_id}/roles")
def set_user_roles(user_id: str, body: RolesIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.identity import accounts

    user.require("admin.users")
    with _session() as session:
        try:
            roles = accounts.set_roles(session, user_id, body.roles, actor=user.id)
        except accounts.AccountError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        session.commit()
    return {"user_id": user_id, "roles": roles}
