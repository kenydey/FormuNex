"""Stage 0: SR-001 connectors GET masking + SR-044 prefs file treatment."""
from __future__ import annotations

import os
import stat

from fastapi.testclient import TestClient

from app.db.database import make_engine
from app.main import app
from app.services import mcp_client
from app.services import skills_store


def _client(tmp_path, monkeypatch):
    db_path = tmp_path / "stage0.db"
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("FORMUNEX_MCP_CLIENT_ENABLED", "false")
    import app.db.database as db_mod
    import app.db.project_store as ps_mod
    import app.config as cfg

    db_mod._default.clear()
    ps_mod._store = None
    cfg.get_settings.cache_clear()
    monkeypatch.setattr(skills_store, "_prefs_path", lambda: tmp_path / "skills_prefs.json")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    make_engine(f"sqlite:///{db_path.as_posix()}")
    return TestClient(app)


def test_connectors_get_masks_mcp_env(tmp_path, monkeypatch):
    _client(tmp_path, monkeypatch)
    mcp_client.save_mcp_servers([
        {"id": "fs", "command": "npx", "args": [], "enabled": True,
         "env": {"THIRD_PARTY_KEY": "sk-secret-value-12345"}, "transport": "stdio"},
    ])
    # Internal callers still see real env; only the HTTP layer masks.
    assert mcp_client.list_mcp_servers()[0]["env"]["THIRD_PARTY_KEY"] == "sk-secret-value-12345"

    r = TestClient(app).get("/api/connectors")
    assert r.status_code == 200, r.text
    assert "sk-secret-value-12345" not in r.text
    mcp = r.json()["mcp"]
    server = next(s for s in mcp if s["id"] == "fs")
    assert set(server["env"]) == {"THIRD_PARTY_KEY"}
    assert server["env"]["THIRD_PARTY_KEY"] != "sk-secret-value-12345"


def test_save_prefs_atomic_0600(tmp_path, monkeypatch):
    monkeypatch.setattr(skills_store, "_prefs_path", lambda: tmp_path / "skills_prefs.json")
    out = skills_store.save_prefs({"mcp_servers": [{"id": "x", "command": "echo"}]})
    assert out["mcp_servers"][0]["id"] == "x"
    path = tmp_path / "skills_prefs.json"
    assert path.is_file()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # No temp files left behind, and a second save round-trips.
    assert list(tmp_path.glob(".*.tmp")) == []
    assert skills_store.load_prefs()["mcp_servers"][0]["id"] == "x"
