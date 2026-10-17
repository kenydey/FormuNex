"""Pending MCP approvals carry the tool-call arguments, with secret-looking values redacted."""
from __future__ import annotations

import sqlite3
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.mcp_approvals import router
from app.services import mcp_approval


def _client(tmp_path, monkeypatch):
    db = tmp_path / "mcp_approval.db"
    monkeypatch.setattr(mcp_approval, "_default_db_path", lambda: db)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), db


def test_redact_arguments_recursive():
    out = mcp_approval.redact_arguments({
        "query": "zinc", "api_key": "sk-1", "Authorization": "Bearer x",
        "nested": {"password": "p", "items": [{"access_token": "t", "n": 1}]}, "limit": 5,
    })
    assert out == {"query": "zinc", "api_key": "***", "Authorization": "***",
                   "nested": {"password": "***", "items": [{"access_token": "***", "n": 1}]}, "limit": 5}


def test_pending_includes_redacted_arguments(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    mcp_approval.evaluate_call("srv", "write_file", path=db, now=time.monotonic(),
                               arguments={"path": "/tmp/a.txt", "secret": "s3cr3t", "headers": {"Authorization": "x"}})
    items = client.get("/api/mcp/approvals/pending").json()["items"]
    assert len(items) == 1
    assert items[0]["arguments"] == {"path": "/tmp/a.txt", "secret": "***", "headers": {"Authorization": "***"}}
    # the secret is never persisted
    with sqlite3.connect(db) as conn:
        raw = conn.execute("SELECT arguments_json FROM mcp_approval_requests").fetchone()[0]
    assert "s3cr3t" not in raw


def test_retry_updates_arguments_and_none_when_absent(tmp_path, monkeypatch):
    client, db = _client(tmp_path, monkeypatch)
    now = time.monotonic()
    mcp_approval.evaluate_call("srv", "t1", path=db, now=now, arguments={"q": "first"})
    mcp_approval.evaluate_call("srv", "t1", path=db, now=now, arguments={"q": "second"})
    mcp_approval.evaluate_call("srv", "t2", path=db, now=now)
    items = {i["tool_name"]: i for i in client.get("/api/mcp/approvals/pending").json()["items"]}
    assert items["t1"]["arguments"] == {"q": "second"}
    assert items["t2"]["arguments"] is None


def test_old_store_gets_column(tmp_path, monkeypatch):
    db = tmp_path / "mcp_approval.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE mcp_approval_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, server_id TEXT NOT NULL, "
            "tool_name TEXT NOT NULL, session_id TEXT, project_id TEXT, status TEXT NOT NULL DEFAULT 'pending', "
            "requested_at REAL NOT NULL, decided_at REAL)")
        conn.execute("INSERT INTO mcp_approval_requests (server_id, tool_name, requested_at) VALUES ('s', 't', ?)",
                     (time.monotonic(),))
    client, _ = _client(tmp_path, monkeypatch)
    items = client.get("/api/mcp/approvals/pending").json()["items"]
    assert items[0]["arguments"] is None
