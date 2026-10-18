"""开发方案 §4.4：R&D 写接口 RBAC + formulation.read_exact 落实。

越权测试：无权限的个人账号调写接口应 403；跨项目越权应 403；
无 read_exact 只见区间配比。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

PASSWORD = "Fx-Rbac-2026"
USERS = {
    "rd": ["rd_engineer"],       # formulation.write / project.write / read_exact
    "lead": ["project_lead"],    # spec.write / project.write / read_exact
    "lab": ["lab_technician"],   # formulation.read only — no write, no read_exact
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'rbac.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models_identity import ProjectMemberRow
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        ids = {name: accounts.create_user(s, username=name, password=PASSWORD, roles=roles).id
               for name, roles in USERS.items()}
        # scoped: no global roles, project_lead in project "proj-a" only.
        scoped_id = accounts.create_user(s, username="scoped", password=PASSWORD, roles=[]).id
        s.add(ProjectMemberRow(project_id="proj-a", user_id=scoped_id, role_code="project_lead"))
        s.commit()
    from app.main import app

    client = TestClient(app)
    tokens = {}
    for name in [*USERS, "scoped"]:
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield {"client": client, "h": tokens}
    get_settings.cache_clear()


def _formula(weight: float) -> dict:
    return {
        "name": "FX-100",
        "domain": "anticorrosion_coating",
        "ingredients": [
            {"name": "二甲苯", "role": "solvent", "weight_pct": weight},
            {"name": "水", "role": "solvent", "weight_pct": 100 - weight},
        ],
    }


# ------------------------------------------------------------------ 写接口越权

def test_formulation_write_requires_permission(env):
    c, h = env["client"], env["h"]
    body = {"formulation": _formula(30), "lineage_id": "lin-1"}
    r = c.post("/api/formulations/versions", json=body, headers=h["lab"])
    assert r.status_code == 403, r.text
    r = c.post("/api/formulations/versions", json=body, headers=h["rd"])
    assert r.status_code == 200, r.text


def test_project_write_requires_permission(env):
    c, h = env["client"], env["h"]
    r = c.put("/api/projects/anything", json={}, headers=h["lab"])
    assert r.status_code == 403, r.text
    r = c.delete("/api/projects/anything", headers=h["lab"])
    assert r.status_code == 403, r.text


def test_cross_project_write_is_forbidden(env):
    c, h = env["client"], env["h"]
    # scoped has project.write in proj-a only.
    r = c.put("/api/projects/proj-a", json={}, headers=h["scoped"])
    assert r.status_code != 403, r.text  # passes guard; 404 (no such project in store)
    r = c.put("/api/projects/proj-b", json={}, headers=h["scoped"])
    assert r.status_code == 403, r.text


def test_doe_optimize_ingest_qc_loop_require_permissions(env):
    c, h = env["client"], env["h"]
    lab = h["lab"]
    assert c.post("/api/doe", json={}, headers=lab).status_code == 403
    assert c.post("/api/optimize", json={}, headers=lab).status_code == 403
    assert c.post("/api/ingest", json={}, headers=lab).status_code == 403
    assert c.post("/api/qc/report", json={}, headers=lab).status_code == 403
    assert c.post("/api/loop/iterate", json={}, headers=lab).status_code == 403
    assert c.delete("/api/kb/sources/x", headers=lab).status_code == 403


def test_reads_and_service_keys_unaffected(env):
    c, h = env["client"], env["h"]
    # Reads stay open for any signed-in account.
    assert c.get("/api/projects", headers=h["lab"]).status_code == 200
    # Service API key (no personal token) keeps working as before.
    body = {"formulation": _formula(30), "lineage_id": "lin-svc"}
    assert c.post("/api/formulations/versions", json=body).status_code == 200


def test_spec_write_requires_permission(env):
    c, h = env["client"], env["h"]
    # Create a PLM revision via service-key calls (guard skips operator keys).
    pid = c.post("/api/plm/products", json={"code": "FX-200", "name_zh": "FX-200"}).json()["id"]
    rid = c.post(f"/api/plm/products/{pid}/revisions",
                 json={"formulation_version_id": None, "spec_snapshot": {}}).json()["id"]
    r = c.put(f"/api/plm/revisions/{rid}/spec", json={"spec_snapshot": {"a": 1}}, headers=h["lab"])
    assert r.status_code == 403, r.text
    r = c.put(f"/api/plm/revisions/{rid}/spec", json={"spec_snapshot": {"a": 1}}, headers=h["lead"])
    assert r.status_code == 200, r.text


# ------------------------------------------------------------------ read_exact

def test_version_detail_redacts_exact_without_read_exact(env):
    c, h = env["client"], env["h"]
    vid = c.post("/api/formulations/versions",
                 json={"formulation": _formula(30), "lineage_id": "lin-exact"}).json()["id"]
    # No read_exact → bands only.
    got = c.get(f"/api/formulations/versions/detail/{vid}", headers=h["lab"]).json()
    ings = got["snapshot"]["ingredients"]
    assert all("weight_pct" not in i for i in ings)
    assert all("weight_pct_band" in i for i in ings)
    assert ings[0]["weight_pct_band"] == "30%～40%"
    # read_exact → exact values.
    got = c.get(f"/api/formulations/versions/detail/{vid}", headers=h["rd"]).json()
    assert got["snapshot"]["ingredients"][0]["weight_pct"] == 30
    # Service key → exact values (operator credential, unchanged behaviour).
    got = c.get(f"/api/formulations/versions/detail/{vid}").json()
    assert got["snapshot"]["ingredients"][0]["weight_pct"] == 30


def test_version_diff_redacts_exact_without_read_exact(env):
    c, h = env["client"], env["h"]
    v1 = c.post("/api/formulations/versions",
                json={"formulation": _formula(30), "lineage_id": "lin-diff"}).json()["id"]
    v2 = c.post("/api/formulations/versions",
                json={"formulation": _formula(40), "lineage_id": "lin-diff",
                      "parent_version_id": v1}).json()["id"]
    got = c.get(f"/api/formulations/versions/{v1}/diff/{v2}", headers=h["lab"]).json()
    ch = got["ingredient_changes"][0]
    assert ch["before_pct"] is None and ch["after_pct"] is None
    assert ch["before_pct_band"] == "30%～40%"
    assert ch["after_pct_band"] == "40%～50%"
    got = c.get(f"/api/formulations/versions/{v1}/diff/{v2}", headers=h["rd"]).json()
    assert got["ingredient_changes"][0]["before_pct"] == 30
    assert got["ingredient_changes"][0]["after_pct"] == 40
