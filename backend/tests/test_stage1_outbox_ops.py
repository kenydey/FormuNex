"""Stage 1 / SR-002: outbox query + manual retry endpoints (API-only).

The query surface is guarded by the existing ``/api/ops`` admin.llm
rule; payloads are not returned, only status/attempts/task id and the
FAILED error summary. Retry rearms the stored row and publishes it
under a fresh id; publish itself is stubbed here so the test proves
the row transition, not the broker.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import dispatcher
from app.db.database import make_engine, make_session_factory
from app.db.models import TaskOutbox
from app.main import app
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/stage1_outbox_ops.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _row(session, op, *, status, error=None, attempts=0):
    row = TaskOutbox(
        id=str(uuid.uuid4()),
        operation=op,
        idempotency_key=f"stage1:{op}:{uuid.uuid4()}",
        payload={"probe": op},
        status=status,
        attempt_count=attempts,
        error=error,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
    )
    session.add(row)
    session.commit()
    return row


def test_outbox_list_counts_filters_and_hides_payloads(db):
    with db() as s:
        _row(s, "search", status="FAILED", error="ValueError: boom", attempts=2)
        _row(s, "search", status="DONE")
    client = TestClient(app)
    listing = client.get("/api/ops/outbox").json()
    assert listing["counts"] == {"FAILED": 1, "DONE": 1}
    failed = client.get("/api/ops/outbox", params={"status": "FAILED"}).json()
    assert len(failed["items"]) == 1
    item = failed["items"][0]
    assert item["status"] == "FAILED" and item["error"] == "ValueError: boom"
    assert item["attempts"] == 2 and "payload" not in item


def test_outbox_retry_rearms_and_publishes(db, monkeypatch):
    published: dict = {}

    def fake_dispatch(operation, payload, task_id=None):
        published.update(operation=operation, payload=payload, task_id=task_id)

    monkeypatch.setattr(dispatcher, "_dispatch", fake_dispatch)
    with db() as s:
        row = _row(s, "search", status="FAILED", error="ValueError: boom", attempts=3)
        row_id = row.id
    client = TestClient(app)
    res = client.post(f"/api/ops/outbox/{row_id}/retry")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["operation"] == "search" and body["status"] == "PENDING"
    assert published["operation"] == "search" and published["payload"] == {"probe": "search"}
    assert published["task_id"] == body["task_id"]
    with db() as s:
        after = s.get(TaskOutbox, row_id)
        assert after.status == "PENDING" and after.attempt_count == 0
        assert after.error is None and after.task_id == body["task_id"]


def test_outbox_retry_rejects_non_failed_and_missing(db):
    with db() as s:
        row = _row(s, "search", status="PENDING")
    client = TestClient(app)
    assert client.post(f"/api/ops/outbox/{row.id}/retry").status_code == 409
    assert client.post(f"/api/ops/outbox/{uuid.uuid4()}/retry").status_code == 404
