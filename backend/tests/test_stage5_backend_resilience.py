"""Stage 5 / 5-2: backend tolerance and error traceability.

SR-085 (clock), SR-088 (record failure fails dispatch), SR-045 (5xx
generic detail), SR-096 (OverflowError by range, not by message text),
SR-097 (422 log on every route), SR-095 (dispatch on stdlib logging).
SR-087 (idempotency key ordering) lives in test_stage5_idempotency.py
once the payload field inventory lands.
"""
from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api import _dispatch
from app.main import app, integer_out_of_range_handler
from app.services import dispatch as _dispatch_kernel
from app.services.supplier_normalize import _parse_datetime

client = TestClient(app, raise_server_exceptions=False)


# ── SR-085: aware datetimes convert to UTC before dropping tzinfo ─────

def test_parse_datetime_converts_offset_to_utc():
    assert _parse_datetime("2026-01-01T08:00:00+08:00").isoformat() == "2026-01-01T00:00:00"
    assert _parse_datetime("2026-01-01T08:00:00Z").isoformat() == "2026-01-01T08:00:00"
    assert _parse_datetime("2026-01-01").isoformat() == "2026-01-01T00:00:00"
    assert _parse_datetime("not a date") is None
    assert _parse_datetime(None) is None


# ── SR-096: OverflowError mapped by range, not by message text ─────────

def _request(path_params=None, query_string=b""):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {"type": "http", "method": "GET", "path": "/x", "headers": [],
         "query_string": query_string, "path_params": path_params or {}},
        receive,
    )


def test_huge_path_param_overflow_is_404():
    resp = asyncio.run(integer_out_of_range_handler(
        _request(path_params={"id": 10**30}), OverflowError("boom")))
    assert resp.status_code == 404
    assert resp.body == b'{"detail":"Not found"}'


def test_huge_query_param_overflow_is_404():
    resp = asyncio.run(integer_out_of_range_handler(
        _request(query_string=b"id=99999999999999999999999999"),
        OverflowError("boom")))
    assert resp.status_code == 404


def test_ordinary_overflow_still_reraises():
    with pytest.raises(OverflowError):
        asyncio.run(integer_out_of_range_handler(
            _request(path_params={"id": 7}), OverflowError("math range error")))
    with pytest.raises(OverflowError):
        asyncio.run(integer_out_of_range_handler(None, OverflowError("math range error")))  # type: ignore[arg-type]


# ── SR-097: every route's 422 is debug-logged (path + field + type) ────

def test_422_on_non_projects_route_is_debug_logged(caplog):
    with caplog.at_level(logging.DEBUG, logger="app.main"):
        resp = client.post("/api/session-plans", json={})
    assert resp.status_code == 422
    recs = [r for r in caplog.records if "validation failed" in r.message]
    assert recs, "no 422 log record for a non-/api/projects route"
    assert "/api/session-plans" in recs[0].message
    # No body values in the summary: only loc + type.
    assert "session_id" in recs[0].message or "phases" in recs[0].message


# ── SR-088: a failed outbox record fails the dispatch ──────────────────

class _FakeTask:
    def __init__(self, events):
        self.events = events

    def apply_async(self, args=None, task_id=None, **_):
        self.events.append(("publish", task_id))

    def delay(self, *_args, **_kwargs):
        raise AssertionError("publish must go through apply_async")


def test_record_failure_is_a_503_with_no_publish(monkeypatch):
    monkeypatch.setenv("FORMUNEX_CELERY_EAGER", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(_dispatch_kernel, "broker_reachable", lambda: True)
    monkeypatch.setattr(_dispatch_kernel, "_record_outbox_task_id", lambda o, t: False)
    events: list = []
    with pytest.raises(HTTPException) as exc_info:
        _dispatch.submit(_FakeTask(events), {"k": "v"}, "doe_cycle", outbox_id="row-1")
    assert exc_info.value.status_code == 503
    assert events == [], "nothing may publish when the id never landed"


# ── SR-095: dispatch logs through stdlib (visible to caplog) ───────────

def test_dispatch_logger_is_stdlib_and_caplog_visible(caplog):
    assert isinstance(_dispatch_kernel.logger, logging.Logger)
    with caplog.at_level(logging.WARNING, logger="app.services.dispatch"):
        _dispatch_kernel.logger.warning("probe-test marker")
    assert any("probe-test marker" in r.message for r in caplog.records)


# ── SR-045: 5xx answers carry a generic detail, never the exception ────

def test_surechembl_ingest_500_hides_the_exception(monkeypatch):
    from app.services import surechembl_kg

    def boom(**_kwargs):
        raise RuntimeError("/tmp/secret/path exploded: third-party body")

    monkeypatch.setattr(surechembl_kg, "ingest_document_graph", boom)
    resp = client.post("/api/surechembl/kg/ingest-document", json={
        "doc_id": "abc", "title": "t",
    })
    assert resp.status_code == 500
    assert resp.json() == {"detail": "SureChEMBL KG 入库失败"}
    assert "secret" not in resp.text
