"""Batch 1: Datalab -> native import tool (fake Datalab, tmp DB)."""

from __future__ import annotations

import httpx
import pytest

from app.db.database import Base, make_engine, make_session_factory
from app.db.models import Campaign
from app.db.models_plm import AuditLogRow
from app.services import datalab_import


@pytest.fixture()
def factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/import.db")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _item(refcode="test:AAAA"):
    return {
        "blocks_obj": {
            "formumind_params": {"data": {
                "status": "Done", "planned_params": {"x": 1.0}, "actual_params": {"x": 1.1},
                "note": "row note", "tags": ["t1"],
            }},
            "formumind_measurements": {"data": {"yield": 0.9}},
        },
        "refcode": refcode,
    }


def _transport(items):
    def handler(request):
        path = request.url.path
        if path.startswith("/items/") and path.endswith("/versions/"):
            return httpx.Response(200, json={"versions": [
                {"_id": "v1", "version": 1, "action": "create",
                 "timestamp": "2026-01-01T00:00:00", "creator": {"display_name": "op"}},
                {"_id": "v2", "version": 2, "action": "update",
                 "timestamp": "2026-01-02T00:00:00", "creator": {"display_name": "op"}},
            ]})
        if path.startswith("/items/") and path.endswith("/"):
            item_id = path.split("/")[2]
            if item_id in items:
                return httpx.Response(200, json=items[item_id])
            return httpx.Response(404, json={})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def _campaign(factory, refs):
    with factory() as session:
        campaign = Campaign(name="c1", strategy="BayBE-LHS", sample_refs=refs)
        session.add(campaign)
        session.commit()
        return campaign.id


def _refs(factory, campaign_id):
    with factory() as session:
        return session.get(Campaign, campaign_id).sample_refs


def _audit(factory):
    with factory() as session:
        return session.query(AuditLogRow).all()


def test_dry_run_writes_nothing(factory):
    cid = _campaign(factory, [{"id": 1, "item_id": "fm_c1_r1"},
                              {"id": 2, "item_id": "local_2", "status": "Pending",
                               "planned_params": {}, "actual_params": {}, "measurements": {}}])
    report = datalab_import.run_import(
        session_factory=factory, api_url="http://datalab.test",
        apply=False, transport=_transport({"fm_c1_r1": _item()}))
    assert report["applied"] is False
    assert report["totals"] == {"rows_imported": 1, "rows_skipped_native": 1,
                                "rows_failed": 0, "datalab_versions": 2, "attachments": 0}
    assert _refs(factory, cid)[0] == {"id": 1, "item_id": "fm_c1_r1"}
    assert _audit(factory) == []


def test_apply_imports_and_is_idempotent(factory):
    cid = _campaign(factory, [{"id": 1, "item_id": "fm_c1_r1"}])
    transport = _transport({"fm_c1_r1": _item()})
    report = datalab_import.run_import(
        session_factory=factory, api_url="http://datalab.test",
        apply=True, transport=transport)
    assert report["totals"]["rows_imported"] == 1
    refs = _refs(factory, cid)
    assert refs[0]["status"] == "Done"
    assert refs[0]["planned_params"] == {"x": 1.0}
    assert refs[0]["measurements"] == {"yield": 0.9}
    assert refs[0]["tags"] == ["t1"]
    entries = _audit(factory)
    assert len(entries) == 1
    assert entries[0].action == "workbench_row.import"
    assert entries[0].entity_id == f"{cid}:1"
    assert entries[0].detail["datalab_refcode"] == "test:AAAA"
    assert entries[0].detail["datalab_version_count"] == 2
    rerun = datalab_import.run_import(
        session_factory=factory, api_url="http://datalab.test",
        apply=True, transport=transport)
    assert rerun["totals"]["rows_imported"] == 0
    assert rerun["totals"]["rows_skipped_native"] == 1
    assert len(_audit(factory)) == 1


def test_fetch_failure_skips_row_but_imports_others(factory):
    cid = _campaign(factory, [{"id": 1, "item_id": "fm_c1_r1"},
                              {"id": 2, "item_id": "fm_c1_gone"}])
    report = datalab_import.run_import(
        session_factory=factory, api_url="http://datalab.test",
        apply=True, transport=_transport({"fm_c1_r1": _item()}))
    assert report["totals"]["rows_imported"] == 1
    assert report["totals"]["rows_failed"] == 1
    assert report["campaigns"][0]["rows_failed"][0]["row_id"] == 2
    refs = _refs(factory, cid)
    assert refs[0]["status"] == "Done"
    assert refs[1] == {"id": 2, "item_id": "fm_c1_gone"}
