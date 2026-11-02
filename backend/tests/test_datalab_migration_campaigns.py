"""Datalab removal batch 1 (consolidated): the pre-existing campaign
migration in app.services.lab.datalab_migration, extended with per-row
workbench_row.import audit entries and a version inventory.

The first batch-1 tool (app.services.datalab_import) duplicated this
migration; it was removed and its two unique values folded in here.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.db.database import Base, make_engine, make_session_factory
from app.db.models import Campaign
from app.db.models_lab import ElnLinkRow
from app.db.models_plm import AuditLogRow
from app.services.lab.datalab_migration import migrate_campaigns


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/mig.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as sess:
        yield sess


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


def _versions(refcode):
    return [{"id": "v1", "version": 1}, {"id": "v2", "version": 2}]


def _campaign(session: Session, refs):
    campaign = Campaign(name="c1", strategy="BayBE-LHS", sample_refs=refs)
    session.add(campaign)
    session.commit()
    return campaign.id


def _fetch(items):
    def fetch(item_id):
        return items.get(item_id)

    return fetch


def test_dry_run_writes_nothing(session):
    cid = _campaign(session, [{"id": 1, "item_id": "fm_c1_r1"}])
    report = migrate_campaigns(session, _fetch({"fm_c1_r1": _item()}), dry_run=True,
                               fetch_versions=_versions)
    assert report["rows"] == 1
    assert report["version_inventory"] == {f"{cid}:1": {"refcode": "test:AAAA",
                                                       "version_count": 2, "version_ids": ["v1", "v2"]}}
    session.rollback()
    assert session.get(Campaign, cid).sample_refs == [{"id": 1, "item_id": "fm_c1_r1"}]
    assert session.query(ElnLinkRow).count() == 0
    assert session.query(AuditLogRow).count() == 0


def test_apply_inlines_ref_writes_link_and_per_row_audit(session):
    cid = _campaign(session, [{"id": 1, "item_id": "fm_c1_r1"}])
    report = migrate_campaigns(session, _fetch({"fm_c1_r1": _item()}),
                               fetch_versions=_versions)
    session.commit()
    assert report["rows"] == 1
    refs = session.get(Campaign, cid).sample_refs
    assert refs[0]["status"] == "Done"
    assert refs[0]["planned_params"] == {"x": 1.0}
    assert refs[0]["measurements"] == {"yield": 0.9}
    assert refs[0]["migrated_from"] == "datalab"
    assert refs[0]["datalab_refcode"] == "test:AAAA"
    assert session.query(ElnLinkRow).filter_by(local_type="campaign_row").count() == 1
    entries = session.query(AuditLogRow).order_by(AuditLogRow.seq).all()
    actions = [e.action for e in entries]
    assert "workbench_row.import" in actions
    assert "lab.datalab_migrate_campaigns" in actions
    per_row = [e for e in entries if e.action == "workbench_row.import"][0]
    assert per_row.entity_id == f"{cid}:1"
    assert per_row.detail["datalab_version_count"] == 2
    # Idempotent re-run: nothing moves, rows already native.
    rerun = migrate_campaigns(session, _fetch({"fm_c1_r1": _item()}), fetch_versions=_versions)
    assert rerun["rows"] == 0
    assert rerun["already_native"] == 1


def test_missing_item_kept_or_pruned(session):
    cid = _campaign(session, [{"id": 1, "item_id": "fm_c1_gone"}])
    report = migrate_campaigns(session, _fetch({}))
    assert report["missing"] == [{"campaign_id": cid, "row_id": 1, "item_id": "fm_c1_gone"}]
    assert session.get(Campaign, cid).sample_refs == [{"id": 1, "item_id": "fm_c1_gone"}]
    pruned = migrate_campaigns(session, _fetch({}), prune_missing=True)
    assert pruned["pruned"] == 1
    assert session.get(Campaign, cid).sample_refs == []


def test_versions_failure_does_not_block_row(session):
    cid = _campaign(session, [{"id": 1, "item_id": "fm_c1_r1"}])

    def boom(refcode):
        raise RuntimeError("versions endpoint moved")

    report = migrate_campaigns(session, _fetch({"fm_c1_r1": _item()}), fetch_versions=boom)
    assert report["rows"] == 1
    assert len(report["errors"]) == 1
    assert "versions fetch failed" in report["errors"][0]["error"]
    assert session.get(Campaign, cid).sample_refs[0]["status"] == "Done"
