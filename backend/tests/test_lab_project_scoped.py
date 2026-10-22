"""P1-12: project-scoped roles on the lab execution / review chain.

Before the fix, every endpoint below checked only the *global* permission, so
a user holding e.g. lab_technician only inside project ``proj-A`` got 403 on
the whole chain even for ``proj-A`` tasks. After the fix each endpoint
resolves the project from the stored row (task / measurement → task /
attachment → task / ELN link → task) and checks the permission in that
project: own-project calls pass, other-project calls still 403.

Deliberate non-change, locked in here: the method / instrument library is
global (rows carry no project_id), so project scoping does not apply to it —
a project-scoped user still gets 403 on method creation. Giving the library a
project_id parameter would let a project role mutate the shared global
library, which is a scope widening, not a fix.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

PW = "Fx-Lab-2026-ok"
PROJ_A = "proj-A"
PROJ_B = "proj-B"
DENSITY = {"metric": "density_g_cm3", "value": 1.347, "method_code": "GB/T 6750"}


@pytest.fixture()
def psc(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'lab-psc.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "lab-psc-secret-" + "z" * 40)
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "files"))
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models_identity import ProjectMemberRow
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        lead = accounts.create_user(s, username="lead", password=PW, roles=["project_lead"])
        # Project-only users: no global roles at all.
        tech_p = accounts.create_user(s, username="tech_p", password=PW, roles=None)
        qa_p = accounts.create_user(s, username="qa_p", password=PW, roles=None)
        lead_p = accounts.create_user(s, username="lead_p", password=PW, roles=None)
        s.flush()
        for user, role in ((tech_p, "lab_technician"), (qa_p, "quality"), (lead_p, "project_lead")):
            s.add(ProjectMemberRow(project_id=PROJ_A, user_id=user.id, role_code=role))
        s.commit()
    from app.main import app

    c = TestClient(app)
    h = {n: {"Authorization": "Bearer " + c.post(
        "/api/identity/login", json={"username": n, "password": PW}).json()["access_token"]}
        for n in ("lead", "tech_p", "qa_p", "lead_p")}
    assert c.post("/api/lab/methods/seed", headers=h["lead"]).json()["added"] >= 15
    yield {"c": c, "h": h}
    get_settings.cache_clear()


def _task(psc, project_id: str) -> dict:
    r = psc["c"].post("/api/lab/tasks", headers=psc["h"]["lead"], json={
        "factors": {}, "metrics": ["density_g_cm3"], "domain": "anticorrosion_coating",
        "project_id": project_id})
    assert r.status_code == 201, r.text
    return r.json()


def _record(psc, who: str, task_id: str, *entries) -> dict:
    r = psc["c"].post(f"/api/lab/tasks/{task_id}/measurements", headers=psc["h"][who],
                       json={"measurements": list(entries)})
    assert r.status_code == 201, r.text
    return r.json()


def test_project_technician_claims_records_and_qa_reviews_own_project(psc):
    c, h = psc["c"], psc["h"]
    task = _task(psc, PROJ_A)
    # Claim + record as the project-scoped technician.
    r = c.post(f"/api/lab/tasks/{task['id']}/claim", headers=h["tech_p"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_progress" and r.json()["assignee_id"] is not None
    m = _record(psc, "tech_p", task["id"], DENSITY)["measurements"][0]
    # Project-scoped reviewer accepts; submit by the claimer closes the loop.
    r = c.post(f"/api/lab/measurements/{m['id']}/review", headers=h["qa_p"], json={"decision": "accept"})
    assert r.status_code == 200, r.text
    r = c.post(f"/api/lab/tasks/{task['id']}/submit", headers=h["tech_p"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"
    # Pick by the project-scoped reviewer.
    r = c.post(f"/api/lab/measurements/{m['id']}/pick", headers=h["qa_p"], json={"reason": "代表值"})
    assert r.status_code == 200, r.text
    assert r.json()["values"]["density_g_cm3"]["basis"] == "manual"


def test_project_reviewer_can_amend_via_scoped_can_review(psc):
    c, h = psc["c"], psc["h"]
    task = _task(psc, PROJ_A)
    assert c.post(f"/api/lab/tasks/{task['id']}/claim", headers=h["tech_p"]).status_code == 200
    m = _record(psc, "tech_p", task["id"], DENSITY)["measurements"][0]
    # qa_p is not the operator; the service's can_review flag must come from
    # the *scoped* lab.review permission, not the global one.
    r = c.post(f"/api/lab/measurements/{m['id']}/amend", headers=h["qa_p"],
               json={"note": "复核备注", "reason": "补充说明"})
    assert r.status_code == 200, r.text
    assert r.json()["review_status"] == "unreviewed"


def test_cross_project_calls_still_403(psc):
    c, h = psc["c"], psc["h"]
    task_b = _task(psc, PROJ_B)
    assert c.post(f"/api/lab/tasks/{task_b['id']}/claim", headers=h["tech_p"]).status_code == 403
    # A proj-B measurement exists (recorded by the global lead); the proj-A
    # reviewer must not be able to review or amend it.
    assert c.post(f"/api/lab/tasks/{task_b['id']}/claim", headers=h["lead"]).status_code == 200
    m = _record(psc, "lead", task_b["id"], DENSITY)["measurements"][0]
    assert c.post(f"/api/lab/measurements/{m['id']}/review", headers=h["qa_p"],
                  json={"decision": "accept"}).status_code == 403
    assert c.post(f"/api/lab/measurements/{m['id']}/amend", headers=h["qa_p"],
                  json={"note": "x", "reason": "y"}).status_code == 403
    # Cancel: project lead of proj-A only.
    assert c.post(f"/api/lab/tasks/{_task(psc, PROJ_A)['id']}/cancel",
                  headers=h["lead_p"], json={"reason": "不需要"}).status_code == 200
    assert c.post(f"/api/lab/tasks/{_task(psc, PROJ_B)['id']}/cancel",
                  headers=h["lead_p"], json={"reason": "不需要"}).status_code == 403


def test_attachments_scoped_to_task_project(psc):
    c, h = psc["c"], psc["h"]
    task_a = _task(psc, PROJ_A)
    up = c.post(f"/api/lab/tasks/{task_a['id']}/attachments", headers=h["tech_p"],
                files={"file": ("spectrum.csv", b"a,b\n1,2\n", "text/csv")})
    assert up.status_code == 201, up.text
    att = up.json()
    # Project lead of proj-A deletes; the same user cannot delete a proj-B attachment.
    assert c.delete(f"/api/lab/tasks/{task_a['id']}/attachments/{att['id']}",
                    headers=h["lead_p"]).status_code == 200
    task_b = _task(psc, PROJ_B)
    up_b = c.post(f"/api/lab/tasks/{task_b['id']}/attachments", headers=h["lead"],
                  files={"file": ("spectrum.csv", b"a,b\n1,2\n", "text/csv")})
    assert up_b.status_code == 201, up_b.text
    assert c.delete(f"/api/lab/tasks/{task_b['id']}/attachments/{up_b.json()['id']}",
                    headers=h["lead_p"]).status_code == 403


def test_instrument_import_scoped_to_sample_tasks(psc):
    c, h = psc["c"], psc["h"]
    task_a = _task(psc, PROJ_A)
    task_b = _task(psc, PROJ_B)

    def _import(sample_no: str):
        rows = f"{sample_no},90,0.1,1,0.5\n"
        data = f"Sample,L*,a*,b*,ΔE\n{rows}".encode("gb18030")
        return c.post("/api/lab/measurements/import", headers=h["tech_p"],
                      files={"file": ("cm.csv", data, "text/csv")},
                      data={"template": "colorimeter_lab", "methods": json.dumps({"delta_e": "GB/T 11186.3"})})

    r = _import(task_a["sample_no"])
    assert r.status_code == 201, r.text
    assert len(r.json()["measurements"]) == 1
    assert _import(task_b["sample_no"]).status_code == 403


def test_eln_push_pull_resolve_scoped(psc, monkeypatch):
    from app.services.eln import sync
    from app.services.eln.connectors.base import RemoteRecord

    class Stub:
        name = "elabftw"
        can_push = True

        def push_plan(self, plan):
            return RemoteRecord(external_id=f"ext-{plan['task_no']}")

        def pull_measurements(self, external_id, *, since=None):
            return []

    monkeypatch.setattr(sync, "get_connector", lambda name: Stub())
    c, h = psc["c"], psc["h"]
    task_a = _task(psc, PROJ_A)
    task_b = _task(psc, PROJ_B)
    r = c.post("/api/lab/eln/elabftw/push", headers=h["lead_p"], json={"task_ids": [task_a["id"]]})
    assert r.status_code == 200, r.text
    assert len(r.json()["pushed"]) == 1
    assert c.post("/api/lab/eln/elabftw/push", headers=h["lead_p"],
                  json={"task_ids": [task_b["id"]]}).status_code == 403
    # Scoped pull: own project passes, other project 403 (task_b has no link,
    # but the gate runs before the connector call).
    assert c.post("/api/lab/eln/elabftw/pull", headers=h["qa_p"],
                  json={"task_ids": [task_a["id"]]}).status_code == 200
    assert c.post("/api/lab/eln/elabftw/pull", headers=h["qa_p"],
                  json={"task_ids": [task_b["id"]]}).status_code == 403
    # Resolve: seed one open conflict per project directly.
    from app.db.database import default_session_factory
    from app.db.models_lab import ElnLinkRow, ElnSyncConflictRow

    def _seed_conflict(task_id: str) -> str:
        with default_session_factory()() as s:
            link = ElnLinkRow(id=str(uuid.uuid4()), connector="elabftw", external_id=f"ext-{task_id}",
                              local_type="lab_task", local_id=task_id)
            s.add(link)
            s.flush()
            conf = ElnSyncConflictRow(id=str(uuid.uuid4()), link_id=link.id, status="open")
            s.add(conf)
            s.commit()
            return conf.id

    conf_a = _seed_conflict(task_a["id"])
    conf_b = _seed_conflict(task_b["id"])
    r = c.post(f"/api/lab/eln/conflicts/{conf_a}/resolve", headers=h["qa_p"], json={"resolution": "keep_local"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "resolved"
    assert c.post(f"/api/lab/eln/conflicts/{conf_b}/resolve", headers=h["qa_p"],
                  json={"resolution": "keep_local"}).status_code == 403


def test_method_library_stays_global_gated(psc):
    # Deliberate P1-12 non-change: the library has no project_id, so a
    # project-scoped lab_technician still cannot create methods.
    c, h = psc["c"], psc["h"]
    body = {"code": "Q/FX 999", "name_zh": "项目方法", "metric": "density_g_cm3", "unit": "g/cm3"}
    assert c.post("/api/lab/methods", headers=h["tech_p"], json=body).status_code == 403
