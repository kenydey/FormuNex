"""Identity (RBAC, passwords) and the hash-chained audit log."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.db.models import Base
from app.db.models_identity import ProjectMemberRow, RoleRow, UserRoleRow, UserRow
from app.db.models_plm import AuditLogRow
from app.services.audit import hash_chain
from app.services.identity import passwords, rbac

VERSIONS = Path(__file__).resolve().parents[1] / "app" / "db" / "alembic" / "versions"


# --------------------------------------------------------------------------- RBAC


def test_migration_seed_matches_live_role_definitions():
    """0051 carries a frozen copy of the roles; it must agree with rbac.BUILTIN_ROLES at this revision."""
    spec = importlib.util.spec_from_file_location("m0051", VERSIONS / "0051_identity_rbac.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    frozen = {code: (zh, en, frozenset(perms)) for code, zh, en, perms in mod._ROLES}
    live = {r.code: (r.name_zh, r.name_en, r.permissions) for r in rbac.BUILTIN_ROLES}
    assert frozen == live


def test_every_builtin_permission_is_catalogued():
    for role in rbac.BUILTIN_ROLES:
        assert role.permissions <= rbac.PERMISSIONS.keys()
    with pytest.raises(ValueError):
        rbac._role("x", "x", "x", ["no.such"])


def test_release_signoff_roles_can_sign():
    for code in rbac.RELEASE_SIGNOFF_ROLES:
        assert "release.sign" in rbac.BUILTIN_BY_CODE[code].permissions
    assert "release.publish" in rbac.BUILTIN_BY_CODE["release_manager"].permissions


def test_trade_secret_permission_is_separate():
    tech = rbac.build_principal("u1", ["lab_technician"], {})
    assert tech.can("formulation.read")
    assert not tech.can("formulation.read_exact")


def test_project_roles_only_apply_in_their_project():
    p = rbac.build_principal("u1", [], {"proj-a": "rd_engineer"})
    assert p.can("formulation.write", "proj-a")
    assert not p.can("formulation.write", "proj-b")
    assert not p.can("formulation.write")
    with pytest.raises(rbac.PermissionDenied, match="formulation.write in project proj-b"):
        rbac.require(p, "formulation.write", "proj-b")
    with pytest.raises(KeyError):
        p.can("formulation.typo")


def test_global_role_applies_everywhere():
    p = rbac.build_principal("u1", ["ehs_regulatory"], {})
    assert p.can("regulatory.review", "any-project")


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'id.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def test_load_principal_honours_custom_roles(session):
    rbac.sync_builtin_roles(session)
    session.add(RoleRow(code="auditor", name_zh="审计员", name_en="Auditor", permissions=["audit.read"]))
    session.add(UserRow(id="u1", username="li", display_name="李工"))
    session.flush()
    session.add(UserRoleRow(user_id="u1", role_code="auditor"))
    session.add(ProjectMemberRow(project_id="p1", user_id="u1", role_code="quality"))
    session.commit()
    p = rbac.load_principal(session, "u1")
    assert p.can("audit.read")
    assert p.can("coa.sign", "p1") and not p.can("coa.sign", "p2")


def test_sync_builtin_roles_repairs_edited_permissions(session):
    assert len(rbac.sync_builtin_roles(session)) == len(rbac.BUILTIN_ROLES)
    session.commit()
    row = session.get(RoleRow, "quality")
    row.permissions = ["knowledge.read"]
    session.commit()
    assert rbac.sync_builtin_roles(session) == ["quality"]
    assert sorted(session.get(RoleRow, "quality").permissions) == sorted(rbac.BUILTIN_BY_CODE["quality"].permissions)
    assert rbac.sync_builtin_roles(session) == []


# --------------------------------------------------------------------------- passwords


def test_password_hash_roundtrip_and_policy():
    h = passwords.hash_password("Formula-2026!")
    assert h.startswith("$argon2id$")
    assert passwords.verify_password(h, "Formula-2026!")
    assert not passwords.verify_password(h, "formula-2026!")
    assert not passwords.verify_password(None, "x")
    assert not passwords.verify_password("not-a-hash", "x")
    for weak in ("Short1!", "alllowercaseletters", "ALLUPPER12345"):
        with pytest.raises(passwords.WeakPassword):
            passwords.hash_password(weak)


def test_lockout_after_repeated_failures():
    now = datetime(2026, 10, 16, 9, 0)
    failed, locked = 0, None
    for _ in range(passwords.MAX_FAILED_LOGINS - 1):
        failed, locked = passwords.register_failure(failed, now)
        assert locked is None
    failed, locked = passwords.register_failure(failed, now)
    assert failed == 0 and locked == now + passwords.LOCKOUT
    assert passwords.is_locked(locked, now + timedelta(minutes=1))
    assert not passwords.is_locked(locked, now + passwords.LOCKOUT + timedelta(seconds=1))


# --------------------------------------------------------------------------- audit chain


def _append_three(session):
    hash_chain.append(session, actor="u1", action="create", entity_type="product", entity_id="p1",
                      after={"code": "FX-001"})
    hash_chain.append(session, actor="u2", action="update", entity_type="product", entity_id="p1",
                      before={"code": "FX-001"}, after={"code": "FX-001A"}, detail={"reason": "改名"})
    hash_chain.append(session, actor="u3", action="sign", entity_type="release_package", entity_id="r1",
                      detail={"meaning": "approve"})
    session.commit()


def test_chain_links_and_verifies(session):
    _append_three(session)
    rows = session.execute(select(AuditLogRow).order_by(AuditLogRow.seq)).scalars().all()
    assert rows[0].prev_hash == hash_chain.GENESIS
    assert [r.prev_hash for r in rows[1:]] == [r.hash for r in rows[:-1]]
    assert rows[1].before_hash == hash_chain.content_hash({"code": "FX-001"})
    assert hash_chain.verify_chain(session) == hash_chain.ChainCheck(ok=True, rows=3)


def test_tampering_is_detected_when_trigger_is_bypassed(session):
    _append_three(session)
    # Someone with DDL rights could drop the trigger; the chain still exposes the edit.
    session.execute(text("DROP TRIGGER IF EXISTS audit_log_no_update"))
    session.execute(text("UPDATE audit_log SET actor = 'mallory' WHERE seq = 2"))
    session.commit()
    check = hash_chain.verify_chain(session)
    assert not check.ok and check.first_bad_seq == 2 and check.rows == 1


def test_make_engine_installs_append_only_guard(tmp_path, monkeypatch):
    from app.db.database import make_engine

    engine = make_engine(f"sqlite:///{tmp_path / 'guard.db'}")
    try:
        with Session(engine) as s:
            _append_three(s)
            with pytest.raises(Exception, match="append-only"):
                s.execute(text("DELETE FROM audit_log"))
                s.commit()
    finally:
        engine.dispose()
