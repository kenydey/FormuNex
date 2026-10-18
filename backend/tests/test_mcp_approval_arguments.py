"""Pending MCP approvals carry the tool-call arguments, with secret-looking values redacted.

Phase 2D (2-24): approval state lives in the main database; the tests
point the service at a temporary factory instead of a standalone SQLite file.
The old-store column migration test is gone with the old store: the schema
is alembic-managed (0062) and ``arguments_json`` exists from the first version.
"""
from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.mcp_approvals import router
from app.db.database import Base, make_engine, make_session_factory
from app.db.models import McpApprovalRequestRow
from app.services import mcp_approval


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path}/mcp_args.db")
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    monkeypatch.setattr(mcp_approval, "_session_factory", lambda _sf=None: sf)
    app = FastAPI()
    app.include_router(router)
    yield TestClient(app, raise_server_exceptions=False), sf
    engine.dispose()


def test_redact_arguments_recursive():
    out = mcp_approval.redact_arguments({
        "query": "zinc", "api_key": "sk-1", "Authorization": "Bearer x",
        "nested": {"password": "p", "items": [{"access_token": "t", "n": 1}]}, "limit": 5,
    })
    assert out == {"query": "zinc", "api_key": "***", "Authorization": "***",
                   "nested": {"password": "***", "items": [{"access_token": "***", "n": 1}]}, "limit": 5}


def test_pending_includes_redacted_arguments(client):
    c, sf = client
    mcp_approval.evaluate_call("srv", "write_file", now=time.monotonic(),
                               arguments={"path": "/tmp/a.txt", "secret": "s3cr3t", "headers": {"Authorization": "x"}})
    items = c.get("/api/mcp/approvals/pending").json()["items"]
    assert len(items) == 1
    assert items[0]["arguments"] == {"path": "/tmp/a.txt", "secret": "***", "headers": {"Authorization": "***"}}
    # the secret is never persisted
    with sf() as session:
        row = session.query(McpApprovalRequestRow).one()
        assert "s3cr3t" not in (row.arguments_json or "")


def test_retry_updates_arguments_and_none_when_absent(client):
    c, _sf = client
    now = time.monotonic()
    mcp_approval.evaluate_call("srv", "t1", now=now, arguments={"q": "first"})
    mcp_approval.evaluate_call("srv", "t1", now=now, arguments={"q": "second"})
    mcp_approval.evaluate_call("srv", "t2", now=now)
    items = {i["tool_name"]: i for i in c.get("/api/mcp/approvals/pending").json()["items"]}
    assert items["t1"]["arguments"] == {"q": "second"}
    assert items["t2"]["arguments"] is None
