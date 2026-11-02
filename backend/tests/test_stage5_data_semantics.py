"""Stage 5 / SR-079 + SR-080: release version semantics + classification uniqueness.

- A new approved seed supersedes EVERY prior approved row (D-11 formal
  landing), whatever its digest; skipped / backfill-completion / draft
  runs supersede nothing.
- latest_data_release is deterministic on created_at ties (id desc).
- The release status vocabulary is CHECK-closed; the classification
  (revision_id, inputs_hash) pair is unique (NULL revisions exempt,
  as in both SQLite and PostgreSQL).
"""
from __future__ import annotations

import gzip
import json
import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401  (registers every table)
from app.db.models import Base
from app.db.models_plm import ClassificationResultRow
from app.db.models_regulatory import RegulatoryDataRelease
from app.services.plm.ghs.service import latest_data_release
from app.services.plm.regulatory.seed import seed_regulatory_db


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 's5.db'}")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _write(path, name, payload):
    p = path / name
    if name.endswith(".gz"):
        with gzip.open(p, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    else:
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def resources(tmp_path):
    res = tmp_path / "res"
    res.mkdir()
    _write(res, "common_chemicals.json.gz", {"rows": [["64-17-5", "乙醇", "Ethanol", "C2H6O"]]})
    _write(res, "hazard_chemical_info.json", {"chemicals": [
        {"index": 2568, "chinese_name": "乙醇", "alias": "", "english_name": "ethanol",
         "cas_number": "64-17-5", "hazard_category": "易燃液体,类别 2", "notes": ""},
    ]})
    # The seed reads these unconditionally; minimal content suffices —
    # only the release rows matter for these tests.
    _write(res, "oel_gbz2_2019.json", {"records": [
        {"name_cn": "乙醇", "name_en": "Ethanol", "cas_number": "64-17-5", "mac_mg_m3": "",
         "pc_twa_mg_m3": "", "pc_stel_mg_m3": "", "critical_effect": "", "remark": ""},
    ]})
    _write(res, "oel_eh40.json", {"records": []})
    _write(res, "hazard_statements.json", {"statements": []})
    _write(res, "precaution_statements.json", {"statements": []})
    _write(res, "gb12268_2025.json", {"entries": {}})
    return res


def _approved(engine):
    with engine.connect() as c:
        return c.execute(
            select(RegulatoryDataRelease.id, RegulatoryDataRelease.status)
            .where(RegulatoryDataRelease.status == "approved")
        ).all()


def _status_of(engine, release_id):
    with engine.connect() as c:
        return c.execute(
            select(RegulatoryDataRelease.status)
            .where(RegulatoryDataRelease.id == release_id)
        ).scalar_one()


def test_approve_new_digest_supersedes_old_approved(engine, resources):
    first = seed_regulatory_db(engine, resources=resources)
    assert not first.get("skipped")
    # A different digest (extra chemical) is a new version, not a re-run.
    _write(resources, "hazard_chemical_info.json", {"chemicals": [
        {"index": 2568, "chinese_name": "乙醇", "alias": "", "english_name": "ethanol",
         "cas_number": "64-17-5", "hazard_category": "易燃液体,类别 2", "notes": ""},
        {"index": 1, "chinese_name": "苯酚", "alias": "", "english_name": "phenol",
         "cas_number": "108-95-2", "hazard_category": "急性毒性-经口,类别 3", "notes": ""},
    ]})
    second = seed_regulatory_db(engine, resources=resources)
    assert not second.get("skipped")
    assert second["release_id"] != first["release_id"]
    assert _status_of(engine, first["release_id"]) == "superseded"
    assert _status_of(engine, second["release_id"]) == "approved"
    assert len(_approved(engine)) == 1


def test_skipped_seed_supersedes_nothing(engine, resources):
    first = seed_regulatory_db(engine, resources=resources)
    again = seed_regulatory_db(engine, resources=resources)
    assert again.get("skipped") is True
    assert again["release_id"] == first["release_id"]
    assert _status_of(engine, first["release_id"]) == "approved"


def test_backfill_completion_and_draft_supersede_nothing(engine, resources):
    first = seed_regulatory_db(engine, resources=resources)
    done = seed_regulatory_db(engine, resources=resources, backfill=True)
    assert done["release_id"] == first["release_id"]
    assert _status_of(engine, first["release_id"]) == "approved"
    draft = seed_regulatory_db(engine, resources=resources, status="draft", force=True)
    assert not draft.get("skipped")
    assert _status_of(engine, first["release_id"]) == "approved"
    assert _status_of(engine, draft["release_id"]) == "draft"


def test_latest_data_release_tiebreak_is_deterministic(engine):
    stamp = datetime(2026, 11, 2, 12, 0, 0)
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    with engine.begin() as c:
        for rid in ids:
            c.execute(insert(RegulatoryDataRelease).values(
                id=rid, label="t", input_digest=f"d-{rid[:8]}",
                created_at=stamp, status="approved"))
    with Session(engine) as s:
        assert latest_data_release(s) == latest_data_release(s) == max(ids)


def test_release_status_check_rejects_unknown_status(engine):
    with engine.begin() as c:
        with pytest.raises(IntegrityError):
            c.execute(insert(RegulatoryDataRelease).values(
                id=str(uuid.uuid4()), label="t", input_digest="d",
                created_at=datetime(2026, 11, 2), status="bogus"))


def test_classification_stacking_is_accepted(engine):
    """SR-080④ decided (2026-11-02): classification rows are
    per-computation records — the same (revision_id, inputs_hash)
    pair legitimately stacks (gate, SDS, label each classify fresh).
    No unique key; this test pins the accepted semantics."""
    with engine.begin() as c:
        for _ in range(2):
            c.execute(insert(ClassificationResultRow).values(
                id=str(uuid.uuid4()), revision_id="rev-1", inputs_hash="h1",
                rule_set_version="test.v1"))
        n = c.execute(
            select(func.count()).select_from(ClassificationResultRow)
            .where(ClassificationResultRow.inputs_hash == "h1")
        ).scalar_one()
    assert n == 2
