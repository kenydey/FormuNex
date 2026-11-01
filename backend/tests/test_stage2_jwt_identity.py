"""Stage 2: JWT identity in sensitive reads, plan approval, audit actor.

2-4 / SR-011: a revoked JWT must not degrade chat attribution to the
unchecked legacy path. 2-5 / SR-040: plan approval is decided by the
signed-in account, never a client-supplied actor. 2-6 / SR-042: the
audit middleware attributes JWT callers by login id.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api import session_plans as plans_api
from app.api.chat import _chat_caller_id
from app.db.database import default_session_factory
from app.db.models_identity import UserRow, UserSessionRow
from app.middleware.audit import _actor
from app.services import session_plan as svc
from app.services.identity import tokens
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/stage2_jwt.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    monkeypatch.setattr(svc, "_data_root", lambda: tmp_path)
    with default_session_factory()() as s:
        s.add(UserRow(id="u1", username="u1", display_name="u1", is_active=True))
        s.add(UserSessionRow(id="s1", user_id="u1", token_hash="b" * 64,
                             expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        s.commit()
    app = FastAPI()
    app.include_router(plans_api.router)
    yield TestClient(app)


def _request(token: str | None) -> Request:
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    return Request({"type": "http", "method": "GET", "path": "/api/chat",
                    "headers": headers, "query_string": b""})


def _token():
    tok, _ttl = tokens.issue_access_token("u1", "s1")
    return tok


def test_chat_caller_id_rejects_revoked_jwt_but_keeps_service_keys(db):
    assert _chat_caller_id(_request(None)) is None
    assert _chat_caller_id(_request("test-secret-token")) is None
    assert _chat_caller_id(_request(_token())) == "u1"
    with default_session_factory()() as s:
        s.get(UserSessionRow, "s1").revoked_at = datetime.now(timezone.utc)
        s.commit()
    with pytest.raises(HTTPException, match="401"):
        _chat_caller_id(_request(_token()))


def test_plan_decide_uses_jwt_id_not_body_actor(db):
    plan = svc.submit_plan("s1", [{"name": "design", "steps": ["a"]}])
    r = db.post(f"/api/session-plans/{plan.plan_id}/decide",
                json={"approved": True, "actor": "spoofed"},
                headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 200, r.text
    assert r.json()["decided_by"] == "u1"


def test_plan_decide_requires_login(db):
    plan = svc.submit_plan("s1", [{"name": "design", "steps": ["a"]}])
    r = db.post(f"/api/session-plans/{plan.plan_id}/decide", json={"approved": True})
    assert r.status_code == 401


def test_audit_actor_is_jwt_login_id(db):
    assert _actor(_request(_token())) == "u1"
    assert _actor(_request(None)) == "anonymous"
    assert _actor(_request("test-secret-token")) == "default"
