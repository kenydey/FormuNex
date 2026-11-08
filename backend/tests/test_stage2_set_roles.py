"""Stage 2 / SR-010 (D-1): set_roles is a forced boundary, not a process assumption."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.db.models import Base
from app.db.models_identity import RoleRow, UserRoleRow, UserRow
from app.services.identity import accounts


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'roles.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for code, perms in (("role_a", ["perm.x", "perm.y"]), ("role_b", ["perm.y", "perm.z"]),
                            ("role_empty", [])):
            s.add(RoleRow(code=code, name_zh=code, name_en=code, permissions=perms))
        for uid in ("admin", "target"):
            s.add(UserRow(id=uid, username=uid, display_name=uid))
        s.add(UserRoleRow(user_id="admin", role_code="role_a", granted_by="system"))
        s.commit()
        yield s
    engine.dispose()


def test_granter_cannot_grant_permissions_they_do_not_hold(session):
    with pytest.raises(accounts.AccountError, match="未持有"):
        accounts.set_roles(session, "target", ["role_b"], actor="admin")


def test_granter_can_grant_a_subset_they_hold(session):
    assert accounts.set_roles(session, "target", ["role_a"], actor="admin") == ["role_a"]
    assert accounts.set_roles(session, "target", [], actor="admin") == []


def test_granter_cannot_modify_own_roles(session):
    with pytest.raises(accounts.AccountError, match="自身"):
        accounts.set_roles(session, "admin", ["role_a"], actor="admin")


def test_system_bootstrap_actor_keeps_old_behaviour(session):
    # No UserRow for "system": the subset check only applies to a real
    # signed-in granter, so bootstrap/admin seeding is unaffected.
    assert accounts.set_roles(session, "target", ["role_b"], actor="system") == ["role_b"]


def test_create_user_cannot_mint_permissions_granter_does_not_hold(session):
    # P0 fix (2026-11-04 deep review): create_user used to skip the D-1
    # subset check, so an admin.users holder could mint a puppet account
    # carrying permissions they do not hold (e.g. plm.document.write).
    with pytest.raises(accounts.AccountError, match="未持有"):
        accounts.create_user(session, username="puppet", password="Str0ng!Pass123",
                             roles=["role_b"], actor="admin")
    # Fail-fast: no row was written.
    assert session.execute(select(UserRow.id).where(UserRow.username == "puppet")).first() is None


def test_create_user_subset_ok_and_bootstrap_unaffected(session):
    user = accounts.create_user(session, username="ok", password="Str0ng!Pass123",
                                roles=["role_a"], actor="admin")
    assert user.username == "ok"
    boot = accounts.create_user(session, username="boot", password="Str0ng!Pass123",
                                roles=["role_b"], actor="system")
    assert boot.username == "boot"
