"""Stage 1 / SR-017: a partially failed ELN pull advances only past landed metrics.

Before, ``_apply`` hashed the full remote dict even when some metrics
failed to record, so the next pull took the "unchanged" fast path and
the failed metrics were never retried. Now the watermark digest covers
only successfully recorded metrics, and a retry after the missing
method appears records exactly the failed metric.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.db.database import make_engine, make_session_factory
from app.db.models import ExperimentRow
from app.db.models_lab import ElnLinkRow, LabTaskRow, SampleRow, TestMethodRow
from app.services.eln import sync as eln_sync
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/stage1_eln.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _setup(session):
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
    link = ElnLinkRow(id=str(uuid.uuid4()), connector="elabftw", external_id="ext-1",
                      local_type="lab_task", local_id=task.id, content_hash="")
    session.add(link)
    session.commit()
    return task, link


def test_partial_failure_watermark_excludes_failed_metric(db):
    with db() as s:
        task, link = _setup(s)
        n, errors = eln_sync._apply(s, link, task, {"good": 1.0, "bad": 2.0}, {})
        assert n == 1 and errors and any("bad" in e for e in errors)
        assert link.sync_state == "error"
        assert link.content_hash[:32] == eln_sync._digest({"good": 1.0})
        # The next pull must NOT take the unchanged fast path.
        assert eln_sync._digest({"good": 1.0, "bad": 2.0}) != link.content_hash[:32]
        s.commit()
        # The missing method appears; a retry records exactly the failed metric.
        s.add(TestMethodRow(id=str(uuid.uuid4()), code="M-BAD", metric="bad", unit=""))
        s.flush()
        n2, errors2 = eln_sync._apply(s, link, task, {"good": 1.0, "bad": 2.0},
                                      eln_sync._local_state(s, "elabftw", "ext-1"))
        assert n2 == 1 and not errors2
        assert link.sync_state == "ok"
        assert link.content_hash[:32] == eln_sync._digest({"good": 1.0, "bad": 2.0})
