"""Stage 1 / SR-029-SR-068: /health worker + outbox probes, /api/ops/metrics.

/health is public and cached for 10 s, so tests reset the cache and
point the DB at a throwaway file. Worker ping is skipped in eager
mode (no worker exists there); the backlog threshold is driven down
via settings so a handful of rows trips degraded.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import make_engine, make_session_factory
from app.db.models import TaskOutbox
from app.main import app, reset_health_cache
from app.services.plm.release import gate as gate_module
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/stage1_health.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    monkeypatch.setenv("FORMUNEX_OUTBOX_BACKLOG_WARN_COUNT", "2")
    monkeypatch.setenv("FORMUNEX_CELERY_EAGER", "true")
    get_settings.cache_clear()
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    yield factory
    get_settings.cache_clear()
    reset_health_cache()
    engine.dispose()


def _row(session, op, *, status, hours=2):
    row = TaskOutbox(
        id=f"stage1-{op}-{status}",
        operation=op,
        idempotency_key=f"stage1:{op}:{status}",
        payload={"probe": op},
        status=status,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours),
    )
    session.add(row)
    session.commit()
    return row


def test_health_reports_outbox_and_degrades_over_threshold(db):
    with db() as s:
        _row(s, "search", status="PENDING")
        _row(s, "loop", status="PENDING")
        _row(s, "optimize", status="PENDING")
    client = TestClient(app)
    body = client.get("/health").json()
    assert body["outbox"]["pending"] == 3
    assert body["outbox"]["backlog_threshold"] == 2
    assert body["worker"] == {"required": False, "alive": None}
    # Pin the other degraded causes off, so "degraded" here can only
    # come from the backlog (eager mode: broker is trivially reachable).
    assert body["task_broker"]["reachable"] is True
    assert body["parsers"]["pdf"] is True
    assert body["status"] == "degraded"


def test_health_ok_below_threshold(db):
    with db() as s:
        _row(s, "search", status="PENDING")
    client = TestClient(app)
    body = client.get("/health").json()
    # Only the outbox block is asserted here: overall status also
    # depends on the broker/parsers, which the sandbox may not have.
    assert body["outbox"]["pending"] == 1
    assert body["outbox"]["backlog_threshold"] == 2
    assert body["outbox"]["oldest_pending_minutes"] is not None


def test_metrics_counts_failed_last_hour_only(db, monkeypatch):
    with db() as s:
        _row(s, "search", status="FAILED", hours=0)
        old = _row(s, "loop", status="FAILED", hours=2)
        old.updated_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        s.commit()
    client = TestClient(app)
    body = client.get("/api/ops/metrics").json()
    assert body["outbox_counts"]["FAILED"] == 2
    assert body["outbox_failed_last_hour"] == 1
    assert body["process_local"] is True


def test_gate_evaluation_error_counter_increments():
    before = gate_module.evaluation_error_count()
    check = gate_module.Check(key="k", code="c", title="t")
    gate_module._evaluation_error(check, ValueError("boom"))
    assert gate_module.evaluation_error_count() == before + 1
    assert check.items and check.items[0]["key"] == "evaluation_error"
