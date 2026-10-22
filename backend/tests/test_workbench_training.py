"""Tests for workbench → ModelRegistry auto-ingest (Sprint 1)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.campaign_store import LocalCampaignStore, reset_campaign_store
from app.db.database import make_engine, make_session_factory
from app.db.store import JsonExperimentStore
from app.domain.schemas import DOEPlan, DOERun, ExperimentRecord, ProductDomain, Requirement, ObjectiveSpec
from app.main import app
from app.services.training import ModelRegistry, registry
from app.services import workbench_training


def _plan() -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[],
        runs=[
            DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0, "cure_temperature_c": 80.0}),
        ],
        notes="test",
        plan_id="abc12345",
        domain=ProductDomain.anticorrosion_coating,
    )


@pytest.fixture(autouse=True)
def _sqlite_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUNEX_CAMPAIGN_BACKEND", "sqlite")
    monkeypatch.setenv("FORMUNEX_WORKBENCH_AUTO_TRAIN", "true")
    get_settings.cache_clear()
    db_path = tmp_path / "wb_train.db"
    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    reset_campaign_store(LocalCampaignStore(factory))
    store = JsonExperimentStore(str(tmp_path / "exp.json"))
    registry._store = store  # noqa: SLF001
    registry.load()
    yield
    reset_campaign_store(None)
    get_settings.cache_clear()


def test_row_to_experiment_record_completed():
    from app.db.campaign_types import WorkbenchRow

    row = WorkbenchRow(
        id=1,
        campaign_id=1,
        item_id="local_c1_r1",
        status="Completed",
        planned_params={"Zinc phosphate": 8.0},
        actual_params={"Zinc phosphate": 8.5, "cure_temperature_c": 81.0},
        measurements={"salt_spray_hours": 900.0},
    )
    rec = workbench_training.row_to_experiment_record(
        row, campaign_id=1, domain=ProductDomain.anticorrosion_coating
    )
    assert rec is not None
    assert rec.measured["salt_spray_hours"] == 900.0
    assert rec.label == "wb:1:local_c1_r1"


def test_numeric_measured_reports_dropped_values():
    report: list[str] = []
    out = workbench_training._numeric_measured(
        {"salt_spray_hours": "900.5", "cost_cny_per_kg": "abc", "voc_gpl": None, "ph_value": ""},
        report=report,
    )
    assert out == {"salt_spray_hours": 900.5}
    assert "non_numeric:cost_cny_per_kg" in report
    assert "empty:voc_gpl" in report
    assert "empty:ph_value" in report


def test_row_to_experiment_record_reports_non_numeric_factor():
    from app.db.campaign_types import WorkbenchRow

    row = WorkbenchRow(
        id=1,
        campaign_id=1,
        item_id="local_c1_r1",
        status="Completed",
        planned_params={"Zinc phosphate": 8.0},
        actual_params={"Zinc phosphate": 8.5, "cure_temperature_c": 81.0, "surfactant": "not-a-number"},
        measurements={"salt_spray_hours": 900.0},
    )
    report: list[str] = []
    rec = workbench_training.row_to_experiment_record(
        row, campaign_id=1, domain=ProductDomain.anticorrosion_coating, report=report
    )
    assert rec is not None
    assert "non_numeric_factor:surfactant" in report


def test_sync_workbench_ingests_training(tmp_path):
    client = TestClient(app)
    created = client.post("/api/experiments/workbench/campaigns", json={"plan": _plan().model_dump()}).json()
    campaign_id = created["campaign_id"]
    row = created["rows"][0]
    before = registry.total_records

    sync = client.put(
        "/api/experiments/workbench/sync",
        json={
            "campaign_id": campaign_id,
            "rows": [
                {
                    "id": row["id"],
                    "status": "Pending",
                    "actual_params": {"Zinc phosphate": 8.5, "cure_temperature_c": 81.0},
                    "measurements": {"salt_spray_hours": 860.0},
                }
            ],
        },
    )
    assert sync.status_code == 200
    body = sync.json()
    assert body["training_ingested"] == 1
    assert registry.total_records == before + 1

    # Idempotent second sync
    sync2 = client.put(
        "/api/experiments/workbench/sync",
        json={
            "campaign_id": campaign_id,
            "rows": [body["rows"][0]],
        },
    )
    assert sync2.json()["training_ingested"] == 0


def _rec(label: str, value: float, **kw) -> ExperimentRecord:
    return ExperimentRecord(
        domain=ProductDomain.anticorrosion_coating,
        project_id="p1",
        factors={"Zinc phosphate": 8.5},
        cure_temperature_c=81.0,
        measured={"salt_spray_hours": value},
        source="workbench",
        label=label,
        **kw,
    )


def test_registry_add_dedups_label_and_updates_corrected_value(tmp_path):
    """P1-9/P1-10/P1-11 at the registry layer (JSON store)."""
    reg = ModelRegistry(path=str(tmp_path / "reg.json"))
    reg.add([_rec("wb:1:r1", 755.0)])
    assert reg.total_records == 1
    reg.add([_rec("wb:1:r1", 755.0)])  # identical re-add: no duplicate
    assert reg.total_records == 1
    reg.add([_rec("wb:1:r1", 800.0)])  # corrected value: update in place
    assert reg.total_records == 1
    assert reg.all_records()[0].measured["salt_spray_hours"] == 800.0
    # Same batch twice, latest wins, still one record.
    reg.add([_rec("wb:1:r2", 700.0), _rec("wb:1:r2", 710.0)])
    assert reg.total_records == 2
    assert [r for r in reg.all_records() if r.label == "wb:1:r2"][0].measured["salt_spray_hours"] == 710.0
    # Labelless records: an identical repeat is skipped, a different value is new.
    reg.add([_rec("", 500.0)])
    assert reg.total_records == 3
    reg.add([_rec("", 500.0)])
    assert reg.total_records == 3
    reg.add([_rec("", 501.0)])
    assert reg.total_records == 4
    # Restart reload keeps the deduped set (store upsert, not append).
    reg2 = ModelRegistry(path=str(tmp_path / "reg.json"))
    assert reg2.total_records == 4
    assert [r for r in reg2.all_records() if r.label == "wb:1:r1"][0].measured["salt_spray_hours"] == 800.0


def test_local_store_add_upserts_by_label(tmp_path):
    """P1-9 at the SQL layer: ledger row + registry row must not become two rows."""
    from app.db.database import Base, make_engine, make_session_factory
    from app.db.models import ExperimentRow
    from app.db.store import LocalExperimentStore

    engine = make_engine(f"sqlite:///{tmp_path}/local.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = LocalExperimentStore(factory)
    store.add([_rec("wb:1:r1", 755.0)])
    store.add([_rec("wb:1:r1", 755.0)])
    assert store.count() == 1
    store.add([_rec("wb:1:r1", 800.0)])
    assert store.count() == 1
    assert store.all()[0].measured["salt_spray_hours"] == 800.0
    with factory() as session:
        rows = session.query(ExperimentRow).filter(ExperimentRow.label == "wb:1:r1").all()
        assert len(rows) == 1


def test_sync_updates_corrected_value_instead_of_skipping(tmp_path):
    """P1-10 end to end: 755 → 800 re-sync updates the training record."""
    client = TestClient(app)
    created = client.post("/api/experiments/workbench/campaigns", json={"plan": _plan().model_dump()}).json()
    campaign_id = created["campaign_id"]
    row = created["rows"][0]
    before = registry.total_records

    def _sync(value: float):
        return client.put(
            "/api/experiments/workbench/sync",
            json={
                "campaign_id": campaign_id,
                "rows": [
                    {
                        "id": row["id"],
                        "status": "Pending",
                        "actual_params": {"Zinc phosphate": 8.5, "cure_temperature_c": 81.0},
                        "measurements": {"salt_spray_hours": value},
                    }
                ],
            },
        )

    assert _sync(755.0).json()["training_ingested"] == 1
    assert registry.total_records == before + 1
    resync = _sync(800.0).json()
    assert resync["training_ingested"] == 0  # no *new* row
    assert "更新" in resync["training_message"]
    assert registry.total_records == before + 1  # still one record, not two
    recs = [r for r in registry.all_records() if r.label.startswith(f"wb:{campaign_id}:")]
    assert len(recs) == 1
    assert recs[0].measured["salt_spray_hours"] == 800.0
    # A third, identical sync is a plain skip again.
    assert _sync(800.0).json()["training_ingested"] == 0
    assert registry.total_records == before + 1
