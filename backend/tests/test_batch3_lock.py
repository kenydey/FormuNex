"""Batch 3 (P1-C1): the EHS substance-lock endpoint is the production path
the release gate's key-component check waits for."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

PASSWORD = "Fx-Lock-2026"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'lock.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    from app.config import get_settings

    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        ids = {name: accounts.create_user(s, username=name, password=PASSWORD, roles=roles).id
               for name, roles in {"ehs": ["ehs_regulatory"], "rd": ["rd_engineer"]}.items()}
        sub = SubstanceRow(id=str(uuid.uuid4()), cas_no="1330-20-7", name_zh="二甲苯",
                           name_en="xylene", aliases=[])
        s.add(sub)
        s.flush()  # parent first: same-flush parent/child insert order trips FK
        s.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=sub.id,
                                         hazard_class="flammable_liquid", category="3", h_code="H226",
                                         source="manual"))
        s.commit()
        sub_id = sub.id
    from app.main import app

    client = TestClient(app)
    tokens = {}
    for name in ("ehs", "rd"):
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield {"client": client, "h": tokens, "sub_id": sub_id, "ids": ids}
    get_settings.cache_clear()


def _rows(env):
    from app.db.database import default_session_factory
    from app.db.models_regulatory import SubstanceClassificationRow

    with default_session_factory()() as s:
        return list(s.execute(select(SubstanceClassificationRow).where(
            SubstanceClassificationRow.substance_id == env["sub_id"])).scalars().all())


def test_ehs_locks_existing_manual_rows(env):
    r = env["client"].post(f"/api/plm/substances/{env['sub_id']}/lock",
                           json={"password": PASSWORD}, headers=env["h"]["ehs"])
    assert r.status_code == 200, r.text
    assert r.json()["locked_rows"] == 1
    rows = _rows(env)
    assert rows[0].locked_at is not None and rows[0].locked_by == env["ids"]["ehs"]


def test_lock_with_classifications_creates_and_locks_rows(env):
    from app.db.database import default_session_factory
    from app.db.models_regulatory import SubstanceRow

    with default_session_factory()() as s:
        sub2 = SubstanceRow(id=str(uuid.uuid4()), cas_no="71-43-2", name_zh="苯", name_en="benzene",
                            aliases=[])
        s.add(sub2)
        s.commit()
        sub2_id = sub2.id
    r = env["client"].post(f"/api/plm/substances/{sub2_id}/lock",
                           json={"password": PASSWORD,
                                 "classifications": [{"hazard_class": "carcinogenicity", "category": "1A",
                                                      "h_code": "H350"}]},
                           headers=env["h"]["ehs"])
    assert r.status_code == 200, r.text
    assert r.json()["locked_rows"] == 1


def test_lock_without_manual_rows_is_rejected(env):
    from app.db.database import default_session_factory
    from app.db.models_regulatory import SubstanceRow

    with default_session_factory()() as s:
        sub2 = SubstanceRow(id=str(uuid.uuid4()), cas_no="71-43-2", name_zh="苯", name_en="benzene",
                            aliases=[])
        s.add(sub2)
        s.commit()
        sub2_id = sub2.id
    r = env["client"].post(f"/api/plm/substances/{sub2_id}/lock",
                           json={"password": PASSWORD}, headers=env["h"]["ehs"])
    assert r.status_code == 422


def test_non_ehs_cannot_lock(env):
    r = env["client"].post(f"/api/plm/substances/{env['sub_id']}/lock",
                           json={"password": PASSWORD}, headers=env["h"]["rd"])
    assert r.status_code == 403
    assert _rows(env)[0].locked_at is None


def test_wrong_password_does_not_lock(env):
    r = env["client"].post(f"/api/plm/substances/{env['sub_id']}/lock",
                           json={"password": "wrong"}, headers=env["h"]["ehs"])
    assert r.status_code == 401
    assert _rows(env)[0].locked_at is None


def test_locked_row_cannot_be_overwritten(env):
    c = env["client"]
    assert c.post(f"/api/plm/substances/{env['sub_id']}/lock",
                  json={"password": PASSWORD}, headers=env["h"]["ehs"]).status_code == 200
    r = c.post(f"/api/plm/substances/{env['sub_id']}/lock",
               json={"password": PASSWORD,
                     "classifications": [{"hazard_class": "flammable_liquid", "category": "2",
                                          "h_code": "H225"}]},
               headers=env["h"]["ehs"])
    assert r.status_code == 409
    assert _rows(env)[0].category == "3"
