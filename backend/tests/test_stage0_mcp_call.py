"""Stage 0 / SR-007: POST /api/connectors/mcp/call requires a signed-in account with knowledge.read."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

PASSWORD = "Aa12345678"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'stage0_04.db'}")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    import app.db.database as db_mod

    db_mod._default.clear()
    from app.db.database import default_session_factory
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        accounts.create_user(s, username="rd", password=PASSWORD, roles=["rd_engineer"])
        accounts.create_user(s, username="norole", password=PASSWORD, roles=[])
        s.commit()
    from app.main import app

    client = TestClient(app)
    tokens = {}
    for name in ("rd", "norole"):
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield {"client": client, "h": tokens}


def test_mcp_call_requires_login_and_knowledge_read(env):
    c, h = env["client"], env["h"]
    body = {"server_id": "nope", "tool_name": "read_something", "arguments": {}}
    assert c.post("/api/connectors/mcp/call", json=body).status_code == 401
    assert c.post("/api/connectors/mcp/call", json=body, headers=h["norole"]).status_code == 403
    r = c.post("/api/connectors/mcp/call", json=body, headers=h["rd"])
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False  # passes the gate; no such server configured
