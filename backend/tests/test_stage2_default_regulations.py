"""Stage 2 / SR-078: new companies default to ["CN"] regulations."""
from __future__ import annotations

import pytest

from app.db.database import Base, make_engine, make_session_factory
from app.db.models_plm import CompanyRow
from app.services.plm import company as company_svc


@pytest.fixture()
def sf(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/default_regs.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


def _base_data(**overrides):
    data = {"name": "测试公司", "emergency_phone": "010-12345678"}
    data.update(overrides)
    return data


def test_model_default_is_cn(sf):
    with sf() as session:
        row = CompanyRow(id="c1", name="A", emergency_phone="010-1")
        session.add(row)
        session.commit()
        assert row.applicable_regulations == ["CN"]


def test_service_create_without_regs_gets_cn(sf):
    with sf() as session:
        row = company_svc.create_company(session, _base_data())
        session.commit()
        assert row.applicable_regulations == ["CN"]


def test_service_create_with_explicit_regs_keeps_them(sf):
    with sf() as session:
        row = company_svc.create_company(
            session, _base_data(applicable_regulations=["CN", "EU"]))
        session.commit()
        assert row.applicable_regulations == ["CN", "EU"]
