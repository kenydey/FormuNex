"""Stage 0 / SR-006: chat session ownership + MCP grant owner binding."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

PASSWORD = "Aa12345678"
USERS = {
    "tech": ["lab_technician"],
    "rd": ["rd_engineer"],
    "admin": ["sys_admin"],
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'stage0_03.db'}")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    import app.db.database as db_mod
    import app.services.session.memory_service as ms_mod

    db_mod._default.clear()
    ms_mod._session_service = None
    from app.db.database import default_session_factory
    from app.db.models import ChatSessionRow
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        for name, roles in USERS.items():
            accounts.create_user(s, username=name, password=PASSWORD, roles=roles)
        # Pre-upgrade legacy row: no owner, guessable id shape.
        s.add(ChatSessionRow(id="chat-legacy-1", title="legacy"))
        s.commit()
    from app.main import app

    client = TestClient(app)
    tokens = {}
    for name in USERS:
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield {"client": client, "h": tokens}


def test_session_ownership(env):
    c, h = env["client"], env["h"]
    payload = {"session_id": "chat-tech-1", "history": [{"role": "user", "content": "hi"}],
               "title": "tech session"}
    assert c.post("/api/session/save", json=payload, headers=h["tech"]).status_code == 200
    # Owner reads fine; another user gets 403; owner cannot be overwritten.
    assert c.get("/api/session/load/chat-tech-1", headers=h["tech"]).status_code == 200
    assert c.get("/api/session/load/chat-tech-1", headers=h["rd"]).status_code == 403
    assert c.post("/api/session/save", json=payload, headers=h["rd"]).status_code == 403
    assert c.delete("/api/session/delete/chat-tech-1", headers=h["rd"]).status_code == 403
    # No-token callers keep legacy behavior (dev / service key).
    assert c.get("/api/session/load/chat-tech-1").status_code == 200
    # List: each user sees only their own sessions; admin sees all.
    assert [s["session_id"] for s in c.get("/api/session/list", headers=h["tech"]).json()["sessions"]] == ["chat-tech-1"]
    assert c.get("/api/session/list", headers=h["rd"]).json()["sessions"] == []
    admin_list = [s["session_id"] for s in c.get("/api/session/list", headers=h["admin"]).json()["sessions"]]
    assert set(admin_list) == {"chat-tech-1", "chat-legacy-1"}
    # Legacy NULL-owner rows are admin-only for JWT callers (ids are guessable).
    assert c.get("/api/session/load/chat-legacy-1", headers=h["tech"]).status_code == 403
    assert c.get("/api/session/load/chat-legacy-1", headers=h["admin"]).status_code == 200
    assert c.post("/api/session/save", json={"session_id": "chat-legacy-1", "history": []},
                  headers=h["tech"]).status_code == 403


def test_mcp_grant_owner_binding(tmp_path, monkeypatch):
    from app.services import mcp_chat_tools, mcp_client
    from app.services import mcp_session_grants

    monkeypatch.setattr(
        "app.services.mcp_client.list_mcp_servers",
        lambda: [{"id": "fs", "command": "echo", "args": [], "enabled": True, "env": {}, "transport": "stdio"}],
    )
    monkeypatch.setattr(
        "app.services.mcp_client.probe_server",
        lambda s, **k: {"ok": True, "tools": []},
    )
    called = {}

    def fake_call(*a, **k):
        called["ok"] = True
        return {"ok": True, "result": {"content": "done"}}

    monkeypatch.setattr(mcp_client, "_call_tool", fake_call)

    class S:
        mcp_client_enabled = True

    mcp_session_grants.approve_session("sess-victim", "fs", tool_name="write_file", owner_user_id="victim-id")
    # Attacker holding the victim's session id must NOT spend the grant.
    out = mcp_chat_tools.maybe_run_selected_mcp(
        "/mcp fs write_file {}", ["fs"], session_id="sess-victim", settings=S(), owner_user_id="attacker-id")
    assert out["mcp_permission_required"] is not None
    assert "ok" not in called
    # The granting owner can.
    out = mcp_chat_tools.maybe_run_selected_mcp(
        "/mcp fs write_file {}", ["fs"], session_id="sess-victim", settings=S(), owner_user_id="victim-id")
    assert out["mcp_permission_required"] is None
    assert called["ok"] is True
