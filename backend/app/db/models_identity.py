"""Identity & RBAC tables (alembic 0051).

FormuNex replaces the single shared bearer token with real accounts: users,
role assignments and project membership. The bearer token survives as a
*service account* API key, so scripts and integrations keep working.

Roles are rows (not an enum) so an administrator can add custom roles; the six
built-in roles are seeded by migration 0051 and flagged ``builtin`` so they
cannot be deleted from the UI.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, _utcnow


class UserRow(Base):
    """A person (or service account) that can sign in and sign documents."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    # argon2id hash. NULL = cannot log in with a password (legacy owner rows
    # imported by 0051). LDAP / OIDC are NOT implemented (see 开发方案附录 B D-6):
    # no user authenticates through them today.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # local | legacy_token | service are in use. ldap | oidc are reserved enum
    # values only — no LDAP/OIDC implementation exists (see D-6).
    auth_provider: Mapped[str] = mapped_column(String(16), default="local", index=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class RoleRow(Base):
    """A named permission set. ``permissions`` is a list of permission codes."""

    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name_zh: Mapped[str] = mapped_column(String(80), default="")
    name_en: Mapped[str] = mapped_column(String(80), default="")
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    builtin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class UserRoleRow(Base):
    """Global (organisation-wide) role assignment."""

    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_code: Mapped[str] = mapped_column(
        ForeignKey("roles.code", ondelete="CASCADE"), primary_key=True
    )
    granted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class ProjectMemberRow(Base):
    """Project-scoped role. Exact formulation compositions are trade secrets and
    are only visible to members of the project that owns them."""

    __tablename__ = "project_members"

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role_code: Mapped[str] = mapped_column(ForeignKey("roles.code"), index=True)
    added_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class UserSessionRow(Base):
    """A refresh-token session (alembic 0060).

    Access tokens are short-lived signed JWTs and are never stored; the refresh
    token is an opaque random string of which only the SHA-256 is kept, so a
    leaked database cannot be replayed. Logout / password change revoke rows.
    """

    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
