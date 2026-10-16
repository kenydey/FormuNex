"""Regulatory data import: idempotency, preservation of human edits, source priority."""
from __future__ import annotations

import gzip
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select, update

import app.db.models  # noqa: F401  (registers every table)
from app.db.models import Base
from app.db.models_regulatory import (
    GhsStatementRow,
    RegulatoryReviewItem,
    SubstanceClassificationRow,
    SubstanceGenericEntryRow,
    SubstanceOELRow,
    SubstanceRow,
    SubstanceTransportRow,
)
from app.services.plm.regulatory.seed import seed_regulatory_db
from app.services.plm.regulatory.source_priority import effective


def _write(path, name, payload):
    p = path / name
    if name.endswith(".gz"):
        with gzip.open(p, "wt", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    else:
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def resources(tmp_path):
    res = tmp_path / "res"
    res.mkdir()
    _write(res, "common_chemicals.json.gz", {"rows": [
        ["64-17-5", "乙醇", "Ethanol", "C2H6O"],
        ["108-95-2", "苯酚", "Phenol", "C6H6O"],
        ["7732-18-5", "水", "Water", "H2O"],
        ["64-17-6", "坏号", "Bad", ""],  # checksum fails
    ]})
    _write(res, "hazard_chemical_info.json", {"chemicals": [
        {"index": 2568, "chinese_name": "乙醇", "alias": "酒精", "english_name": "ethanol",
         "cas_number": "64-17-5", "hazard_category": "易燃液体,类别 2", "notes": ""},
        {"index": 1, "chinese_name": "苯酚", "alias": "", "english_name": "phenol", "cas_number": "108-95-2",
         "hazard_category": "急性毒性-经口,类别 3*\n皮肤腐蚀/刺激,类别 1B", "notes": ""},
        {"index": 2, "chinese_name": "苯酚溶液", "alias": "", "english_name": "phenol solution",
         "cas_number": "108-95-2", "hazard_category": "皮肤腐蚀/刺激,类别 2", "notes": ""},
        {"index": 3, "chinese_name": "丙烯酸", "alias": "", "english_name": "acrylic acid",
         "cas_number": "2079-10-07 00:00:00", "hazard_category": "易燃液体,类别 3", "notes": "易制爆化学品"},
        {"index": 4, "chinese_name": "氰化物", "alias": "", "english_name": "cyanides", "cas_number": "",
         "hazard_category": "急性毒性-经口,类别 2*", "notes": "剧毒"},
        {"index": 5, "chinese_name": "某物", "alias": "", "english_name": "", "cas_number": "7647-01-0",
         "hazard_category": "有毒固体", "notes": "剧毒"},
    ]})
    _write(res, "oel_gbz2_2019.json", {"records": [
        {"name_cn": "乙醇", "name_en": "Ethanol", "cas_number": "64-17-5", "mac_mg_m3": "",
         "pc_twa_mg_m3": "", "pc_stel_mg_m3": "", "critical_effect": "", "remark": "―"},
        {"name_cn": "氨", "name_en": "Ammonia", "cas_number": "7664-41-7", "mac_mg_m3": "",
         "pc_twa_mg_m3": "20", "pc_stel_mg_m3": "30", "critical_effect": "眼和上呼吸道刺激", "remark": "―"},
        {"name_cn": "某混合物", "name_en": "", "cas_number": "", "mac_mg_m3": "1", "pc_twa_mg_m3": "",
         "pc_stel_mg_m3": "", "critical_effect": "", "remark": ""},
    ]})
    _write(res, "hazard_statements.json", {"statements": [
        {"code": "H225", "statement": "高度易燃液体和蒸气", "hazard_type": "易燃液体", "hazard_category": "类别 2"},
        {"code": "H300+H310+H3 30", "statement": "吞咽、皮肤接触或吸入致命", "hazard_type": "", "hazard_category": ""},
    ]})
    _write(res, "precaution_statements.json", {"statements": [
        {"code": "P210", "statement": "远离热源", "precaution_category": "预防措施", "hazard_type": "",
         "hazard_category": "", "usage_condition": ""},
        {"code": "P301+P330\n+P331", "statement": "如误吞咽：漱口。\n不要催吐。", "precaution_category": "应急措施",
         "hazard_type": "", "hazard_category": "", "usage_condition": ""},
    ]})
    _write(res, "gb12268_2025.json", {"entries": {
        "1170": {"name_cn": "乙醇", "name_en": "Ethanol", "class": "3", "pg": "II", "marine_pollutant": False},
        "9999": {"name_cn": "不存在的物质", "name_en": "", "class": "9", "pg": None},
    }})
    return res


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'reg.db'}")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _count(engine, model, *where):
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(model).where(*where)).scalar_one()


