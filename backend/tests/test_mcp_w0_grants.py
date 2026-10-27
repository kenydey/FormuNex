"""W0 (2026-10-27): MCP session grants — expiry, owner binding, revoke,
startup purge of legacy permanent grants, hash-chain audit on approve/revoke."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import mcp_session_grants as grants

PASSWORD = "W0test!123"


@pytest.fixture()
def tmp_grants(tmp_path, monkeypatch):
    monkeypatch.setattr(grants, "_persist_path", lambda: tmp_path / "mcp_session_grants.json")
    grants._GRANTS.clear()
    grants._loaded = True
    yield tmp_path
    grants._GRANTS.clear()
    grants._loaded = False


def test_session_grant_has_finite_expiry_and_owner(tmp_grants):
    before = time.time()
    grants.approve_session("sess-1", "fs", tool_name="write_file", owner_user_id="u1")
    assert grants.is_granted("sess-1", "fs", "write_file", owner_user_id="u1") is True
    entry = grants._GRANTS["sess-1"]["fs"]["write_file"]
    assert before + 3600 < entry["expires"] <= before + 24 * 3600 + 60
    assert entry["owner"] == "u1"
    # Short grants keep the 1 h expiry.
    grants.approve_session("sess-1", "fs", tool_name="other", until_session_end=False, owner_user_id="u1")
    short = grants._GRANTS["sess-1"]["fs"]["other"]
    assert before + 3500 < short["expires"] <= before + 3600 + 60


def test_owner_binding_enforced_when_caller_supplies_owner(tmp_grants):
    grants.approve_session("sess-1", "fs", tool_name="write_file", owner_user_id="u1")
    assert grants.is_granted("sess-1", "fs", "write_file", owner_user_id="u2") is False
    assert grants.is_granted("sess-1", "fs", "write_file", owner_user_id="u1") is True


def test_revoke_owner_stranger_admin(tmp_grants):
    grants.approve_session("sess-1", "fs", tool_name="write_file", owner_user_id="u1")
    r = grants.revoke_session("sess-1", "fs", tool_name="write_file", owner_user_id="u2")
    assert r == {"ok": False, "error": "not_authorized", "revoked": 0}
    assert grants.is_granted("sess-1", "fs", "write_file", owner_user_id="u1") is True
    r = grants.revoke_session("sess-1", "fs", tool_name="write_file", owner_user_id="u1")
    assert r == {"ok": True, "revoked": 1}
    assert grants.is_granted("sess-1", "fs", "write_file", owner_user_id="u1") is False
    # Revoking a nonexistent grant is an idempotent no-op, not an error.
    r = grants.revoke_session("sess-1", "fs", tool_name="write_file", owner_user_id="u1")
    assert r == {"ok": True, "revoked": 0}
    # Admin may revoke anyone's grant.
    grants.approve_session("sess-1", "fs", tool_name="write_file", owner_user_id="u1")
    r = grants.revoke_session("sess-1", "fs", tool_name="write_file", is_admin=True)
    assert r == {"ok": True, "revoked": 1}


def test_startup_purge_drops_legacy_permanent_and_expired(tmp_grants, tmp_path):
    path = tmp_path / "mcp_session_grants.json"
    path.write_text(json.dumps({
        "old-sess": {"fs": {"write_file": None}},  # pre-W0 permanent grant
        "dead-sess": {"fs": {"write_file": {"expires": time.time() - 10, "owner": "u1", "created": 0}}},
        "live-sess": {"fs": {"write_file": {"expires": time.time() + 3600, "owner": "u1", "created": 0}}},
    }), encoding="utf-8")
    grants._GRANTS.clear()
    grants._loaded = False
    grants._ensure_loaded()
    assert "old-sess" not in grants._GRANTS
    assert "dead-sess" not in grants._GRANTS
    assert grants.is_granted("live-sess", "fs", "write_file", owner_user_id="u1") is True


# ---- API layer: approve binds the login, revoke is owner-or-admin --------


@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'mcp_grants_api.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_KG_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_MCP_CLIENT_ENABLED", "true")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        for name, roles in (("admin", ["sys_admin"]), ("owner", ["rd_engineer"]),
                            ("stranger", ["lab_technician"])):
            accounts.create_user(s, username=name, password=PASSWORD, roles=roles)
        s.commit()
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    tokens = {}
    for name in ("admin", "owner", "stranger"):
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield {"client": client, "h": tokens}
    get_settings.cache_clear()


def test_api_approve_requires_login_and_revoke_owner_admin(api_env, tmp_path, monkeypatch):
    monkeypatch.setattr(grants, "_persist_path", lambda: tmp_path / "mcp_session_grants.json")
    grants._GRANTS.clear()
    grants._loaded = True
    c, h = api_env["client"], api_env["h"]
    body = {"session_id": "chat-9", "tool_name": "write_file"}
    r = c.post("/api/connectors/mcp/fs/approve-session", json=body)
    assert r.status_code == 401
    r = c.post("/api/connectors/mcp/fs/approve-session", json=body, headers=h["owner"])
    assert r.status_code == 200 and r.json()["ok"] is True
    assert grants.is_granted("chat-9", "fs", "write_file", owner_user_id=None) is True
    # Hash-chain audit for the grant.
    from app.db.database import default_session_factory
    from app.db.models_plm import AuditLogRow

    with default_session_factory()() as s:
        rows = s.query(AuditLogRow).filter(AuditLogRow.action == "mcp.session.granted").all()
        assert len(rows) == 1 and rows[0].detail["session_id"] == "chat-9"
    # Stranger cannot revoke; owner and admin can.
    r = c.post("/api/connectors/mcp/fs/revoke-session", json=body, headers=h["stranger"])
    assert r.status_code == 403
    assert grants.is_granted("chat-9", "fs", "write_file", owner_user_id=None) is True
    r = c.post("/api/connectors/mcp/fs/revoke-session", json=body, headers=h["owner"])
    assert r.status_code == 200 and r.json()["revoked"] == 1
    assert grants.is_granted("chat-9", "fs", "write_file", owner_user_id=None) is False
