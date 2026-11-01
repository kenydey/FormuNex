"""Stage 2 / SR-043: admin deactivation/activation + session revocation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import identity as identity_api
from app.api._principal import CurrentUser
from app.db.database import default_session_factory
from app.db.models_identity import UserRow, UserSessionRow
from app.services.identity import accounts
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/stage2_admin.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    with default_session_factory()() as s:
        for uid in ("admin", "victim"):
            s.add(UserRow(id=uid, username=uid, display_name=uid, is_active=True))
        for sid in ("s1", "s2"):
            s.add(UserSessionRow(id=sid, user_id="victim", token_hash=sid * 32,
                                 expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        s.commit()
    yield


def _admin(can: bool = True) -> CurrentUser:
    principal = SimpleNamespace(can=lambda permission, project_id=None: can)
    return CurrentUser(id="admin", username="admin", display_name="admin",
                       session_id="adm-sess", principal=principal)


def test_deactivate_revokes_sessions_and_blocks_login(db):
    from app.services.identity import tokens

    with default_session_factory()() as s:
        accounts.set_active(s, "victim", False, actor="admin")
        s.commit()
    with default_session_factory()() as s:
        victim = s.get(UserRow, "victim")
        assert victim.is_active is False
        live = [r for r in s.query(UserSessionRow).filter_by(user_id="victim")
                if r.revoked_at is None]
        assert live == []
        # A fresh, unrevoked session row still cannot authenticate: the
        # is_active gate fires before any token use succeeds.
        s.add(UserSessionRow(id="s3", user_id="victim", token_hash="s3" * 32,
                             expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        s.commit()
        tok, _ttl = tokens.issue_access_token("victim", "s3")
        with pytest.raises(accounts.AuthError, match="停用"):
            accounts.active_user_for_token(s, tok)


def test_activate_does_not_restore_revoked_sessions(db):
    with default_session_factory()() as s:
        accounts.set_active(s, "victim", False, actor="admin")
        accounts.set_active(s, "victim", True, actor="admin")
        s.commit()
    with default_session_factory()() as s:
        assert s.get(UserRow, "victim").is_active is True
        assert all(r.revoked_at is not None
                   for r in s.query(UserSessionRow).filter_by(user_id="victim"))


def test_self_deactivate_and_self_revoke_refused(db):
    with default_session_factory()() as s:
        with pytest.raises(accounts.AccountError, match="自身"):
            accounts.set_active(s, "admin", False, actor="admin")
        with pytest.raises(accounts.AccountError, match="自身"):
            accounts.revoke_user_sessions(s, "admin", actor="admin")


def test_routes_require_admin_users(db):
    with pytest.raises(HTTPException, match="403"):
        identity_api.set_user_active("victim", identity_api.ActiveIn(is_active=False),
                                     _admin(can=False))
    out = identity_api.set_user_active("victim", identity_api.ActiveIn(is_active=False), _admin())
    assert out == {"user_id": "victim", "is_active": False}
    out = identity_api.revoke_user_sessions("victim", _admin())
    assert out == {"user_id": "victim", "revoked": 0}  # already revoked by deactivation
