"""P1-2 tightening: write paths refuse project-less rows; 0077 enforces it.

Covers the store/API choke points (``doe_plan_store.save``,
``tasks.create_task`` / ``dispatch_plan``) and the migration itself:
backfill-then-guard-then-tighten on a real Alembic chain.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from app.db import doe_plan_store
from app.db.database import Base, make_engine, make_session_factory
from app.domain.schemas import DOEFactor, DOEPlan, DOERun, ProductDomain
from app.services.lab import tasks
from app.services.lab.methods import LabError
from tests.alembic_helpers import run_downgrade, run_upgrade


@pytest.fixture()
def factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/tight.db")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def _plan(plan_id: str) -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[DOEFactor(name="x", low=0.0, high=1.0)],
        runs=[DOERun(run_id=1, coded={"x": 0.0}, natural={"x": 0.0})],
        plan_id=plan_id,
        domain=ProductDomain.surface_treatment,
    )


def test_save_requires_project(factory):
    with factory() as s:
        with pytest.raises(ValueError, match="project_id"):
            doe_plan_store.save(s, _plan("p"), project_id=None)
        with pytest.raises(ValueError, match="project_id"):
            doe_plan_store.save(s, _plan("p"), project_id="")
        pid = doe_plan_store.save(s, _plan("p"), project_id="proj-A")
        assert pid == "p"


def test_create_task_requires_project(factory):
    with factory() as s:
        with pytest.raises(LabError, match="project_id"):
            tasks.create_task(s, factors={"x": 1.0}, metrics=[{"metric": "m"}],
                              domain="anticorrosion_coating", user_id="u1",
                              project_id=None)
        with pytest.raises(LabError, match="project_id"):
            tasks.create_task(s, factors={"x": 1.0}, metrics=[{"metric": "m"}],
                              domain="anticorrosion_coating", user_id="u1",
                              project_id="  ")
        t = tasks.create_task(s, factors={"x": 1.0}, metrics=[{"metric": "m"}],
                              domain="anticorrosion_coating", user_id="u1",
                              project_id="proj-A")
        assert t.project_id == "proj-A"


def test_dispatch_falls_back_to_plan_project(factory):
    with factory() as s:
        doe_plan_store.save(s, _plan("dp"), project_id="proj-A")
        s.commit()
        out = tasks.dispatch_plan(s, "dp", metrics=[{"metric": "m"}],
                                  user_id="u1", project_id=None)
        assert out["created"] and all(t.project_id == "proj-A" for t in out["created"])


def _legacy_plan_params() -> str:
    import json

    return json.dumps({
        "factors": [{"name": "x", "low": 0.0, "high": 1.0}],
        "runs": [{"run_id": 1, "coded": {"x": 0.0}, "natural": {"x": 0.0}}],
        "domain": "surface_treatment", "notes": "",
    })


def test_dispatch_without_any_project_is_refused(tmp_path):
    # Pre-0077 legacy state: a NULL-project plan row. The ORM model is
    # already NOT NULL, so the row is hand-built (the migration chain's
    # 0001 baseline create_all would otherwise bake the new shape in).
    # Neither the request nor the plan supplies a project -> fail closed.
    db_url = f"sqlite:///{tmp_path}/legacy.db"
    eng = create_engine(db_url)
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE doe_plans (id VARCHAR(36) PRIMARY KEY, experiment_id INTEGER, "
            "campaign_id INTEGER, design_type VARCHAR(32) NOT NULL, parameters JSON NOT NULL, "
            "round INTEGER, created_at DATETIME NOT NULL, project_id VARCHAR(36), "
            "status VARCHAR(16) NOT NULL)"))
        conn.execute(text(
            "INSERT INTO doe_plans (id, design_type, parameters, project_id, status, created_at) "
            "VALUES ('dp', 'lhs', :params, NULL, 'draft', '2026-01-01')"),
            {"params": _legacy_plan_params()})
    eng.dispose()
    factory = make_session_factory(create_engine(db_url))
    with factory() as s:
        with pytest.raises(LabError, match="project_id"):
            tasks.dispatch_plan(s, "dp", metrics=[{"metric": "m"}],
                                user_id="u1", project_id=None)


def _nullable(db_url: str, table: str) -> bool:
    eng = create_engine(db_url)
    try:
        for col in inspect(eng).get_columns(table):
            if col["name"] == "project_id":
                return bool(col["nullable"])
    finally:
        eng.dispose()
    raise AssertionError(f"no project_id on {table}")


def _legacy_tables(db_url: str) -> None:
    """Hand-build the pre-0077 shape (nullable project_id) for the two
    tables plus the parents the backfill reads. Later migrations are
    inspect-first, so the full chain leaves these tables alone until 0077."""
    eng = create_engine(db_url)
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE campaigns (id INTEGER PRIMARY KEY, name VARCHAR(128) NOT NULL, "
            "project_id VARCHAR(36))"))
        conn.execute(text(
            "CREATE TABLE experiments (id INTEGER PRIMARY KEY, project_id VARCHAR(36) NOT NULL DEFAULT '')"))
        conn.execute(text(
            "CREATE TABLE doe_plans (id VARCHAR(36) PRIMARY KEY, experiment_id INTEGER, "
            "campaign_id INTEGER, design_type VARCHAR(32) NOT NULL, parameters JSON NOT NULL, "
            "round INTEGER, created_at DATETIME NOT NULL, project_id VARCHAR(36), "
            "status VARCHAR(16) NOT NULL)"))
        conn.execute(text(
            "CREATE TABLE lab_tasks (id VARCHAR(36) PRIMARY KEY, task_no VARCHAR(40) NOT NULL, "
            "project_id VARCHAR(64), doe_plan_id VARCHAR(64), run_index INTEGER, "
            "formulation_version_id VARCHAR(36), factors JSON NOT NULL, "
            "requested_metrics JSON NOT NULL, assignee_id VARCHAR(36), status VARCHAR(16) NOT NULL, "
            "due_at DATETIME, notes TEXT NOT NULL, created_by VARCHAR(36), "
            "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"))
    eng.dispose()


def test_migration_0077_backfills_guards_and_tightens(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path}/m77.db"
    _legacy_tables(db_url)
    assert _nullable(db_url, "doe_plans") is True
    assert _nullable(db_url, "lab_tasks") is True
    eng = create_engine(db_url)
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO campaigns (name, project_id) VALUES ('c1', 'proj-A')"))
        camp_id = conn.execute(text("SELECT id FROM campaigns WHERE name = 'c1'")).scalar_one()
        # Attributable plan (via campaign) + orphan plan + orphan task.
        # created_at / status have no server defaults, so raw SQL supplies them.
        conn.execute(text(
            "INSERT INTO doe_plans (id, campaign_id, design_type, parameters, project_id, status, created_at) "
            "VALUES ('att', :camp, 'lhs', '{}', NULL, 'draft', '2026-01-01')"), {"camp": camp_id})
        conn.execute(text(
            "INSERT INTO doe_plans (id, design_type, parameters, project_id, status, created_at) "
            "VALUES ('orp', 'lhs', '{}', NULL, 'draft', '2026-01-01')"))
        conn.execute(text(
            "INSERT INTO lab_tasks (id, task_no, doe_plan_id, project_id, status, factors, "
            "requested_metrics, notes, created_at, updated_at) "
            "VALUES ('t1', 'LT-1', 'att', NULL, 'open', '{}', '[]', '', '2026-01-01', '2026-01-01')"))
    eng.dispose()
    # Guard: orphan rows remain -> upgrade refuses with their counts.
    with pytest.raises(Exception, match="0077 refuses"):
        run_upgrade(db_url, monkeypatch, "head")
    # Operator deletes the orphan; the backfill then fills the rest.
    eng = create_engine(db_url)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM doe_plans WHERE id = 'orp'"))
    eng.dispose()
    run_upgrade(db_url, monkeypatch, "head")
    assert _nullable(db_url, "doe_plans") is False
    assert _nullable(db_url, "lab_tasks") is False
    eng = create_engine(db_url)
    with eng.begin() as conn:
        assert conn.execute(text("SELECT project_id FROM doe_plans WHERE id = 'att'")).scalar_one() == "proj-A"
        assert conn.execute(text("SELECT project_id FROM lab_tasks WHERE id = 't1'")).scalar_one() == "proj-A"
    eng.dispose()
    # Downgrade restores nullability.
    run_downgrade(db_url, monkeypatch, "0076")
    assert _nullable(db_url, "doe_plans") is True
    assert _nullable(db_url, "lab_tasks") is True