def test_seed_loads_every_table(engine, resources):
    result = seed_regulatory_db(engine, resources=resources)
    stats = result["stats"]
    assert not result["skipped"]
    assert stats["common_invalid_cas"] == 1
    assert stats["catalog_cas_repaired"] == 1
    assert stats["catalog_generic"] == 1
    with engine.connect() as c:
        acrylic = c.execute(select(SubstanceRow).where(SubstanceRow.cas_no == "79-10-7")).one()
        assert acrylic.explosive_precursor is True and acrylic.hazardous_catalog_no == 3
        ethanol = c.execute(select(SubstanceRow).where(SubstanceRow.cas_no == "64-17-5")).one()
        assert ethanol.aliases == ["酒精"] and ethanol.name_en == "Ethanol"
        water = c.execute(select(SubstanceRow).where(SubstanceRow.cas_no == "7732-18-5")).one()
        assert water.hazardous_catalog_no is None and water.iecsc_listed is None
        phenol_cls = c.execute(
            select(SubstanceClassificationRow.hazard_class, SubstanceClassificationRow.category,
                   SubstanceClassificationRow.h_code, SubstanceClassificationRow.is_minimum)
            .join(SubstanceRow, SubstanceRow.id == SubstanceClassificationRow.substance_id)
            .where(SubstanceRow.cas_no == "108-95-2")
            .order_by(SubstanceClassificationRow.hazard_class, SubstanceClassificationRow.category)
        ).all()
        assert [tuple(r) for r in phenol_cls] == [
            ("acute_tox_oral", "3", "H301", True),
            ("skin_corrosion", "1B", "H314", False),
            ("skin_corrosion", "2", "H315", False),
        ]
        ammonia_oel = c.execute(
            select(SubstanceOELRow.pc_twa, SubstanceOELRow.pc_stel)
            .join(SubstanceRow, SubstanceRow.id == SubstanceOELRow.substance_id)
            .where(SubstanceRow.cas_no == "7664-41-7")
        ).one()
        assert tuple(ammonia_oel) == (20.0, 30.0)  # substance created from the OEL list itself
        codes = set(c.execute(select(GhsStatementRow.code)).scalars())
        assert {"H225", "H300+H310+H330", "P210", "P301+P330+P331"} <= codes
        linked = c.execute(select(SubstanceTransportRow.un_number, SubstanceTransportRow.substance_id)).all()
        assert {un: sid is not None for un, sid in linked} == {"1170": True, "9999": False}
        generic = c.execute(select(SubstanceGenericEntryRow.name_zh, SubstanceGenericEntryRow.parsed)).one()
        assert generic.name_zh == "氰化物" and generic.parsed[0]["hazard_class"] == "acute_tox_oral"
        kinds = dict(c.execute(select(RegulatoryReviewItem.kind, func.count()).group_by(RegulatoryReviewItem.kind)).all())
    assert kinds == {
        "invalid_cas": 1, "repaired_cas": 1, "duplicate_catalog_entry": 1, "hazard_text": 1,
        "oel_unmatched": 1, "transport_unlinked": 1,
    }


