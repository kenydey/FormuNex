"""P1-25: REST saved-version classification redacts exact pct without read_exact.

Same redaction as the P0-1 WebSocket fix, applied to
``POST /api/plm/ghs/classify/formulation-versions/{version_id}`` (the
saved-version path — the server loads the version and echoes disclosure /
trace). Tiers, mirroring test_plm_ws_auth.py:
- doc-writer token (plm.document.write, NO formulation.read_exact) → banded
  response: pct_band present, exact pct absent — but the persisted row still
  holds exact values (computation / persistence unchanged, only the echo).
- rd_engineer token (plm.document.write + formulation.read_exact) → exact pct.
- operator token (FORMUNEX_API_TOKEN) → exact pct (service-key exemption).
- lab_technician token (no plm.document.write) → 403 via the guard.

The caller-supplied ``POST /api/plm/ghs/classify`` endpoint is deliberately
NOT redacted (its inputs are the caller's own data) and is not covered here.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

PASSWORD = "Fx-Plm-2026-ok"
OP_TOKEN = "op-token-redact"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'plm-redact.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "true")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    monkeypatch.setenv("FORMUNEX_API_TOKEN", OP_TOKEN)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion
    from app.db.models_identity import RoleRow, UserRoleRow
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as session:
        sync_builtin_roles(session)
        session.flush()
        session.add(RoleRow(code="doc_writer", name_zh="文档编写", name_en="Doc writer",
                            permissions=["knowledge.read", "formulation.read", "plm.document.write"],
                            builtin=False))
        session.flush()
        xylene = SubstanceRow(id=str(uuid.uuid4()), cas_no="1330-20-7", name_zh="二甲苯", name_en="xylene",
                              aliases=[], hazardous_catalog_no=2289)
        session.add(xylene)
        session.flush()
        session.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=xylene.id,
                                               hazard_class="flammable_liquid", category="3", h_code="H226",
                                               source="cn_catalog"))
        session.add(FormulationVersion(
            id="fv-red", lineage_id="lin-red", version=1, domain="coating", name="底漆 v1", snapshot={
                "ingredients": [{"name": "xylene", "zh_name": "二甲苯", "cas_no": "1330-20-7", "weight_pct": 60},
                                {"name": "water", "cas_no": "7732-18-5", "weight_pct": 40}]},
            created_at=datetime(2026, 10, 1)))
        doc = accounts.create_user(session, username="doc", password=PASSWORD, roles=None)
        session.flush()
        session.add(UserRoleRow(user_id=doc.id, role_code="doc_writer"))
        for name, roles in (("rd", ["rd_engineer"]), ("lab", ["lab_technician"])):
            accounts.create_user(session, username=name, password=PASSWORD, roles=roles)
        session.commit()
    from app.main import app

    client = TestClient(app)
    tokens = {}
    for name in ("rd", "lab", "doc"):
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = r.json()["access_token"]
    yield {"client": client, "tokens": tokens}
    get_settings.cache_clear()


def _classify(api, headers):
    r = api["client"].post("/api/plm/ghs/classify/formulation-versions/fv-red",
                           headers=headers,
                           json={"properties": {"flash_point_c": 35, "initial_boiling_point_c": 140}})
    assert r.status_code == 200, r.text
    return r.json()


def test_version_classify_banded_without_read_exact(api):
    body = _classify(api, {"Authorization": "Bearer " + api["tokens"]["doc"]})
    assert body["h_codes"] and "H226" in body["h_codes"]
    rows = body["disclosure"]
    assert rows and "pct" not in rows[0] and rows[0].get("pct_band")
    for entry in body["trace"]:
        for contributor in entry.get("contributors", []):
            assert "pct" not in contributor
    for value in body["unknown_toxicity_pct"].values():
        assert isinstance(value, str)  # banded, not a number
    # Persistence is unchanged: the stored row still holds exact values.
    from app.db.database import default_session_factory
    from app.db.models_plm import ClassificationResultRow

    with default_session_factory()() as session:
        row = session.get(ClassificationResultRow, body["classification_id"])
        assert row is not None and row.formulation_version_id == "fv-red"
        assert row.result["disclosure"] and row.result["disclosure"][0]["pct"] == 60


def test_version_classify_exact_with_read_exact(api):
    body = _classify(api, {"Authorization": "Bearer " + api["tokens"]["rd"]})
    rows = body["disclosure"]
    assert rows and rows[0]["pct"] == 60 and "pct_band" not in rows[0]


def test_version_classify_exact_for_operator_token(api):
    body = _classify(api, {"Authorization": "Bearer " + OP_TOKEN})
    rows = body["disclosure"]
    assert rows and rows[0]["pct"] == 60


def test_version_classify_gate_unchanged(api):
    r = api["client"].post("/api/plm/ghs/classify/formulation-versions/fv-red",
                           headers={"Authorization": "Bearer " + api["tokens"]["lab"]}, json={})
    assert r.status_code == 403
    assert api["client"].post("/api/plm/ghs/classify/formulation-versions/nope",
                              headers={"Authorization": "Bearer " + api["tokens"]["doc"]},
                              json={}).status_code == 404
