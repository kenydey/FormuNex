"""Batch 3 DOE: P1-C15/C16/C17, P2-C23/C24/C25/C27/C28."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.database import Base, default_engine
from app.db.models import DOECycleRunRow, ExperimentRow
from app.domain.project_workspace import default_requirement
from app.services import doe_cycle_service as svc
from app.services.doe_adaptive import adapt_factor_ranges
from app.services.doe_designs.constrained_mixture import Component, design
from app.services.doe_designs.d_optimal import MixtureSpace


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path}/doe3.db")
    get_settings.cache_clear()
    Base.metadata.create_all(default_engine())
    yield
    get_settings.cache_clear()


def test_two_component_replicate_centroid_finds_centroid_by_coordinates():
    # P1-C15/P2-C27: with two components the centroid (50, 50) coincides
    # with the edge midpoint, so tag matching missed it and next() raised
    # StopIteration. Coordinate matching must find it.
    d = design([Component("A", 10, 90), Component("B", 10, 90)], total=100,
               n_runs=5, replicate_centroid=2)
    centroids = [r for r in d.runs if r["A"] == 50.0 and r["B"] == 50.0]
    assert len(centroids) >= 2
    assert not any("未找到中心点" in n for n in d.notes)


def test_pseudo_span_zero_returns_zeros():
    # P1-C16: lowers already sum to the total — a single-point space.
    assert MixtureSpace(["A", "B"], [50.0, 50.0], 100.0).pseudo({"A": 50, "B": 50}) == [0.0, 0.0]
    assert MixtureSpace(["A", "B"], [10.0, 20.0], 100.0).pseudo({"A": 40, "B": 60}) == [
        pytest.approx(30 / 70), pytest.approx(40 / 70)]


def test_persist_skips_infeasible_numeric_only_campaign(db):
    req = default_requirement()
    experiments = [
        {"run_id": 1, "natural_factors": {"X": 5.0, "label": "text", "meta": {"a": 1}, "flag": True, "none": None},
         "ai_suggested": True, "infeasible": False, "infeasible_reason": ""},
        {"run_id": 2, "natural_factors": {"X": 6.0},
         "ai_suggested": True, "infeasible": True, "infeasible_reason": "超出固含量上限"},
    ]
    out = svc.persist_experiments(req, experiments, [], task_id="t1", workbench_campaign_id=7)
    assert out["count"] == 1 and out["skipped_infeasible"] == 1
    assert out["skipped_infeasible_reasons"] == ["超出固含量上限"]
    from app.db.database import default_session_factory

    with default_session_factory()() as s:
        row = s.query(ExperimentRow).filter(ExperimentRow.doe_task_id == "t1").one()
        assert row.factors == {"X": 5.0}
        assert row.source == "doe_cycle"
        assert row.workbench_campaign_id == 7


def test_adapt_direction_min_and_shrink():
    class FakeFactor:
        def __init__(self, name, low, high):
            self.name = name
            self.low = low
            self.high = high

    # direction="min": the minimum-response row sits on the low boundary.
    f = [FakeFactor("t", -10, 10)]
    results = [{"t": -10, "response": 0}, {"t": 0, "response": 5}, {"t": 10, "response": 9}]
    assert adapt_factor_ranges(f, results, direction="min")[0].low == pytest.approx(-14)
    assert adapt_factor_ranges([FakeFactor("t", -10, 10)], results)[0].high == pytest.approx(14)
    # Shrink: response uncorrelated with the factor (R² = 0), best mid-range.
    f2 = [FakeFactor("x", 0, 100)]
    results2 = [{"x": 20, "response": 2}, {"x": 40, "response": 4},
                {"x": 60, "response": 4}, {"x": 80, "response": 2}]
    adapted = adapt_factor_ranges(f2, results2)
    assert adapted[0].low == pytest.approx(10) and adapted[0].high == pytest.approx(90)


@pytest.fixture()
def mem_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _factory():
        return factory()

    return _factory


def test_cycle_runs_total_is_full_sum_not_page(mem_session_factory, monkeypatch):
    from types import SimpleNamespace

    from app.api import doe as doe_api

    with mem_session_factory() as s:
        for count in (5, 3):
            s.add(DOECycleRunRow(project_id="proj-1", domain="anticorrosion_coating",
                                 engine="baybe", prior_measurement_count=0,
                                 experiment_count=count, status="success"))
        s.commit()
    monkeypatch.setattr("app.db.database.default_session_factory", lambda: mem_session_factory)
    monkeypatch.setattr("app.services.training.registry",
                        SimpleNamespace(records_for=lambda d, project_id="": []))
    resp = doe_api.doe_cycle_runs(project_id="proj-1", limit=1)
    assert len(resp.items) == 1
    assert resp.summary["cycle_count"] == 2
    assert resp.summary["total_experiments"] == 8
