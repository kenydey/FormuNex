"""Tests for the MCP approval REST API (W3-11).

Phase 2D (2-24): the service reads the main database through
``default_session_factory``, so the API test points the real app at a
temporary SQLite database via ``FORMUNEX_DB_URL`` (same pattern as the
PLM lifecycle e2e ``stack`` fixture) instead of a standalone approval DB.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import mcp_approval


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'mcp_approval_api.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_KG_ENABLED", "false")
    get_settings.cache_clear()
    from app.main import app

    yield TestClient(app, raise_server_exceptions=False)
    get_settings.cache_clear()


def _seed_pending():
    # default policy is ask -> evaluate_call creates a pending request;
    # requested_at uses the monotonic clock, so seed with a fresh monotonic now
    res = mcp_approval.evaluate_call("srv1", "tool_a", session_id="s1", now=time.monotonic())
    assert res["verdict"] == "deny" and res["reason"] == "approval_required"
    return res["request_id"]


def test_pending_lists_request(client):
    _seed_pending()
    r = client.get("/api/mcp/approvals/pending")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["server_id"] == "srv1" and item["tool_name"] == "tool_a"
    assert item["session_id"] == "s1"


def test_decide_allow_persists_policy(client):
    rid = _seed_pending()
    r = client.post(f"/api/mcp/approvals/{rid}/decide", json={"decision": "allow", "scope": "session"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # second decide on same request -> 404 (not pending anymore)
    r2 = client.post(f"/api/mcp/approvals/{rid}/decide", json={"decision": "deny"})
    assert r2.status_code == 404


def test_decide_missing_request_404(client):
    r = client.post("/api/mcp/approvals/does-not-exist/decide", json={"decision": "deny"})
    assert r.status_code == 404


def test_decide_invalid_payload_422(client):
    r = client.post("/api/mcp/approvals/does-not-exist/decide", json={"decision": "maybe", "scope": "once"})
    assert r.status_code == 422
    r = client.post("/api/mcp/approvals/does-not-exist/decide", json={"decision": "allow", "scope": "forever"})
    assert r.status_code == 422
