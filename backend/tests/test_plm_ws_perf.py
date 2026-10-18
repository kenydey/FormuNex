"""WebSocket real-time compliance performance (plan 3-9).

The service-layer path already has a <150 ms assertion (``test_plm_perf``);
this file asserts the *end-to-end* WebSocket round trip — connect → send
``classify`` → receive ``classification`` — over the same 50-component
payload, because the socket path adds a worker-thread hop and JSON framing
the service test never measures.

Median of 3 warmed round trips must fit the 150 ms per-edit budget
(方案 §4.6). Marked ``perf`` so ``pytest -m perf`` runs the full
performance regression suite.
"""
from __future__ import annotations

import statistics
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

pytestmark = pytest.mark.perf

_HAZARDS = [
    ("flammable_liquid", "3", "H226"),
    ("acute_tox_oral", "4", "H302"),
    ("acute_tox_dermal", "4", "H312"),
    ("skin_corrosion", "2", "H315"),
    ("eye_damage", "2A", "H319"),
    ("aquatic_acute", "2", "H401"),
    ("aquatic_chronic", "2", "H411"),
]

WS_BUDGET_S = 0.15


def _valid_cas(i: int) -> str:
    digits = f"{100000 + i}"
    total = sum(int(d) * (len(digits) - j) for j, d in enumerate(digits))
    return f"{digits[:4]}-{digits[4:]}-{total % 10}"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'plm-ws-perf.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    from sqlalchemy import insert

    from app.db.database import default_session_factory
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow

    sub_rows, cls_rows = [], []
    for i in range(50):
        sid = str(uuid.uuid4())
        sub_rows.append({"id": sid, "cas_no": _valid_cas(i), "name_zh": f"测试组分{i:02d}",
                         "name_en": f"test-component-{i:02d}", "aliases": [],
                         "hazardous_catalog_no": None})
        for j, (hc, cat, h) in enumerate(_HAZARDS):
            if (i + j) % 3 == 0:
                continue
            cls_rows.append({"id": str(uuid.uuid4()), "substance_id": sid, "hazard_class": hc,
                             "category": cat, "h_code": h, "source": "cn_catalog"})
    with default_session_factory()() as session:
        session.execute(insert(SubstanceRow), sub_rows)
        session.execute(insert(SubstanceClassificationRow), cls_rows)
        session.commit()
    from app.main import app

    yield TestClient(app)
    get_settings.cache_clear()


def _payload() -> dict:
    return {
        "type": "classify",
        "components": [{"name": f"测试组分{i:02d}", "cas": _valid_cas(i), "pct": 2.0} for i in range(50)],
        "properties": {"flash_point_c": 27, "initial_boiling_point_c": 138},
    }


def _round_trip(ws) -> dict:
    ws.send_json(_payload())
    return ws.receive_json()


def test_ws_classify_round_trip_under_150ms(api):
    with api.websocket_connect("/ws/formulations/new/compliance") as ws:
        # Warm-up: fills the in-process regulatory-data cache (first edit).
        first = _round_trip(ws)
        assert first["type"] == "classification" and first["classified"] is True
        latencies = []
        for _ in range(3):
            start = time.monotonic()
            msg = _round_trip(ws)
            latencies.append(time.monotonic() - start)
            assert msg["type"] == "classification"
    median = statistics.median(latencies)
    assert median < WS_BUDGET_S, (
        f"WS classify round-trip median {median * 1000:.1f} ms "
        f"(trips {[f'{t * 1000:.1f}' for t in latencies]} ms, budget 150 ms)")
