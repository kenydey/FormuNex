"""W1-1: gate G3 reads the manual pick before the latest reviewed value.

Without a pick the gate keeps its old behaviour (latest reviewed wins);
with a pick the picked reviewed value is judged instead. A pick whose
measurement has since gone back to unreviewed is stale and ignored.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import ExperimentRow, MeasurementRow
from app.db.models_lab import MeasurementPickRow
from app.db.models_plm import ProductRevisionRow
from app.services.plm.release.gate import Check, _check_objectives


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _setup(session, *, pick_id=None, stale_pick=False):
    exp = ExperimentRow(domain="surface_treatment", project_id="proj-test", measured={})
    session.add(exp)
    session.flush()
    t0 = datetime(2026, 1, 1)
    old = MeasurementRow(id=str(uuid.uuid4()), experiment_id=exp.id, metric="salt_spray_hours",
                         value=600.0, unit="h", review_status="reviewed", created_at=t0)
    new = MeasurementRow(id=str(uuid.uuid4()), experiment_id=exp.id, metric="salt_spray_hours",
                         value=400.0, unit="h", review_status="reviewed", created_at=t0 + timedelta(days=1))
    session.add_all([old, new])
    session.flush()
    if pick_id == "old":
        session.add(MeasurementPickRow(id=str(uuid.uuid4()), experiment_id=exp.id,
                                       metric="salt_spray_hours", measurement_id=old.id,
                                       picked_by="u1"))
        if stale_pick:
            old.review_status = "unreviewed"
        session.flush()
    rev = ProductRevisionRow(id=str(uuid.uuid4()), product_id="prod-x", rev="A",
                             winning_experiment_id=exp.id,
                             spec_snapshot={"acceptance": [{"metric": "salt_spray_hours", "min": 500}]})
    session.add(rev)
    session.flush()
    return rev, old, new


def test_pick_wins_over_latest_reviewed(session):
    rev, old, _new = _setup(session, pick_id="old")
    c = Check("objectives", "G3", "目标契约关键指标达标")
    _check_objectives(session, c, rev)
    assert c.items == []
    assert c.evidence["salt_spray_hours"] == {"value": 600.0, "measurement_id": old.id, "basis": "manual"}


def test_no_pick_keeps_latest_reviewed_behaviour(session):
    rev, _old, new = _setup(session)
    c = Check("objectives", "G3", "目标契约关键指标达标")
    _check_objectives(session, c, rev)
    assert len(c.items) == 1
    assert c.evidence["salt_spray_hours"]["measurement_id"] == new.id
    assert c.evidence["salt_spray_hours"]["basis"] == "latest"


def test_stale_pick_falls_back_to_latest_reviewed(session):
    rev, _old, new = _setup(session, pick_id="old", stale_pick=True)
    c = Check("objectives", "G3", "目标契约关键指标达标")
    _check_objectives(session, c, rev)
    assert len(c.items) == 1
    assert c.evidence["salt_spray_hours"]["measurement_id"] == new.id
