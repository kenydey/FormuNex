"""PLM audit-log tamper detection through the API.

Mirrors test_identity_audit.test_tampering_is_detected_when_trigger_is_bypassed
(generic chain) but exercises the PLM surface: a real PLM record
(``release.publish``) is appended, tampered with the append-only trigger
dropped, and the tamper must surface via ``GET /api/plm/audit`` as
``chain_ok=False`` with the correct ``first_bad_seq``.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings  # noqa: E402

PASSWORD = "Formula-2026!"
USERS = {"rm": ["release_manager"], "admin": ["sys_admin"]}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'plm_audit.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.services.audit import hash_chain
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        ids = {name: accounts.create_user(s, username=name, password=PASSWORD, roles=roles).id
               for name, roles in USERS.items()}
        # Three PLM audit records, the middle one a release publish.
        hash_chain.append(s, actor=ids["rm"], action="document.generate", entity_type="plm_document",
                          entity_id="doc-1", detail={"doc_type": "sds"})
        hash_chain.append(s, actor=ids["rm"], action="release.publish", entity_type="release_package",
                          entity_id="pkg-1", detail={"version": "A.1"})
        hash_chain.append(s, actor=ids["admin"], action="document.approve", entity_type="plm_document",
                          entity_id="doc-1", detail={"meaning": "approve"})
        s.commit()
    from app.main import app

    client = TestClient(app)
    tokens = {}
    for name in USERS:
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield {"client": client, "h": tokens}
    get_settings.cache_clear()


def _audit(env, who="rm"):
    r = env["client"].get("/api/plm/audit", headers=env["h"][who])
    assert r.status_code == 200, r.text
    return r.json()


def test_plm_audit_chain_ok_before_tamper(env):
    body = _audit(env)
    assert body["chain_ok"] is True and body["first_bad_seq"] is None
    # User creation also appends audit rows, so the chain holds more than the
    # three PLM records this fixture wrote — assert a lower bound, not equality.
    assert body["chain_rows"] >= 3
    actions = [rec["action"] for rec in body["records"]]
    assert "release.publish" in actions


def _publish_seq() -> int:
    from app.db.database import default_session_factory

    with default_session_factory()() as s:
        return int(s.execute(
            text("SELECT seq FROM audit_log WHERE action = 'release.publish'")).scalar_one())


def test_plm_audit_tamper_detected_via_api(env):
    from app.db.database import default_session_factory
    from app.services.audit import hash_chain

    target = _publish_seq()
    # Someone with DDL rights drops the append-only trigger and edits a row;
    # the hash chain must still expose the edit through the PLM API.
    with default_session_factory()() as s:
        s.execute(text("DROP TRIGGER IF EXISTS audit_log_no_update"))
        s.execute(text("UPDATE audit_log SET actor = 'mallory' WHERE seq = :seq"), {"seq": target})
        s.commit()
        check = hash_chain.verify_chain(s)
        assert not check.ok and check.first_bad_seq == target

    body = _audit(env)
    assert body["chain_ok"] is False
    assert body["first_bad_seq"] == target
    assert body["chain_rows"] == target - 1  # verify_chain stops at the first bad row


def test_plm_audit_tamper_of_publish_record_is_visible(env):
    from app.db.database import default_session_factory

    target = _publish_seq()
    with default_session_factory()() as s:
        s.execute(text("DROP TRIGGER IF EXISTS audit_log_no_update"))
        s.execute(text("UPDATE audit_log SET detail = '{\"version\": \"A.9\"}' WHERE action = 'release.publish'"))
        s.commit()

    body = _audit(env)
    assert body["chain_ok"] is False
    assert body["first_bad_seq"] == target
