"""G6 test records: CRUD, CAS grouping, SDS 11/12 additive rendering."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.models_regulatory import SubstanceRow
from app.services.plm import test_records as tr
from app.services.plm.documents.sds_builder import SdsContext, build_sds


@pytest.fixture()
def sf(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/g6.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()
    get_settings.cache_clear()


def _substance(s, cas="7732-18-5", name="水"):
    row = SubstanceRow(id=f"sub-{cas}", cas_no=cas, name_zh=name)
    s.add(row)
    s.flush()
    return row


# --------------------------------------------------------------------------- CRUD


def test_tox_crud_and_validation(sf):
    with sf() as s:
        sub = _substance(s)
        row = tr.add_tox(s, sub.id, {"endpoint": "LD50", "route": "oral", "value": ">2000"})
        assert row.unit == "mg/kg" and row.species == "大鼠"
        with pytest.raises(ValueError, match="已存在"):
            tr.add_tox(s, sub.id, {"endpoint": "LD50", "route": "oral", "value": ">2000"})
        with pytest.raises(ValueError, match="非法值"):
            tr.add_tox(s, sub.id, {"endpoint": "LD99", "route": "oral", "value": "1"})
        with pytest.raises(ValueError, match="不能为空"):
            tr.add_tox(s, sub.id, {"endpoint": "LD50", "route": "oral", "value": "  "})
        with pytest.raises(ValueError, match="物质不存在"):
            tr.add_tox(s, "nope", {"value": "1"})
        tr.delete_tox(s, sub.id, row.id)
        with pytest.raises(ValueError, match="不存在"):
            tr.delete_tox(s, sub.id, row.id)


def test_eco_crud_profile_and_grouping(sf):
    with sf() as s:
        sub = _substance(s)
        eco = tr.add_eco(s, sub.id, {"organism_group": "fish", "endpoint": "LC50", "value": "3",
                                     "duration": "96h", "species": "蓝鳃太阳鱼"})
        assert eco.unit == "mg/L"
        with pytest.raises(ValueError, match="organism_group"):
            tr.add_eco(s, sub.id, {"endpoint": "LC50", "value": "1"})
        with pytest.raises(ValueError, match="至少填写一项"):
            tr.upsert_profile(s, sub.id, {"persistence": "", "bioaccum": "", "mobility": ""})
        prof = tr.upsert_profile(s, sub.id, {"persistence": ">70% 快速降解"})
        prof = tr.upsert_profile(s, sub.id, {"bioaccum": "不易累积"})
        assert prof.persistence == ">70% 快速降解" and prof.bioaccum == "不易累积"
        out = tr.list_records(s, sub.id)
        assert len(out["eco"]) == 1 and out["eco_profile"]["persistence"] == ">70% 快速降解"
        grouped = tr.records_by_cas(s, ["7732-18-5", "000-00-0"])
        assert len(grouped["eco"]["7732-18-5"]) == 1 and "000-00-0" not in grouped["eco"]
        tr.delete_eco(s, sub.id, eco.id)
        assert tr.list_records(s, sub.id)["eco"] == []


# --------------------------------------------------------------------------- SDS rendering


def _sds_with_records():
    ctx = SdsContext(
        product={"name_zh": "测试产品", "code": "T-1"},
        company={"name": "测试公司", "address": "x", "phone": "x", "email": "x",
                 "emergency_phone": "010-12345678"},
        classification={}, statements={},
        components=[{"name": "水", "cas": "7732-18-5", "pct": 100, "hazards": []}],
        tox_records={"7732-18-5": [{"endpoint": "LD50", "route": "oral", "value": ">2000",
                                     "unit": "mg/kg", "species": "大鼠", "method": ""}]},
        eco_records={"7732-18-5": [{"organism_group": "fish", "endpoint": "LC50", "value": "3",
                                     "unit": "mg/L", "duration": "96h", "species": "蓝鳃太阳鱼",
                                     "method": "OECD 203"}]},
        eco_profiles={"7732-18-5": {"persistence": ">70% 快速降解", "bioaccum": "不易累积",
                                     "mobility": ""}},
        revision={"version": "A.1", "date": "2026-10-25"},
    )
    return build_sds(ctx)


def test_sds_sections_11_12_render_records():
    sds = _sds_with_records()
    sec11 = {f["key"]: f for f in sds["sections"][10]["fields"]}
    sec12 = {f["key"]: f for f in sds["sections"][11]["fields"]}
    tox = [f for k, f in sec11.items() if k.startswith("tox_")]
    assert len(tox) == 1 and "LD50（经口）" in "".join(tox[0]["value"])
    assert ">2000 mg/kg" in "".join(tox[0]["value"])
    eco = [f for k, f in sec12.items() if k.startswith("eco_") and not k.startswith("eco_profile_")]
    assert len(eco) == 1 and "鱼类 LC50（96h）" in "".join(eco[0]["value"])
    prof = [f for k, f in sec12.items() if k.startswith("eco_profile_")]
    assert len(prof) == 1 and ">70% 快速降解" in "".join(prof[0]["value"])
    # Mixture-level template fields are untouched by component records.
    assert sec12["persistence"]["value"] != ">70% 快速降解"


def test_sds_without_records_unchanged():
    ctx = SdsContext(
        product={"name_zh": "测试产品", "code": "T-1"},
        company={"name": "测试公司", "address": "x", "phone": "x", "email": "x",
                 "emergency_phone": "010-12345678"},
        classification={}, statements={}, components=[{"name": "水", "cas": "7732-18-5", "pct": 100, "hazards": []}],
        revision={"version": "A.1", "date": "2026-10-25"},
    )
    sds = build_sds(ctx)
    assert not [f for f in sds["sections"][10]["fields"] if f["key"].startswith("tox_")]
    assert not [f for f in sds["sections"][11]["fields"] if f["key"].startswith("eco_")]
