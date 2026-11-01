"""Stage 3 / SR-026 (3-3a): /ingest/url and /ingest/task are 202 + poll.

The fetch + parse used to run inline, holding the HTTP connection until
the origin answered — a slow origin surfaced as a 502 while the work
still completed behind it. Now: 202 with a task id, results via
GET /api/tasks/{id}, failures visible on the task (and the outbox row).
SSRF pre-flight and input-shape errors stay sync 400s.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.domain.schemas import Evidence
from app.main import app
from app.services.document_task import TaskOutcome
from app.services.ingestion import IngestOutcome


def _wait(client: TestClient, task_id: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    task: dict = {}
    while time.monotonic() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()
        if task.get("state") in ("completed", "failed"):
            return task
        time.sleep(0.1)
    return task


@pytest.fixture()
def client():
    return TestClient(app)


def _ok_outcome():
    return IngestOutcome(
        evidence=[Evidence(source="web", identifier="http://93.184.216.34/x", title="x",
                           snippet="hello", relevance=0.9)],
        source_id="src-1",
        extraction_status="ok",
    )


def test_url_ingest_returns_202_and_pollable_result(client, monkeypatch):
    import app.services.ingestion as ingestion_mod
    from app.services import colbert_store

    indexed = []
    monkeypatch.setattr(ingestion_mod, "ingest_url", lambda url: _ok_outcome())
    monkeypatch.setattr(colbert_store, "index_evidence", lambda ev, **kw: indexed.append(ev))
    r = client.post("/api/ingest/url", json={"url": "http://93.184.216.34/x"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["task_id"] and body["stream_url"].endswith("/stream")
    task = _wait(client, body["task_id"])
    assert task.get("state") == "completed", task
    assert task["result"]["source_id"] == "src-1"
    assert task["result"]["extraction_status"] == "ok"
    assert len(indexed) == 1


def test_url_ingest_failure_is_visible_on_task(client, monkeypatch):
    import app.services.ingestion as ingestion_mod

    def boom(url):
        raise RuntimeError("origin timed out")

    monkeypatch.setattr(ingestion_mod, "ingest_url", boom)
    r = client.post("/api/ingest/url", json={"url": "http://93.184.216.34/x"})
    assert r.status_code == 202
    task = _wait(client, r.json()["task_id"])
    assert task.get("state") == "failed", task
    assert "origin timed out" in (task.get("message") or str(task.get("result")))


def test_url_ingest_ssrf_stays_sync_400(client):
    r = client.post("/api/ingest/url", json={"url": "http://127.0.0.1/secret"})
    assert r.status_code == 400


def test_task_ingest_returns_202_and_pollable_result(client, monkeypatch):
    import app.services.document_task as doc_task_mod
    from app.services import colbert_store

    def fake_resolve(doc_type, identifier):
        return TaskOutcome(doc_type=doc_type, identifier=identifier,
                           evidence=_ok_outcome().evidence, source_id="src-9",
                           extraction_status="ok")

    monkeypatch.setattr(doc_task_mod, "resolve_document", fake_resolve)
    monkeypatch.setattr(colbert_store, "index_evidence", lambda ev, **kw: None)
    r = client.post("/api/ingest/task", json={"doc_type": "paper", "identifier": "10.1000/xyz"})
    assert r.status_code == 202, r.text
    task = _wait(client, r.json()["task_id"])
    assert task.get("state") == "completed", task
    assert task["result"]["source_id"] == "src-9"


def test_task_ingest_resolve_error_is_failed_task_not_502(client, monkeypatch):
    import app.services.document_task as doc_task_mod

    def fake_resolve(doc_type, identifier):
        return TaskOutcome(doc_type=doc_type, identifier=identifier, error="no full text found")

    monkeypatch.setattr(doc_task_mod, "resolve_document", fake_resolve)
    r = client.post("/api/ingest/task", json={"doc_type": "paper", "identifier": "10.1000/xyz"})
    assert r.status_code == 202
    task = _wait(client, r.json()["task_id"])
    assert task.get("state") == "failed", task
    assert "no full text found" in (task.get("message") or str(task.get("result")))


def test_task_ingest_malformed_web_identifier_stays_sync_400(client):
    r = client.post("/api/ingest/task", json={"doc_type": "web", "identifier": "not-a-url"})
    assert r.status_code == 400


def test_evidence_ingest_crash_is_failed_task(client, monkeypatch):
    # Stage 3 / SR-026 (3-3b): an unexpected crash in the worker surfaces
    # as a failed task, not a lost request.
    import app.services.kb_ingest as kb_ingest_mod

    def boom(ev, **kw):
        raise RuntimeError("store exploded")

    monkeypatch.setattr(kb_ingest_mod, "ingest_single_evidence", boom)
    r = client.post("/api/kb/ingest-evidence",
                    json={"identifier": "CN104789083B", "source": "surechembl"})
    assert r.status_code == 202, r.text
    task = _wait(client, r.json()["task_id"])
    assert task.get("state") == "failed", task
    assert "store exploded" in (task.get("message") or str(task.get("result")))
