"""Stage 2 / SR-010 (D-1): set_roles is a forced boundary, not a process assumption."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
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
