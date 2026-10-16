"""Accounts: create users, sign in, refresh / revoke sessions, re-authenticate for e-signatures.

Every security-relevant event (login success / failure / lock-out, password
change, role change, signature re-authentication failure) goes to the
hash-chained audit log in the caller's transaction.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ...clock import utcnow
from ...config import get_settings
from ...db.models_identity import RoleRow, UserRoleRow, UserRow, UserSessionRow
from ..audit import hash_chain
from . import passwords, tokens


class AuthError(PermissionError):
    """Wrong credentials, locked or inactive account, revoked / expired session."""


class AccountError(ValueError):
    """Invalid account data (duplicate username, unknown role, weak password)."""


@dataclass
class LoginResult:
    user: UserRow
    access_token: str
    expires_in: int
    refresh_token: str
    session_id: str


def user_dict(session: Session, user: UserRow) -> dict:
    roles = session.execute(select(UserRoleRow.role_code).where(UserRoleRow.user_id == user.id)).scalars().all()
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "email": user.email,
            "auth_provider": user.auth_provider, "is_active": user.is_active, "roles": sorted(roles),
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None}


def _check_roles(session: Session, roles: list[str]) -> None:
    known = set(session.execute(select(RoleRow.code)).scalars())
    unknown = sorted(set(roles) - known)
    if unknown:
        raise AccountError(f"未知角色：{', '.join(unknown)}")


def create_user(session: Session, *, username: str, password: str | None, display_name: str = "",
                email: str | None = None, roles: list[str] | None = None, actor: str = "system") -> UserRow:
    username = username.strip()
    if not username:
        raise AccountError("用户名不能为空")
    if session.execute(select(UserRow.id).where(UserRow.username == username)).first():
        raise AccountError(f"用户名 {username} 已存在")
    roles = list(dict.fromkeys(roles or []))
    _check_roles(session, roles)
    try:
        pw_hash = passwords.hash_password(password) if password else None
    except passwords.WeakPassword as exc:
        raise AccountError(str(exc)) from exc
    user = UserRow(id=str(uuid.uuid4()), username=username, display_name=display_name or username, email=email,
                   password_hash=pw_hash, auth_provider="local")
    session.add(user)
    session.flush()
    for code in roles:
        session.add(UserRoleRow(user_id=user.id, role_code=code, granted_by=None if actor == "system" else actor))
    session.flush()
    hash_chain.append(session, actor=actor, action="user.create", entity_type="user", entity_id=user.id,
                      after={"username": username, "roles": roles})
    return user


def set_roles(session: Session, user_id: str, roles: list[str], *, actor: str) -> list[str]:
    user = session.get(UserRow, user_id)
    if user is None:
        raise AccountError("用户不存在")
    roles = sorted(set(roles))
    _check_roles(session, roles)
    before = sorted(session.execute(select(UserRoleRow.role_code).where(UserRoleRow.user_id == user_id)).scalars())
    for row in session.execute(select(UserRoleRow).where(UserRoleRow.user_id == user_id)).scalars():
        session.delete(row)
    session.flush()
    for code in roles:
        session.add(UserRoleRow(user_id=user_id, role_code=code, granted_by=actor))
    session.flush()
    hash_chain.append(session, actor=actor, action="user.roles", entity_type="user", entity_id=user_id,
                      before={"roles": before}, after={"roles": roles})
    return roles


def _check_password(session: Session, user: UserRow | None, password: str, *, action: str, now: datetime) -> UserRow:
    """Verify *password* with lock-out bookkeeping. Raises :class:`AuthError` (one message for every
    failure, so the response does not reveal whether the username exists)."""
    generic = "用户名或密码错误"
    if user is None:
        passwords.verify_password(None, password)
        raise AuthError(generic)
    if not user.is_active:
        raise AuthError("账号已停用")
    if passwords.is_locked(user.locked_until, now):
        raise AuthError("登录失败次数过多，账号已临时锁定")
    if not passwords.verify_password(user.password_hash, password):
        user.failed_logins, user.locked_until = passwords.register_failure(user.failed_logins or 0, now)
        hash_chain.append(session, actor=user.id, action=f"{action}.failed", entity_type="user", entity_id=user.id,
                          detail={"locked": user.locked_until is not None})
        raise AuthError(generic)
    user.failed_logins = 0
    user.locked_until = None
    if user.password_hash and passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.rehash(password)
    return user


def _open_session(session: Session, user: UserRow, user_agent: str, now: datetime) -> tuple[str, str]:
    refresh, digest = tokens.new_refresh_token()
    sid = str(uuid.uuid4())
    session.add(UserSessionRow(id=sid, user_id=user.id, token_hash=digest, user_agent=(user_agent or "")[:300],
                               created_at=now, expires_at=now + timedelta(days=get_settings().auth_refresh_ttl_days)))
    return sid, refresh


def login(session: Session, username: str, password: str, *, user_agent: str = "") -> LoginResult:
    now = utcnow()
    user = session.execute(select(UserRow).where(UserRow.username == username.strip())).scalar_one_or_none()
    user = _check_password(session, user, password, action="auth.login", now=now)
    user.last_login_at = now
    sid, refresh = _open_session(session, user, user_agent, now)
    access, ttl = tokens.issue_access_token(user.id, sid)
    hash_chain.append(session, actor=user.id, action="auth.login", entity_type="user", entity_id=user.id,
                      detail={"session_id": sid})
    session.flush()
    return LoginResult(user, access, ttl, refresh, sid)


def refresh(session: Session, refresh_token: str) -> LoginResult:
    """Rotate: the presented refresh token is revoked and a new one issued."""
    now = utcnow()
    row = session.execute(
        select(UserSessionRow).where(UserSessionRow.token_hash == tokens.hash_refresh_token(refresh_token))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise AuthError("会话已失效，请重新登录")
    user = session.get(UserRow, row.user_id)
    if user is None or not user.is_active:
        raise AuthError("账号已停用")
    row.revoked_at = now
    sid, new_refresh = _open_session(session, user, row.user_agent, now)
    access, ttl = tokens.issue_access_token(user.id, sid)
    session.flush()
    return LoginResult(user, access, ttl, new_refresh, sid)


def logout(session: Session, session_id: str) -> None:
    row = session.get(UserSessionRow, session_id)
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()
        hash_chain.append(session, actor=row.user_id, action="auth.logout", entity_type="user", entity_id=row.user_id,
                          detail={"session_id": session_id})


def change_password(session: Session, user_id: str, old: str, new: str) -> None:
    now = utcnow()
    user = _check_password(session, session.get(UserRow, user_id), old, action="auth.password", now=now)
    try:
        user.password_hash = passwords.hash_password(new)
    except passwords.WeakPassword as exc:
        raise AccountError(str(exc)) from exc
    session.execute(update(UserSessionRow).where(UserSessionRow.user_id == user_id,
                                                 UserSessionRow.revoked_at.is_(None)).values(revoked_at=now))
    hash_chain.append(session, actor=user_id, action="auth.password", entity_type="user", entity_id=user_id)


def active_user_for_token(session: Session, access_token: str) -> tuple[UserRow, str]:
    """Resolve a bearer access token to ``(user, session_id)``; a revoked session invalidates its access
    tokens immediately rather than after their 15 minutes."""
    try:
        claims = tokens.decode_access_token(access_token)
    except tokens.InvalidToken as exc:
        raise AuthError(str(exc)) from exc
    row = session.get(UserSessionRow, claims.get("sid") or "")
    if row is None or row.revoked_at is not None or row.user_id != claims["sub"]:
        raise AuthError("会话已失效，请重新登录")
    user = session.get(UserRow, claims["sub"])
    if user is None or not user.is_active:
        raise AuthError("账号已停用")
    return user, row.id


def reauthenticate(session: Session, user_id: str, password: str) -> UserRow:
    """Electronic-signature re-authentication (plan §4.4): the signer types the password again."""
    return _check_password(session, session.get(UserRow, user_id), password, action="esign.reauth", now=utcnow())
