"""WebSocket compliance endpoint authentication (P0-1 fix).

Tiers, all against the saved-version path (components omitted — the exact-pct
leak vector: the server loads the saved version and echoes disclosure/trace):
- no token → socket closed 4401
- low-permission token (lab_technician: no plm.document.write) → 4403
- rd_engineer token (plm.document.write + formulation.read_exact) → exact pct
- custom doc-writer token (plm.document.write, NO formulation.read_exact) →
  banded response: pct_band present, exact pct absent
- operator token (FORMUNEX_API_TOKEN) → exact pct (service-key exemption)

Auth is enabled in this fixture (unlike test_plm_ws.py, which runs with auth
disabled and therefore still passes unchanged).
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import get_settings

PASSWORD = "pw-1234567"
OP_TOKEN = "op-token-123-for-tests"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'plm-ws-auth.db'}")
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
        # Custom role: may write PLM documents but may NOT read exact ratios.
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
            id="fv-ws", lineage_id="lin-ws", version=1, domain="coating", name="底漆 v1", snapshot={
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


def _saved_version_msg(client, token_query):
    with client.websocket_connect(f"/ws/formulations/fv-ws/compliance{token_query}") as ws:
        ws.send_json({"type": "classify", "properties": {}})
        return ws.receive_json()


def test_ws_no_token_closed_4401(api):
    with pytest.raises(WebSocketDisconnect) as exc:
        with api["client"].websocket_connect("/ws/formulations/fv-ws/compliance"):
            pass
    assert exc.value.code == 4401


def test_ws_low_permission_token_closed_4403(api):
    with pytest.raises(WebSocketDisconnect) as exc:
        with api["client"].websocket_connect(
                f"/ws/formulations/fv-ws/compliance?token={api['tokens']['lab']}"):
            pass
    assert exc.value.code == 4403


def test_ws_exact_for_read_exact(api):
    msg = _saved_version_msg(api["client"], f"?token={api['tokens']['rd']}")
    assert msg["type"] == "classification"
    rows = [r for r in msg["disclosure"] if r["name"] == "二甲苯"]
    assert rows and rows[0]["pct"] == 60 and "pct_band" not in rows[0]


def test_ws_banded_without_read_exact(api):
    msg = _saved_version_msg(api["client"], f"?token={api['tokens']['doc']}")
    assert msg["type"] == "classification"
    rows = [r for r in msg["disclosure"] if r["name"] == "二甲苯"]
    assert rows and "pct" not in rows[0] and rows[0].get("pct_band")
    for entry in msg["trace"]:
        for contributor in entry.get("contributors", []):
            assert "pct" not in contributor
    for value in msg.get("unknown_toxicity_pct", {}).values():
        assert isinstance(value, str)


def test_ws_operator_token_exact(api):
    msg = _saved_version_msg(api["client"], f"?token={OP_TOKEN}")
    assert msg["type"] == "classification"
    rows = [r for r in msg["disclosure"] if r["name"] == "二甲苯"]
    assert rows and rows[0]["pct"] == 60


def test_ws_caller_supplied_components_still_work(api):
    with api["client"].websocket_connect(
            f"/ws/formulations/new/compliance?token={api['tokens']['rd']}") as ws:
        ws.send_json({"type": "classify",
                      "components": [{"name": "二甲苯", "cas": "1330-20-7", "pct": 60},
                                     {"name": "水", "cas": "7732-18-5", "pct": 40}],
                      "properties": {"flash_point_c": 30, "initial_boiling_point_c": 140}})
        msg = ws.receive_json()
    assert msg["type"] == "classification" and msg["h_codes"]
