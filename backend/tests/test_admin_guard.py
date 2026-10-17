"""Administrative writes need ``admin.llm`` / ``admin.connectors`` for signed-in accounts (app/api/_admin_guard.py)."""
from __future__ import annotations

import pytest

from app.api._admin_guard import required_permission
from tests.test_plm_release import env  # noqa: F401  (fixture)


@pytest.mark.parametrize(("method", "path", "perm"), [
    ("GET", "/api/settings", None),
    ("POST", "/api/settings", "admin.llm"),
    ("POST", "/api/settings/secrets", "admin.llm"),
    ("POST", "/api/settings/env-flags", "admin.llm"),
    ("POST", "/api/dependencies/install", "admin.llm"),
    ("POST", "/api/embedding-models/switch", "admin.llm"),
    ("DELETE", "/api/skills/installed/x", "admin.llm"),
    ("POST", "/api/skills/install/github", "admin.llm"),
    ("PUT", "/api/connectors/mcp", "admin.connectors"),
    ("DELETE", "/api/connectors/mcp/srv", "admin.connectors"),
    ("POST", "/api/connectors/builtin/pubchem/toggle", "admin.connectors"),
    ("POST", "/api/connectors/builtin/pubchem/search", None),
    ("POST", "/api/connectors/mcp/call", None),
    ("POST", "/api/connectors/mcp/srv/approve-session", None),
    ("POST", "/api/settingsx", None),
    ("POST", "/api/chat", None),
])
def test_required_permission(method, path, perm):
    assert required_permission(method, path) == perm


def test_signed_in_account_needs_the_permission(env):  # noqa: F811
    c, h = env["client"], env["h"]
    assert c.get("/api/settings", headers=h["rd"]).status_code == 200  # reads stay open (masked status)
    r = c.post("/api/settings/env-flags", headers=h["rd"], json={"flags": {}})
    assert r.status_code == 403 and "admin.llm" in r.text
    assert c.put("/api/connectors/mcp", headers=h["rd"], json={"servers": []}).status_code == 403
    assert c.post("/api/dependencies/install", headers=h["rd"], json={"packages": []}).status_code == 403
    # the platform administrator passes the guard (whatever the endpoint then answers)
    assert c.post("/api/settings/env-flags", headers=h["admin"], json={"flags": {}}).status_code != 403
    # a service API key / development mode without a user token is an operator credential, not a person
    assert c.post("/api/settings/env-flags", json={"flags": {}}).status_code != 403
