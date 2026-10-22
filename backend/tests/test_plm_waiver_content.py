"""Release-gate waivers are bound to the failure content they waive (P0-2 fix).

Regression: waiving a gate item, then changing the underlying value so the
failure message changes (same item key), must NOT keep the item waived — the
old waiver stops matching and the item fails again until re-waived.
Pre-0067 waiver rows (content_hash NULL) never match a current message.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

PASSWORD = "pw-1234567"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'waiver.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        ids = {name: accounts.create_user(s, username=name, password=PASSWORD, roles=roles).id
               for name, roles in (("lead", ["project_lead"]), ("ehs", ["ehs_regulatory"]))}
        xyl = SubstanceRow(id=str(uuid.uuid4()), cas_no="1330-20-7", name_zh="二甲苯", name_en="xylene", aliases=[],
                           hazardous_catalog_no=2289)
        water = SubstanceRow(id=str(uuid.uuid4()), cas_no="7732-18-5", name_zh="水", name_en="water", aliases=[])
        s.add_all([xyl, water])
        s.flush()
        locked = datetime(2026, 10, 1)
        for hc, cat, h in [("flammable_liquid", "3", "H226"), ("skin_corrosion", "2", "H315")]:
            s.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=xyl.id, hazard_class=hc, category=cat,
                                             h_code=h, source="manual", locked_at=locked, locked_by=ids["ehs"]))
        s.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=water.id, hazard_class="not_classified",
                                         category="-", source="manual", locked_at=locked, locked_by=ids["ehs"]))
        s.add(FormulationVersion(id="fv-1", lineage_id="l", version=1, domain="coating", name="v1",
                                 created_at=datetime(2026, 10, 1),
                                 snapshot={"ingredients": [{"name": "二甲苯", "cas_no": "1330-20-7", "weight_pct": 30},
                                                           {"name": "水", "cas_no": "7732-18-5", "weight_pct": 70}]}))
        s.commit()
    from app.main import app

    client = TestClient(app)
    tokens = {}
    for name in ("lead", "ehs"):
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    pid = client.post("/api/plm/products", json={"code": "FX-100", "name_zh": "FX-100 环氧底漆"}).json()["id"]
    rid = client.post(f"/api/plm/products/{pid}/revisions", json={
        "formulation_version_id": "fv-1",
        "spec_snapshot": {"acceptance": [{"metric": "solids", "label": "固含量", "unit": "%", "min": 55, "max": 60}]}}).json()["id"]
    yield {"client": client, "rid": rid, "h": tokens}
    get_settings.cache_clear()


def _objectives_item(e):
    gate = e["client"].get(f"/api/plm/revisions/{e['rid']}/gate").json()
    check = next(c for c in gate["checks"] if c["key"] == "objectives")
    return next(i for i in check["items"] if i["key"] == "solids")


def test_waiver_stops_matching_after_message_change(env):
    c, rid, h = env["client"], env["rid"], env["h"]
    item = _objectives_item(env)
    assert item["message"] == "固含量：无已复核的实测值" and item["waivable"] and not item["waived"]
    r = c.post(f"/api/plm/revisions/{rid}/gate/waivers", headers=h["lead"],
               json={"check": "objectives", "item": "solids", "reason": "客户同意以当前值交付", "password": PASSWORD})
    assert r.status_code == 201, r.text
    assert _objectives_item(env)["waived"] is True
    # Change the underlying spec label: same item key, different message.
    from app.db.database import default_session_factory
    from app.db.models_plm import ProductRevisionRow

    with default_session_factory()() as s:
        rev = s.get(ProductRevisionRow, rid)
        snap = dict(rev.spec_snapshot)
        snap["acceptance"] = [{**snap["acceptance"][0], "label": "固含量（修订）"}]
        rev.spec_snapshot = snap
        s.commit()
    item = _objectives_item(env)
    assert item["message"] == "固含量（修订）：无已复核的实测值"
    assert item["waived"] is False
    # Re-waiving the new message works (new unique constraint allows it).
    r = c.post(f"/api/plm/revisions/{rid}/gate/waivers", headers=h["lead"],
               json={"check": "objectives", "item": "solids", "reason": "修订后再次评估，风险可接受", "password": PASSWORD})
    assert r.status_code == 201, r.text
    assert _objectives_item(env)["waived"] is True


def test_legacy_null_hash_waiver_never_matches():
    from app.services.plm.release.gate import Check, waiver_content_hash

    check = Check(key="objectives", code="G3", title="目标契约")
    check.fail("solids", "固含量：无已复核的实测值")
    legacy = SimpleNamespace(id="w-legacy", reason="old", waived_by="u",
                             created_at=datetime(2026, 10, 1))
    assert check.to_dict({("objectives", "solids", None): legacy})["items"][0]["waived"] is False
    matching = SimpleNamespace(id="w-new", reason="new", waived_by="u",
                               created_at=datetime(2026, 10, 1))
    key = ("objectives", "solids", waiver_content_hash("固含量：无已复核的实测值"))
    assert check.to_dict({key: matching})["items"][0]["waived"] is True
