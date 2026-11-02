"""Datalab removal batch 2: the native store still serves workbench
campaigns when the deprecated "sqlite" backend alias is configured.

Salvaged from tests/test_datalab_eln.py (deleted: every other test there
asserted Datalab hard-fail / health-degraded behaviour that no longer
exists); the sqlite-alias contract itself survives the removal.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.campaign_store import LocalCampaignStore, reset_campaign_store
from app.db.database import make_engine, make_session_factory
from app.domain.schemas import DOEPlan, DOERun, ProductDomain
from app.main import app


def _plan() -> DOEPlan:
    return DOEPlan(
        design="lhs",
        factors=[],
        runs=[DOERun(run_id=1, coded={}, natural={"Zinc phosphate": 8.0})],
        notes="test",
        plan_id="eln01",
        domain=ProductDomain.anticorrosion_coating,
    )


@pytest.fixture(autouse=True)
def _reset_stores():
    reset_campaign_store(None)
    get_settings.cache_clear()
    yield
    reset_campaign_store(None)
    get_settings.cache_clear()


def test_sqlite_campaign_still_works_in_dev(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUNEX_CAMPAIGN_BACKEND", "sqlite")
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'dev.db'}")
    factory = make_session_factory(make_engine(f"sqlite:///{tmp_path / 'dev.db'}"))
    reset_campaign_store(LocalCampaignStore(factory))
    client = TestClient(app)
    res = client.post(
        "/api/experiments/workbench/campaigns",
        json={"plan": _plan().model_dump(), "strategy": "DOE-lhs"},
    )
    assert res.status_code == 200
