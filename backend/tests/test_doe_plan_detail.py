"""DOE plan by id (GET /api/doe/plans/{plan_id}) + /api/doe/active persistence → lab dispatch."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

PW = "Fx-Doe-2026-ok"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'doe.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "doe-secret-" + "z" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        accounts.create_user(s, username="lead", password=PW, roles=["project_lead"])
        s.commit()
    from app.main import app

    c = TestClient(app)
    tok = c.post("/api/identity/login", json={"username": "lead", "password": PW}).json()["access_token"]
    yield c, {"Authorization": "Bearer " + tok}
    get_settings.cache_clear()


REQ = {"domain": "anticorrosion_coating", "project_id": "p-doe", "n_suggest": 3, "engine": "legacy"}


def _active(c, **extra):
    r = c.post("/api/doe/active", json={**REQ, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_active_plan_is_persisted_and_dispatchable(env):
    c, h = env
    body = _active(c)
    pid = body["plan"]["plan_id"]
    assert pid and body["plan_id"] == pid
    r = c.post("/api/lab/tasks/dispatch", headers=h, json={
        "doe_plan_id": pid, "metrics": ["salt_spray_hours"], "project_id": "p-doe"})
    assert r.status_code == 201, r.text
    assert len(r.json()["created"]) == len(body["plan"]["runs"])


def test_active_plan_with_unknown_campaign_still_persists(env):
    c, h = env
    pid = _active(c, workbench_campaign_id=987654)["plan"]["plan_id"]
    r = c.post("/api/lab/tasks/dispatch", headers=h, json={
        "doe_plan_id": pid, "metrics": ["salt_spray_hours"], "project_id": "p-doe"})
    assert r.status_code == 201, r.text


def test_get_plan_by_id_with_project_scope(env):
    c, _ = env
    body = _active(c)
    pid = body["plan"]["plan_id"]
    r = c.get(f"/api/doe/plans/{pid}")
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["plan_id"] == pid and item["project_id"] == "p-doe" and item["status"] == "draft"
    assert len(item["runs"]) == len(body["plan"]["runs"]) and item["factors"]
    assert {"design", "notes", "domain", "campaign_id", "round", "created_at"} <= set(item)
    assert c.get(f"/api/doe/plans/{pid}", params={"project_id": "p-doe"}).status_code == 200
    assert c.get(f"/api/doe/plans/{pid}", params={"project_id": "other"}).status_code == 404
    assert c.get("/api/doe/plans/nope").status_code == 404
    # same item shape as history
    hist = c.get("/api/doe/history", params={"project_id": "p-doe"}).json()["items"]
    assert {k: v for k, v in item.items() if k != "project_id"} == next(h for h in hist if h["plan_id"] == pid)


def test_active_plan_with_unknown_campaign_reports_plan_id(env):
    c, _ = env
    body = _active(c, workbench_campaign_id=55555)
    assert body["plan_id"] == body["plan"]["plan_id"]
    assert c.get(f"/api/doe/plans/{body['plan_id']}").json()["campaign_id"] is None