def test_unchanged_data_is_skipped_and_force_is_idempotent(engine, resources):
    first = seed_regulatory_db(engine, resources=resources)
    again = seed_regulatory_db(engine, resources=resources)
    assert again == {"skipped": True, "release_id": first["release_id"], "stats": first["stats"]}
    counts = [_count(engine, m) for m in (SubstanceRow, SubstanceClassificationRow, SubstanceOELRow,
                                          RegulatoryReviewItem, GhsStatementRow)]
    forced = seed_regulatory_db(engine, resources=resources, force=True)
    assert not forced["skipped"]
    assert [_count(engine, m) for m in (SubstanceRow, SubstanceClassificationRow, SubstanceOELRow,
                                        RegulatoryReviewItem, GhsStatementRow)] == counts


def test_reseed_preserves_locked_rows_names_and_resolved_reviews(engine, resources):
    seed_regulatory_db(engine, resources=resources)
    with engine.begin() as c:
        c.execute(update(SubstanceRow).where(SubstanceRow.cas_no == "64-17-5").values(name_zh="无水乙醇"))
        c.execute(
            update(SubstanceClassificationRow)
            .where(SubstanceClassificationRow.hazard_class == "flammable_liquid",
                   SubstanceClassificationRow.category == "2")
            .values(locked_at=datetime(2026, 10, 1), locked_by="ehs-1")
        )
        c.execute(update(RegulatoryReviewItem).where(RegulatoryReviewItem.kind == "repaired_cas")
                  .values(status="resolved"))
    seed_regulatory_db(engine, resources=resources, force=True)
    with engine.connect() as c:
        assert c.execute(select(SubstanceRow.name_zh).where(SubstanceRow.cas_no == "64-17-5")).scalar_one() == "无水乙醇"
        locked = c.execute(select(SubstanceClassificationRow).where(
            SubstanceClassificationRow.locked_at.isnot(None))).all()
        assert len(locked) == 1 and locked[0].locked_by == "ehs-1"
        flam = c.execute(select(func.count()).select_from(SubstanceClassificationRow).where(
            SubstanceClassificationRow.hazard_class == "flammable_liquid",
            SubstanceClassificationRow.category == "2")).scalar_one()
        assert flam == 1  # not duplicated next to the locked row
    assert _count(engine, RegulatoryReviewItem, RegulatoryReviewItem.kind == "repaired_cas") == 1


def test_seed_without_common_list(engine, resources):
    stats = seed_regulatory_db(engine, resources=resources, include_common=False)["stats"]
    assert "common_rows" not in stats
    assert _count(engine, SubstanceRow, SubstanceRow.cas_no == "7732-18-5") == 0
    assert _count(engine, SubstanceRow, SubstanceRow.cas_no == "64-17-5") == 1


def _row(hazard_class, source, category="1", locked=False):
    return SimpleNamespace(hazard_class=hazard_class, source=source, category=category,
                           locked_at=datetime(2026, 1, 1) if locked else None)


def test_source_priority_is_per_hazard_class():
    rows = [
        _row("acute_tox_oral", "pubchem", "4"),
        _row("acute_tox_oral", "supplier_sds", "3"),
        _row("carcinogenicity", "cn_catalog", "1B"),
        _row("skin_corrosion", "cn_catalog", "1B"),
        _row("skin_corrosion", "pubchem", "2", locked=True),
        _row("reproductive", "cn_catalog", "1B"),
        _row("reproductive", "cn_catalog", "lact"),
    ]
    picked = sorted((r.hazard_class, r.source, r.category) for r in effective(rows))
    assert picked == [
        ("acute_tox_oral", "supplier_sds", "3"),
        ("carcinogenicity", "cn_catalog", "1B"),
        ("reproductive", "cn_catalog", "1B"),
        ("reproductive", "cn_catalog", "lact"),
        ("skin_corrosion", "pubchem", "2"),  # a locked row wins whatever its source
    ]


def test_cli_requires_migrated_database(tmp_path, capsys):
    from scripts.seed_regulatory_db import main

    assert main(["--db-url", f"sqlite:///{tmp_path / 'empty.db'}"]) == 2
    assert "alembic upgrade head" in capsys.readouterr().err
