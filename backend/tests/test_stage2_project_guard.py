"""Stage 2 / SR-032: project-scoped writes name a live project.

Non-empty is not enough (P1-2): a missing or archived project fails
closed at the store entry, instead of writing rows invisible to every
project-scoped read.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.db import doe_plan_store
from app.db.database import make_engine, make_session_factory
from app.db.models import ProjectRow
from app.domain.schemas import DOEFactor, DOEPlan, DOERun
from app.services.lab import tasks
from app.services.lab.tasks import LabError
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/stage2_guard.db"
    run_upgrade(db_url, monkeypatch)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with factory() as s:
        s.add(ProjectRow(id="live"))
        s.add(ProjectRow(id="old", is_archived=True))
        s.commit()
    return factory


def _plan() -> DOEPlan:
    return DOEPlan(design="lhs", factors=[DOEFactor(name="x", low=0.0, high=1.0)],
                   runs=[DOERun(run_id=1, coded={"x": 0.0}, natural={"x": 0.0})])


def test_save_rejects_missing_and_archived_projects(factory):
    with factory() as s:
        with pytest.raises(ValueError, match="项目不存在"):
            doe_plan_store.save(s, _plan(), project_id="ghost")
        with pytest.raises(ValueError, match="项目已归档"):
            doe_plan_store.save(s, _plan(), project_id="old")
        pid = doe_plan_store.save(s, _plan(), project_id="live")
        assert pid


def test_create_task_rejects_missing_project(factory):
    with factory() as s:
        with pytest.raises(LabError, match="项目不存在"):
            tasks.create_task(s, factors={"x": 1.0}, metrics=[{"metric": "m"}],
                              domain="surface_treatment", user_id="u1", project_id="ghost")
