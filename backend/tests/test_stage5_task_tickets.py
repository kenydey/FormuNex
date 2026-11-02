"""Stage 5 / SR-081: one-time task-stream tickets replace the bearer token in URLs."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.middleware import api_auth

TOKEN = "ticket-walk-secret"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_TOKEN", TOKEN)
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "true")
    return TestClient(app, raise_server_exceptions=False)


def _mint(client, task_id="t-1"):
    r = client.post(f"/api/tasks/{task_id}/ticket", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["expires_in"] == 300 and len(body["ticket"]) >= 32
    return body["ticket"]


def test_ticket_opens_the_stream_once_then_burns(client):
    ticket = _mint(client)
    # Unknown task: past the gate, the endpoint answers 404 — not 401.
    assert client.get("/api/tasks/t-1/stream", params={"ticket": ticket}).status_code != 401
    # Single-use: the second presentation is refused at the gate.
    assert client.get("/api/tasks/t-1/stream", params={"ticket": ticket}).status_code == 401


def test_ticket_is_bound_to_its_task(client):
    ticket = _mint(client, "t-1")
    assert client.get("/api/tasks/t-2/stream", params={"ticket": ticket}).status_code == 401


def test_ticket_mint_needs_header_auth(client):
    assert client.post("/api/tasks/t-1/ticket").status_code == 401
    assert client.post("/api/tasks/t-1/ticket", params={"ticket": _mint(client)}).status_code == 401


def test_expired_and_unknown_tickets_are_refused():
    assert api_auth.consume_task_ticket("no-such-ticket", "t-1") is None
    ticket = api_auth.mint_task_ticket("t-1", "default", ttl_s=0)
    assert api_auth.consume_task_ticket(ticket, "t-1") is None


def test_log_filter_redacts_the_ticket():
    import logging

    from app.main import _RedactQueryToken

    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", "/api/tasks/abc/stream?ticket=live-ticket-value&x=1", "1.1", 200), None)
    assert _RedactQueryToken().filter(record) is True
    msg = record.getMessage()
    assert "live-ticket-value" not in msg and "ticket=***" in msg
