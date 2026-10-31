"""Stage 0 / SR-005: sensitive reads gated + org dashboard banding."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

PASSWORD = "Aa12345678"
USERS = {
    "tech": ["lab_technician"],   # formulation.read + lab.execute, NO plm.document.read, NO read_exact
    "rd": ["rd_engineer"],       # plm.document.read + read_exact
    "rm": ["release_manager"],   # neither lab.execute nor lab.review
    "admin": ["sys_admin"],      # no formulation.* at all
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'stage0_02.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import ExperimentRow, ProjectRow
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        for name, roles in USERS.items():
            accounts.create_user(s, username=name, password=PASSWORD, roles=roles)
        proj = ProjectRow(id="p1", title="P1")
        s.add(proj)
        s.add(ExperimentRow(domain="coating", project_id="p1",
                            factors={"秘密原料": 30.0, "水": 70.0},
                            measured={"光泽": 88.0}))
        s.commit()
    from app.main import app

    client = TestClient(app)
    tokens = {}
    for name in USERS:
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield {"client": client, "h": tokens}


def test_plm_sensitive_reads_gated(env):
    c, h = env["client"], env["h"]
    paths = [
        "/api/plm/documents/nope",
        "/api/plm/documents/nope/file/pdf",
        "/api/plm/packages/nope/delivery",
        "/api/plm/documents/batch-export/nope/download",
    ]
    for path in paths:
        assert c.get(path, headers=h["tech"]).status_code == 403, path
        # rd holds plm.document.read: passes the gate, then 404s on the missing row.
        assert c.get(path, headers=h["rd"]).status_code == 404, path


def test_attachment_download_gate(env):
    c, h = env["client"], env["h"]
    path = "/api/lab/tasks/nope/attachments/nope/download"
    assert c.get(path, headers=h["rm"]).status_code == 403
    # lab_technician holds global lab.execute: passes the gate, then 404s.
    assert c.get(path, headers=h["tech"]).status_code == 404


def test_org_dashboard_banding(env):
    from app.api.org import invalidate_org_dashboard_cache

    c, h = env["client"], env["h"]
    invalidate_org_dashboard_cache()
    exact = c.get("/api/org/dashboard", headers=h["rd"])
    assert exact.status_code == 200, exact.text
    top = exact.json()["top_performers"]
    assert top and "秘密原料" in top[0]["formulation_preview"]
    freq = exact.json()["ingredient_frequency"]
    assert freq and freq[0]["avg_weight_pct"] is not None

    invalidate_org_dashboard_cache()
    banded = c.get("/api/org/dashboard", headers=h["tech"])
    assert banded.status_code == 200, banded.text
    top = banded.json()["top_performers"]
    assert top and top[0]["formulation_preview"] == ""
    freq = banded.json()["ingredient_frequency"]
    assert freq and freq[0]["avg_weight_pct"] is None

    # A JWT without formulation.read still gets banded data from the API
    # (the nav/page gates are the UI layer); exact values never leak.
    invalidate_org_dashboard_cache()
    admin = c.get("/api/org/dashboard", headers=h["admin"])
    assert admin.status_code == 200, admin.text
    assert admin.json()["top_performers"][0]["formulation_preview"] == ""
