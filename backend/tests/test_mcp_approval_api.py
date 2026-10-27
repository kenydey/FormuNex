"""Tests for the MCP approval REST API (W3-11), W0 auth semantics.

Phase 2D (2-24): the service reads the main database through
``default_session_factory``, so the API test points the real app at a
temporary SQLite database via ``FORMUNEX_DB_URL`` (same pattern as the
PLM lifecycle e2e ``stack`` fixture) instead of a standalone approval DB.

W0: decide / list are personal actions — they require ``current_user``,
non-admins only see and decide requests attributable to their own projects,
and ``scope=global`` is restricted to ``admin.connectors``.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import mcp_approval

PASSWORD = "W0test!123"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'mcp_approval_api.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_KG_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models_identity import ProjectMemberRow
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        users = {}
        for name, roles in (("admin", ["sys_admin"]), ("member", ["rd_engineer"]),
                            ("stranger", ["lab_technician"])):
            users[name] = accounts.create_user(s, username=name, password=PASSWORD, roles=roles)
        s.add(ProjectMemberRow(project_id="p1", user_id=users["member"].id, role_code="rd_engineer"))
        s.commit()
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    tokens = {}
    for name in ("admin", "member", "stranger"):
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield {"client": client, "h": tokens}
    get_settings.cache_clear()


def _seed(project_id=None, session_id=None):
    res = mcp_approval.evaluate_call("srv1", "tool_a", session_id=session_id,
                                     project_id=project_id, now=time.time())
    assert res["verdict"] == "deny" and res["reason"] == "approval_required"
    return res["request_id"]


def test_pending_requires_login(env):
    r = env["client"].get("/api/mcp/approvals/pending")
    assert r.status_code == 401


def test_pending_filtered_by_attribution(env):
    _seed(project_id="p1")
    _seed()
    c, h = env["client"], env["h"]
    assert c.get("/api/mcp/approvals/pending", headers=h["member"]).json()["total"] == 1
    assert c.get("/api/mcp/approvals/pending", headers=h["stranger"]).json()["total"] == 0
    assert c.get("/api/mcp/approvals/pending", headers=h["admin"]).json()["total"] == 2


def test_decide_member_and_stranger(env):
    rid = _seed(project_id="p1")
    c, h = env["client"], env["h"]
    r = c.post(f"/api/mcp/approvals/{rid}/decide", json={"decision": "deny"}, headers=h["stranger"])
    assert r.status_code == 403
    r = c.post(f"/api/mcp/approvals/{rid}/decide", json={"decision": "deny"}, headers=h["member"])
    assert r.status_code == 200 and r.json()["ok"] is True
    # Second decide on the same request -> 404 (not pending anymore).
    r2 = c.post(f"/api/mcp/approvals/{rid}/decide", json={"decision": "deny"}, headers=h["member"])
    assert r2.status_code == 404


def test_decide_global_requires_admin(env):
    rid = _seed(project_id="p1")
    c, h = env["client"], env["h"]
    r = c.post(f"/api/mcp/approvals/{rid}/decide",
               json={"decision": "allow", "scope": "global"}, headers=h["member"])
    assert r.status_code == 403
    rid2 = _seed()
    r = c.post(f"/api/mcp/approvals/{rid2}/decide",
               json={"decision": "allow", "scope": "global"}, headers=h["admin"])
    assert r.status_code == 200 and r.json()["ok"] is True


def test_decide_expired_410(env):
    res = mcp_approval.evaluate_call("srv1", "tool_a", now=time.time() - 301.0)
    rid = res["request_id"]
    r = env["client"].post(f"/api/mcp/approvals/{rid}/decide",
                           json={"decision": "allow"}, headers=env["h"]["admin"])
    assert r.status_code == 410


def test_decide_missing_request_404(env):
    r = env["client"].post("/api/mcp/approvals/does-not-exist/decide",
                           json={"decision": "deny"}, headers=env["h"]["admin"])
    assert r.status_code == 404


def test_decide_invalid_payload_422(env):
    c, h = env["client"], env["h"]
    r = c.post("/api/mcp/approvals/does-not-exist/decide",
               json={"decision": "maybe", "scope": "once"}, headers=h["admin"])
    assert r.status_code == 422
    r = c.post("/api/mcp/approvals/does-not-exist/decide",
               json={"decision": "allow", "scope": "forever"}, headers=h["admin"])
    assert r.status_code == 422


def test_decide_requires_login(env):
    rid = _seed(project_id="p1")
    r = env["client"].post(f"/api/mcp/approvals/{rid}/decide", json={"decision": "deny"})
    assert r.status_code == 401
