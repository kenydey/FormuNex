"""G4 EH40: seeded under its own standard; SDS §8 stays GBZ-only."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.models_regulatory import SubstanceOELRow, SubstanceRow
from app.services.plm.documents.service import _oel


@pytest.fixture()
def sf(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/g4.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()
    get_settings.cache_clear()


def test_oel_helper_returns_gbz_only_when_eh40_present(sf):
    with sf() as s:
        sub = SubstanceRow(id="sub-1", cas_no="75-07-0", name_zh="乙醛")
        s.add(sub)
        s.flush()
        s.add(SubstanceOELRow(id="oel-gbz", substance_id="sub-1", standard="GBZ 2.1-2019",
                              mac=None, pc_twa=20.0, pc_stel=30.0))
        s.add(SubstanceOELRow(id="oel-eh40", substance_id="sub-1", standard="EH40/2005",
                              mac=None, pc_twa=37.0, pc_stel=92.0, notes="8h TWA 20 ppm"))
        s.flush()
        out = _oel(s, ["75-07-0"])
        assert out == {"75-07-0": {"mac": None, "pc_twa": 20.0, "pc_stel": 30.0}}
