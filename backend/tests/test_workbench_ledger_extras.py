"""Workbench ledger extras: batch attachment counts, native row versions (audit-log backed),
GET /api/experiments/{id}, and the correctly spelled doecycle-status route (+ legacy alias)."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.campaign_store import LocalCampaignStore, reset_campaign_store
from app.domain.schemas import DOEPlan, DOERun, ObjectiveSpec, ProductDomain, Requirement


def _plan() -> DOEPlan:
    return DOEPlan(
        design="lhs", factors=[], notes="t", plan_id="wbx12345", domain=ProductDomain.anticorrosion_coating,
        runs=[
            DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0}),
            DOERun(run_id=2, coded={}, natural={"Zinc phosphate": 10.0}),
            DOERun(run_id=3, coded={}, natural={"Zinc phosphate": 12.0}),
        ],
    )


def _req() -> dict:
    return Requirement(
        domain=ProductDomain.anticorrosion_coating,
        objectives=[ObjectiveSpec(metric="salt_spray_hours", weight=1.0, direction="maximize")],
    ).model_dump()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'wb.db'}")
    monkeypatch.setenv("FORMUNEX_CAMPAIGN_BACKEND", "sqlite")
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.main import app

    reset_campaign_store(LocalCampaignStore(default_session_factory()))
    # get_measurement_store() caches a store bound to whichever database an earlier test used.
    monkeypatch.setattr("app.db.measurement_store._store", None)
    yield TestClient(app)
    reset_campaign_store(None)
    get_settings.cache_clear()


def _campaign(c) -> dict:
    r = c.post("/api/experiments/workbench/campaigns", json={"plan": _plan().model_dump(), "requirement": _req()})
    assert r.status_code == 200, r.text
    return r.json()


def _sync(c, cid: int, row: dict, **changes) -> dict:
    body = {"campaign_id": cid, "rows": [{
        "id": row["id"], "actual_params": row["actual_params"], "measurements": row["measurements"],
        "status": row["status"], "note": row.get("note"), "tags": row.get("tags") or [], **changes}]}
    r = c.put("/api/experiments/workbench/sync", json=body)
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["rows"] if x["id"] == row["id"])


# ── attachment counts ────────────────────────────────────────────────────────


def test_attachment_counts_in_one_call(client):
    from app.db.database import default_session_factory
    from app.db.models import ExperimentAttachment, ExperimentRow, SourceDocument
    from app.services.workbench_training import workbench_record_label

    camp = _campaign(client)
    cid = camp["campaign_id"]
    rows = camp["rows"]
    with default_session_factory()() as s:
        exp = ExperimentRow(domain="anticorrosion_coating", measured={}, label=workbench_record_label(cid, rows[0]["item_id"]))
        exp2 = ExperimentRow(domain="anticorrosion_coating", measured={}, label=workbench_record_label(cid, rows[1]["item_id"]))
        s.add_all([exp, exp2])
        s.flush()
        for i in range(2):
            doc = SourceDocument(id=str(uuid.uuid4()), content_hash=f"h{i}")
            s.add(doc)
            s.flush()
            s.add(ExperimentAttachment(id=str(uuid.uuid4()), experiment_id=exp.id, source_document_id=doc.id))
        s.commit()

    r = client.get(f"/api/experiments/workbench/{cid}/attachment-counts")
    assert r.status_code == 200, r.text
    counts = r.json()["counts"]
    assert counts == {str(rows[0]["id"]): 2, str(rows[1]["id"]): 0, str(rows[2]["id"]): 0}
    # the per-row endpoint agrees
    assert len(client.get(f"/api/experiments/workbench/{cid}/rows/{rows[0]['id']}/attachments").json()) == 2
    assert client.get("/api/experiments/workbench/999999/attachment-counts").status_code == 404


# ── native row versions ──────────────────────────────────────────────────────


def test_native_row_versions_list_diff_restore(client):
    camp = _campaign(client)
    cid, row = camp["campaign_id"], camp["rows"][0]
    base = f"/api/experiments/workbench/{cid}/rows/{row['id']}/versions"
    assert client.get(base).json() == {"refcode": None, "source": "native", "versions": []}

    _sync(client, cid, row, note="first")
    v = client.get(base).json()["versions"]
    assert [x["action"] for x in v] == ["baseline", "update"]
    assert v[0]["snapshot"]["note"] is None and v[1]["snapshot"]["note"] == "first"

    _sync(client, cid, row, note="second", actual_params={**row["actual_params"], "Zinc phosphate": 9.5})
    v = client.get(base).json()["versions"]
    assert [x["version"] for x in v] == [1, 2, 3]
    diff = client.get(base, params={"compare_v1": v[1]["id"], "compare_v2": v[2]["id"]}).json()["diff"]
    assert "root['note']" in diff["values_changed"]
    assert "root['actual_params']['Zinc phosphate']" in diff["values_changed"]

    # an unchanged save adds no version
    current = client.get(f"/api/experiments/workbench/{cid}").json()["rows"][0]
    _sync(client, cid, current)
    vv = client.get(base).json()["versions"]
    assert len(vv) == 3, client.get(base, params={"compare_v1": vv[2]["id"], "compare_v2": vv[-1]["id"]}).json()

    r = client.post(f"{base}/{v[0]['id']}/restore", json={})
    assert r.status_code == 200, r.text
    restored = client.get(f"/api/experiments/workbench/{cid}").json()["rows"][0]
    assert restored["note"] is None and restored["actual_params"]["Zinc phosphate"] == 8.0
    v = client.get(base).json()["versions"]
    assert v[-1]["action"] == "restore" and len(v) == 4  # restore is itself a version → reversible

    assert client.post(f"{base}/424242/restore", json={}).status_code == 404
    assert client.get(f"/api/experiments/workbench/{cid}/rows/999/versions").status_code == 404
    # other rows keep their own (empty) history
    other = camp["rows"][1]["id"]
    assert client.get(f"/api/experiments/workbench/{cid}/rows/{other}/versions").json()["versions"] == []


# ── experiment by id ─────────────────────────────────────────────────────────


def test_get_experiment_by_id_with_project_scope(client):
    from app.db.database import default_session_factory
    from app.db.models import ExperimentRow

    with default_session_factory()() as s:
        e = ExperimentRow(domain="anticorrosion_coating", project_id="p1", measured={"salt_spray_hours": 500.0},
                          label="L1", source="lab")
        s.add(e)
        s.commit()
        eid = e.id
    r = client.get(f"/api/experiments/{eid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == eid and body["label"] == "L1" and body["measured"] == {"salt_spray_hours": 500.0}
    assert body["measurement_count"] == 0 and body["project_id"] == "p1"
    assert client.get(f"/api/experiments/{eid}", params={"project_id": "p1"}).status_code == 200
    assert client.get(f"/api/experiments/{eid}", params={"project_id": "other"}).status_code == 404
    assert client.get("/api/experiments/987654").status_code == 404
    # literal sibling routes are not shadowed
    assert client.get("/api/experiments/workbench/campaigns").status_code == 200


# ── doecycle-status spelling ─────────────────────────────────────────────────


def test_doecycle_status_correct_path_and_legacy_alias(client):
    from app.main import app

    cid = _campaign(client)["campaign_id"]
    new = client.get(f"/api/experiments/hooks/doecycle-status/{cid}")
    old = client.get(f"/api/experiments/hooks/doecyle-status/{cid}")
    assert new.status_code == old.status_code == 200
    assert new.json()["isPaused"] is False and new.json() == old.json()
    paths = app.openapi()["paths"]
    assert "/api/experiments/hooks/doecycle-status/{campaign_id}" in paths
    assert "/api/experiments/hooks/doecyle-status/{campaign_id}" not in paths
    assert "/api/experiments/{experiment_id}" in paths
    assert "/api/experiments/workbench/{campaign_id}/attachment-counts" in paths
