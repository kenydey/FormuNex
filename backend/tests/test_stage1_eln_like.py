"""Stage 1 / SR-052: LIKE escaping, deterministic ordering, duplicate warnings.

An external id containing ``%`` or ``_`` must not widen the local-state
match to other links' measurements; ordering ties break on id; a
remote payload repeating a metric keeps the last value but warns.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.database import make_engine, make_session_factory
from app.db.models import ExperimentRow, MeasurementRow
from app.db.models_lab import ElnLinkRow, LabTaskRow, SampleRow, TestMethodRow
from app.services.eln import sync as eln_sync
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/stage1_eln_like.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _measurement(session, exp_id, ref, metric, value):
    session.add(MeasurementRow(id=str(uuid.uuid4()), experiment_id=exp_id, metric=metric,
                               value=value, external_ref=ref, review_status="unreviewed"))
    session.flush()


def test_like_wildcard_in_external_id_does_not_widen_match(db):
    with db() as s:
        exp = ExperimentRow(domain="coating", project_id="p1", measured={})
        s.add(exp)
        s.flush()
        _measurement(s, exp.id, "elabftw:ext_1:good", "good", 1.0)
        _measurement(s, exp.id, "elabftw:extX1:good", "good", 9.0)
        s.commit()
        # Without escaping, "ext_1" would match "extX1" ("_" = any char).
        assert eln_sync._local_state(s, "elabftw", "ext_1") == {"good": 1.0}
        assert eln_sync._local_state(s, "elabftw", "extX1") == {"good": 9.0}


def test_duplicate_remote_metric_warns_and_keeps_last(db, caplog):
    with db() as s:
        task = LabTaskRow(id=str(uuid.uuid4()), task_no="T-3", project_id="p1")
        s.add(task)
        s.flush()
        exp = ExperimentRow(domain="coating", project_id="p1", measured={})
        s.add(exp)
        s.flush()
        s.add(SampleRow(id=str(uuid.uuid4()), sample_no="S-3", lab_task_id=task.id, experiment_id=exp.id))
        s.add(TestMethodRow(id=str(uuid.uuid4()), code="M-G", metric="good", unit=""))
        empty = eln_sync._digest({})
        link = ElnLinkRow(id=str(uuid.uuid4()), connector="elabftw", external_id="ext-3",
                          local_type="lab_task", local_id=task.id, content_hash=empty + empty)
        s.add(link)
        s.commit()
        conn = SimpleNamespace(name="elabftw", pull_measurements=lambda ext, since=None: [
            SimpleNamespace(metric="good", value=1.0, unit=""),
            SimpleNamespace(metric="good", value=2.0, unit=""),
        ])
        with caplog.at_level("WARNING", logger="app.services.eln.sync"):
            report = eln_sync.pull(s, conn, task_ids=[task.id])
        assert report["recorded"] == 1
        assert any("duplicate remote metric" in r.message for r in caplog.records)
        rows = s.execute(select(MeasurementRow)).scalars().all()
        assert len(rows) == 1 and rows[0].value == 2.0
