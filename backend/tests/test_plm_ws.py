"""WebSocket real-time compliance: /ws/formulations/{id}/compliance.

Covers the protocol (classify / ping / error frames), parity with the REST
response structure, the saved-version path (components omitted), and the
guarantee that the socket never persists anything.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'plm-ws.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow

    with default_session_factory()() as session:
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
        session.commit()
    from app.main import app

    yield TestClient(app)
    get_settings.cache_clear()


def test_ws_classify_matches_rest_structure(api):
    rest = api.post("/api/plm/ghs/classify", json={
        "components": [{"name": "二甲苯", "cas": "1330-20-7", "pct": 60}, {"name": "水", "cas": "7732-18-5", "pct": 40}],
        "properties": {"flash_point_c": 30, "initial_boiling_point_c": 140}}).json()
    with api.websocket_connect("/ws/formulations/new/compliance") as ws:
        ws.send_json({"type": "classify",
                      "components": [{"name": "二甲苯", "cas": "1330-20-7", "pct": 60},
                                     {"name": "水", "cas": "7732-18-5", "pct": 40}],
                      "properties": {"flash_point_c": 30, "initial_boiling_point_c": 140}})
        msg = ws.receive_json()
    assert msg["type"] == "classification"
    # Same response structure as REST (minus nothing, plus the WS frame type).
    for key in ("hazards", "pictograms", "signal_word", "h_codes", "p_codes", "disclosure",
                "transport", "inputs_hash", "rule_set_version", "statements", "trace", "data_gaps"):
        assert key in msg, f"missing key {key}"
    assert msg["hazards"] == rest["hazards"] and msg["inputs_hash"] == rest["inputs_hash"]
    assert msg["h_codes"] == rest["h_codes"]


def test_ws_ping_and_error_frames_keep_socket_open(api):
    with api.websocket_connect("/ws/formulations/new/compliance") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}
        ws.send_json({"type": "explode"})
        err = ws.receive_json()
        assert err["type"] == "error" and "未知消息类型" in err["detail"]
        ws.send_json({"type": "classify", "components": [{"name": "水", "cas": "7732-18-5", "pct": 100}]})
        ok = ws.receive_json()
        assert ok["type"] == "classification" and ok["classified"] is False
        # Invalid payload: error frame, socket stays open.
        ws.send_json({"type": "classify", "components": []})
        err = ws.receive_json()
        assert err["type"] == "error"
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_ws_loads_saved_version_when_components_omitted(api):
    with api.websocket_connect("/ws/formulations/fv-ws/compliance") as ws:
        ws.send_json({"type": "classify", "properties": {"flash_point_c": 35, "initial_boiling_point_c": 140}})
        msg = ws.receive_json()
    assert msg["type"] == "classification"
    assert sorted(h["category"] for h in msg["hazards"] if h["hazard_class"] == "flammable_liquid") == ["3"]


def test_ws_unknown_version_and_unsaved_without_components(api):
    with api.websocket_connect("/ws/formulations/nope/compliance") as ws:
        ws.send_json({"type": "classify"})
        err = ws.receive_json()
        assert err["type"] == "error" and "配方版本不存在" in err["detail"]
    with api.websocket_connect("/ws/formulations/new/compliance") as ws:
        ws.send_json({"type": "classify"})
        err = ws.receive_json()
        assert err["type"] == "error" and "未保存的配方" in err["detail"]


def test_ws_never_persists(api):
    from sqlalchemy import select

    from app.db.database import default_session_factory
    from app.db.models_plm import ClassificationResultRow

    with api.websocket_connect("/ws/formulations/new/compliance") as ws:
        ws.send_json({"type": "classify",
                      "components": [{"name": "二甲苯", "cas": "1330-20-7", "pct": 60},
                                     {"name": "水", "cas": "7732-18-5", "pct": 40}]})
        assert ws.receive_json()["type"] == "classification"
    with default_session_factory()() as session:
        assert session.execute(select(ClassificationResultRow)).scalars().all() == []
