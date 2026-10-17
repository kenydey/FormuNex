"""GET /api/artifacts/lineages — lineage picker for the research versions panel."""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import artifact_versions as api_mod
from app.services import artifact_versions as svc


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_data_root", lambda: tmp_path)
    app = FastAPI()
    app.include_router(api_mod.router)
    return TestClient(app)


def _lineage(c, project, name, kind="report"):
    r = c.post("/api/artifacts/lineages", json={"project_id": project, "name": name, "kind": kind})
    assert r.status_code == 200, r.text
    return r.json()["lineage_id"]


def test_empty(client):
    r = client.get("/api/artifacts/lineages")
    assert r.status_code == 200
    assert r.json() == []


def test_lists_with_latest_version_and_filters(client):
    a = _lineage(client, "p1", "Report A")
    time.sleep(0.01)
    b = _lineage(client, "p1", "Formula B", kind="formulation")
    time.sleep(0.01)
    _lineage(client, "p2", "Other")
    time.sleep(0.01)
    v1 = client.post(f"/api/artifacts/lineages/{a}/versions", json={"content": "x"}).json()
    v2 = client.post(f"/api/artifacts/lineages/{a}/versions", json={"content": "y", "based_on_version_id": v1["version_id"]}).json()
    client.post(f"/api/artifacts/versions/{v2['version_id']}/submit")

    rows = client.get("/api/artifacts/lineages", params={"project_id": "p1"}).json()
    assert [r["lineage_id"] for r in rows] == [a, b]  # A has the newest activity
    ra = rows[0]
    assert ra["title"] == "Report A" and ra["kind"] == "report"
    assert ra["latest_version"] == 2 and ra["latest_version_id"] == v2["version_id"]
    assert ra["status"] == "pending"
    assert rows[1]["latest_version"] is None and rows[1]["status"] is None

    assert [r["lineage_id"] for r in client.get("/api/artifacts/lineages", params={"kind": "formulation"}).json()] == [b]
    assert len(client.get("/api/artifacts/lineages").json()) == 3
    assert len(client.get("/api/artifacts/lineages", params={"limit": 1}).json()) == 1


def test_skips_tampered_lineage(client, tmp_path):
    a = _lineage(client, "p1", "A")
    path = tmp_path / "artifacts" / "lineages" / f"{a}.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"A"', '"B"'), encoding="utf-8")
    assert client.get("/api/artifacts/lineages").json() == []


def test_disabled_returns_404(client, monkeypatch):
    monkeypatch.setattr(svc, "artifact_versions_enabled", lambda s: False)
    assert client.get("/api/artifacts/lineages").status_code == 404
