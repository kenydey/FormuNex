"""W1-12: API datetime inputs normalize to naive UTC before storage.

SQLite stores datetimes as naive wall time and silently drops the offset,
so the same instant written as ``...+08:00`` and as ``...Z`` used to land
as two different stored values — and a COA date a day apart at the boundary.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from app.clock import parse_iso_to_naive_utc, to_naive_utc
from app.config import get_settings


def test_clock_to_naive_utc():
    assert to_naive_utc(datetime(2026, 10, 15, 0, 30, tzinfo=timezone(timedelta(hours=8)))) == datetime(2026, 10, 14, 16, 30)
    assert to_naive_utc(datetime(2026, 10, 14, 16, 30, tzinfo=timezone.utc)) == datetime(2026, 10, 14, 16, 30)
    assert to_naive_utc(datetime(2026, 10, 14, 16, 30)) == datetime(2026, 10, 14, 16, 30)
    assert to_naive_utc(None) is None


def test_clock_parse_iso():
    assert parse_iso_to_naive_utc("2026-10-15T00:30:00+08:00") == parse_iso_to_naive_utc("2026-10-14T16:30:00Z")
    assert parse_iso_to_naive_utc("2026-10-15") == datetime(2026, 10, 15)
    with pytest.raises(ValueError):
        parse_iso_to_naive_utc("not-a-date")


def test_parse_iso_is_naive_and_comparable():
    from app.api.experiments import _parse_iso
    a = _parse_iso("2026-10-15T00:30:00+08:00")
    b = _parse_iso("2026-10-14T16:30:00Z")
    assert a == b and a is not None and a.tzinfo is None
    assert a <= datetime(2026, 10, 15)  # naive-vs-aware comparison used to raise TypeError


def test_lab_model_datetimes_normalize():
    from app.api.lab import DispatchIn, InstrumentIn, MeasurementIn, TaskIn
    m = MeasurementIn(metric="x", value=1.0, measured_at="2026-10-15T00:30:00+08:00")
    assert m.measured_at is not None and m.measured_at.tzinfo is None
    assert m.measured_at.isoformat() == "2026-10-14T16:30:00"
    assert MeasurementIn(metric="x", value=1.0).measured_at is None
    i = InstrumentIn(asset_no="A1", calibrated_at="2026-10-15T00:30:00+08:00",
                     calibration_due="2026-12-01T00:00:00Z")
    assert i.calibrated_at is not None and i.calibrated_at.isoformat() == "2026-10-14T16:30:00"
    assert i.calibration_due is not None and i.calibration_due.isoformat() == "2026-12-01T00:00:00"
    assert DispatchIn(doe_plan_id="p", metrics=["x"], domain="d",
                      due_at="2026-10-15T00:30:00+08:00").due_at is not None
    assert TaskIn(metrics=["x"], domain="d", due_at="2026-10-15T00:30:00+08:00").due_at is not None
    assert TaskIn(metrics=["x"], domain="d", due_at="2026-10-15T00:30:00+08:00").due_at.isoformat() == "2026-10-14T16:30:00"


@pytest.fixture()
def plm(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'tz.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    monkeypatch.setenv("FORMUNEX_PLM_COMPANY_NAME", "示例化工有限公司")
    get_settings.cache_clear()
    from datetime import datetime as _dt

    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion
    with default_session_factory()() as s:
        s.add(FormulationVersion(id="fv-1", lineage_id="l", version=1, domain="coating", name="v1",
                                 created_at=_dt(2026, 10, 1), snapshot={"ingredients": []}))
        s.commit()
    from app.main import app
    client = TestClient(app)
    pid = client.post("/api/plm/products", json={"code": "FX-100", "name_zh": "FX-100"}).json()["id"]
    rid = client.post(f"/api/plm/products/{pid}/revisions", json={
        "formulation_version_id": "fv-1",
        "spec_snapshot": {"release_specs": [{"metric": "solids", "label": "固含量", "unit": "%", "min": 55, "max": 60}]}}).json()["id"]
    yield client, pid, rid
    get_settings.cache_clear()


def _find_produced_at(node):
    if isinstance(node, dict):
        if node.get("key") == "produced_at" and "value" in node:
            return node["value"]
        for v in node.values():
            found = _find_produced_at(v)
            if found is not None:
                return found
    if isinstance(node, list):
        for v in node:
            found = _find_produced_at(v)
            if found is not None:
                return found
    return None


def test_batch_same_instant_two_notations_one_stored_moment(plm):
    """W1-12 acceptance: Z and +08:00 for the same instant store identically,
    so the COA date no longer differs by a day at the boundary."""
    from app.db.database import default_session_factory
    from app.db.models_plm import BatchRow
    client, pid, rid = plm
    b1 = client.post(f"/api/plm/products/{pid}/batches",
                     json={"batch_no": "B-1", "produced_at": "2026-10-15T00:30:00+08:00"}).json()["id"]
    b2 = client.post(f"/api/plm/products/{pid}/batches",
                     json={"batch_no": "B-2", "produced_at": "2026-10-14T16:30:00Z"}).json()["id"]
    with default_session_factory()() as s:
        p1 = s.get(BatchRow, b1).produced_at
        p2 = s.get(BatchRow, b2).produced_at
    assert p1 == p2 == datetime(2026, 10, 14, 16, 30)
    assert p1 is not None and p1.tzinfo is None
    for bid in (b1, b2):
        client.post(f"/api/plm/batches/{bid}/qc-results", json={"results": [{"metric": "solids", "value": 57.1}]})
        coa = client.post(f"/api/plm/revisions/{rid}/documents", json={"doc_type": "coa", "coa": {"batch_id": bid}}).json()
        assert _find_produced_at(coa["content"]) == "2026-10-14"


def test_batch_naive_produced_at_unchanged(plm):
    """A naive input is already in the naive-UTC convention: no shift."""
    from app.db.database import default_session_factory
    from app.db.models_plm import BatchRow
    client, pid, _ = plm
    bid = client.post(f"/api/plm/products/{pid}/batches",
                      json={"batch_no": "B-3", "produced_at": "2026-10-15"}).json()["id"]
    with default_session_factory()() as s:
        assert s.get(BatchRow, bid).produced_at == datetime(2026, 10, 15)
    bad = client.post(f"/api/plm/products/{pid}/batches", json={"batch_no": "B-4", "produced_at": "not-a-date"})
    assert bad.status_code == 422
