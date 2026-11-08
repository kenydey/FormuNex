"""Batch 3 ELN: P2-C19/C20/C21/C22."""
from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.database import make_engine, make_session_factory
from app.db.models import ExperimentRow, MeasurementRow
from app.db.models_lab import ElnLinkRow, ElnSyncConflictRow, LabTaskRow, SampleRow, TestMethodRow
from app.services.eln import sync as eln_sync
from app.services.eln.connectors.base import RemoteValue
from app.services.eln.connectors.elabftw import ElabftwConnector
from app.services.eln.unit_conversions import convert_unit
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path}/eln3.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _setup(session, external_id="ext-1"):
    task = LabTaskRow(id=str(uuid.uuid4()), task_no="T-1", project_id="p1")
    session.add(task)
    session.flush()
    exp = ExperimentRow(domain="coating", project_id="p1", measured={})
    session.add(exp)
    session.flush()
    session.add(SampleRow(id=str(uuid.uuid4()), sample_no="S-1", lab_task_id=task.id,
                          experiment_id=exp.id))
    session.add(TestMethodRow(id=str(uuid.uuid4()), code="M-GOOD", metric="good", unit=""))
    session.flush()
    link = ElnLinkRow(id=str(uuid.uuid4()), connector="elabftw", external_id=external_id,
                      local_type="lab_task", local_id=task.id, content_hash="")
    session.add(link)
    session.commit()
    return task, link


def _measure(session, task, exp_id, metric, value, status, external_id="ext-1"):
    session.add(MeasurementRow(id=str(uuid.uuid4()), experiment_id=exp_id, metric=metric, value=value,
                               unit="", test_method="M-GOOD", operator="op", sample_id=None,
                               lab_task_id=task.id, review_status=status,
                               external_ref=f"elabftw:{external_id}:{metric}"))
    session.commit()


class _Stub:
    name = "elabftw"

    def __init__(self, values):
        self._values = values

    def pull_measurements(self, external_id, *, since=None):
        return [RemoteValue(m, v, "") for m, v in self._values.items()]


def test_unit_aliases_ascii_forms():
    assert convert_unit(1000.0, "ml", "L") == pytest.approx(1.0)
    assert convert_unit(1.0, "ul", "mL") == pytest.approx(0.001)
    assert convert_unit(1.0, "ug", "mg") == pytest.approx(0.001)
    assert convert_unit(1000.0, "um", "mm") == pytest.approx(1.0)
    assert convert_unit(1.0, "ug/L", "mg/L") == pytest.approx(0.001)
    assert convert_unit(1.0, "mg/l", "mg/L") == pytest.approx(1.0)
    # ppm -> mg/L is genuinely unconvertible (not a bug): keep failing closed.
    assert convert_unit(1.0, "ppm", "mg/L") is None


def test_conflict_snapshot_excludes_rejected_and_refreshes(db):
    with db() as s:
        task, link = _setup(s)
        exp_id = s.execute(select(SampleRow).where(SampleRow.lab_task_id == task.id)).scalar_one().experiment_id
        _measure(s, task, exp_id, "good", 9.0, "unreviewed")
        _measure(s, task, exp_id, "bad", 1.0, "rejected")
    with db() as s:
        report = eln_sync.pull(s, _Stub({"good": 2.0, "bad": 1.0}))
        assert report["conflicts"] == 1
        c = s.execute(select(ElnSyncConflictRow)).scalar_one()
        assert c.remote_snapshot == {"good": 2.0}
        assert "bad" not in (c.remote_units or {})
    with db() as s:
        report = eln_sync.pull(s, _Stub({"good": 3.0, "bad": 1.0}))
        assert report["conflicts"] == 1
        c = s.execute(select(ElnSyncConflictRow)).scalar_one()
        assert c.remote_snapshot == {"good": 3.0}


def test_take_remote_skips_rejected_and_states_it(db, caplog):
    with db() as s:
        task, link = _setup(s)
        exp_id = s.execute(select(SampleRow).where(SampleRow.lab_task_id == task.id)).scalar_one().experiment_id
        _measure(s, task, exp_id, "bad", 1.0, "rejected")
        conflict = ElnSyncConflictRow(id=str(uuid.uuid4()), link_id=link.id,
                                      local_snapshot={}, remote_snapshot={"good": 2.0, "bad": 1.0},
                                      remote_units={"good": "", "bad": ""}, status="open")
        s.add(conflict)
        s.commit()
        cid = conflict.id
    with db() as s:
        eln_sync.resolve_conflict(s, cid, resolution="take_remote", user_id="u1")
        rows = s.execute(select(MeasurementRow)).scalars().all()
        assert [r.metric for r in rows if r.value == 2.0] == ["good"]
        assert sum(1 for r in rows if r.metric == "bad") == 1  # only the rejected row
    assert "skipping locally-rejected" in caplog.text and "bad" in caplog.text


def test_elabftw_unparseable_field_is_reported_not_dropped(monkeypatch):
    conn = ElabftwConnector("https://elab.example", "key")
    payload = {"metadata": json.dumps({"extra_fields": {
        "good": {"value": "2.5", "unit": "mg/L", "description": ""},
        "bad": {"value": "not-a-number", "unit": "", "description": ""}}})}
    monkeypatch.setattr(conn, "_call", lambda *a, **k: SimpleNamespace(json=lambda: payload))
    values = conn.pull_measurements("42")
    assert [v.metric for v in values] == ["good"]
    assert len(conn.last_pull_errors) == 1 and "bad" in conn.last_pull_errors[0]
