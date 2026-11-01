"""Stage 2 / SR-014 (D-3): task ownership prefers the JWT login id."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.db.database import default_session_factory
from app.db.models_identity import UserRow, UserSessionRow
from app.middleware.api_auth import assert_owner, request_owner
from app.services.identity import tokens
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/stage2_owner.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    with default_session_factory()() as s:
        s.add(UserRow(id="u1", username="u1", display_name="u1", is_active=True))
        s.add(UserSessionRow(id="s1", user_id="u1", token_hash="a" * 64,
                             expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        s.commit()
    yield db_url


def _request(token: str | None) -> Request:
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    return Request({"type": "http", "method": "GET", "path": "/api/tasks",
                    "headers": headers, "query_string": b""})


def test_jwt_owner_is_the_login_id(db):
    tok, _ttl = tokens.issue_access_token("u1", "s1")
    assert request_owner(_request(tok)) == "u1"


def test_revoked_jwt_falls_back_to_default(db):
    tok, _ttl = tokens.issue_access_token("u1", "s1")
    with default_session_factory()() as s:
        row = s.get(UserSessionRow, "s1")
        row.revoked_at = datetime.now(timezone.utc)
        s.commit()
    assert request_owner(_request(tok)) == "default"


def test_service_key_keeps_mapping_owner(db):
    assert request_owner(_request("test-secret-token")) == "default"
    assert request_owner(_request(None)) == "default"


def test_assert_owner_blocks_other_users_but_not_legacy_rows():
    with pytest.raises(HTTPException, match="403"):
        assert_owner("u1", "u2")
    assert_owner("u1", "u1") is None
    assert_owner("default", "u2") is None
    assert_owner(None, "u2") is None
