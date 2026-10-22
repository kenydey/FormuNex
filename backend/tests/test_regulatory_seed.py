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
        # P1-23：明确标注非目录的条目（行号 2730、真实数据同款标记）永不写序号。
        {"index": 2730, "chinese_name": "黄樟素", "alias": "黄樟素", "english_name": "Safrole",
         "cas_number": "94-59-7", "hazard_category": "易燃液体,类别 3", "notes": "易制毒化学品",
         "source": "非目录条目：不在《危险化学品目录》（2015版）中（本文件附加，待用易制毒/易制爆名录核对）"},
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
    _write(res, "hpstatements_en.json", {"statements": {
        "latest/en/H225": "Highly flammable liquid and vapour.",
        "latest/en/P210": "Keep away from heat, hot surfaces, sparks, open flames and other ignition sources. No smoking.",
        "latest/en/P301+P330+P331": "IF SWALLOWED: Rinse mouth. Do NOT induce vomiting.",
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
        # P1-23：未标注非目录的缺号条目仍回退行号（既有行为，见 acrylic==3）；
        # 明确标注非目录的条目即使有行号也不写序号。
        safrole = c.execute(select(SubstanceRow).where(SubstanceRow.cas_no == "94-59-7")).one()
        assert safrole.hazardous_catalog_no is None and safrole.precursor_control is True
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
        en_texts = dict(c.execute(select(GhsStatementRow.code, GhsStatementRow.text_en)).all())
        assert en_texts["H225"] == "Highly flammable liquid and vapour."
        assert en_texts["P301+P330+P331"] == "IF SWALLOWED: Rinse mouth. Do NOT induce vomiting."
        # No English entry in the fixture file -> stays blank, never invented.
        assert en_texts["H300+H310+H330"] == ""
        linked = c.execute(select(SubstanceTransportRow.un_number, SubstanceTransportRow.substance_id)).all()
        assert {un: sid is not None for un, sid in linked} == {"1170": True, "9999": False}
        generic = c.execute(select(SubstanceGenericEntryRow.name_zh, SubstanceGenericEntryRow.parsed)).one()
        assert generic.name_zh == "氰化物" and generic.parsed[0]["hazard_class"] == "acute_tox_oral"
        kinds = dict(c.execute(select(RegulatoryReviewItem.kind, func.count()).group_by(RegulatoryReviewItem.kind)).all())
    assert kinds == {
        "invalid_cas": 1, "repaired_cas": 1, "duplicate_catalog_entry": 1, "hazard_text": 1,
        "oel_unmatched": 1, "transport_unlinked": 1,
    }


def test_bundled_non_catalog_entries_never_get_a_catalog_no():
    """P1-23：真实捆绑文件中的 29 条非目录条目，种子逻辑一律不给序号。

    只读断言（不跑全量种子、不改数据）：逐条核对 source 标记与
    _catalog_no 回退，29 条全为空；其余 232 条无 catalog_no 条目仍
    保留行号回退（缺号 ≠ 明确非目录，两情形必须分开）。
    """
    from pathlib import Path

    from app.services.plm.regulatory.seed import _catalog_no

    res = Path(__file__).resolve().parents[1] / "app" / "resources" / "regulatory"
    chemicals = json.loads((res / "hazard_chemical_info.json").read_text(encoding="utf-8"))["chemicals"]
    non_catalog = [c for c in chemicals if str(c.get("source") or "").startswith("非目录条目")]
    assert len(non_catalog) == 29
    assert all(_catalog_no(c) is None for c in non_catalog)
    assert {c.get("cas_number") for c in non_catalog} >= {"94-59-7"}
    unnumbered_unmarked = [c for c in chemicals if not c.get("catalog_no") and not c.get("source")]
    assert len(unnumbered_unmarked) == 232
    assert all(_catalog_no(c) == c.get("index") for c in unnumbered_unmarked)


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


def test_reseed_backfills_english_and_never_overwrites_human_text_en(engine, resources):
    seed_regulatory_db(engine, resources=resources)
    with engine.begin() as c:
        # Simulate a human-edited English text and a blank one.
        c.execute(update(GhsStatementRow).where(GhsStatementRow.code == "H225")
                  .values(text_en="HUMAN EDIT"))
        c.execute(update(GhsStatementRow).where(GhsStatementRow.code == "P210").values(text_en=""))
    seed_regulatory_db(engine, resources=resources, force=True)
    with engine.connect() as c:
        texts = dict(c.execute(select(GhsStatementRow.code, GhsStatementRow.text_en)).all())
    assert texts["H225"] == "HUMAN EDIT"
    assert texts["P210"] == (
        "Keep away from heat, hot surfaces, sparks, open flames and other ignition sources. No smoking.")


# Codes in the bundled Chinese seed files with no trustworthy English text.
# All are GHS-only (CLP does not adopt Cat 5 / Skin 3 / Eye 2B / Asp. Tox. 2 /
# Aquatic Acute 3 / Flam. Liq. 4), deleted P codes, or non-standard codes
# (H361E is not a GHS code — its Chinese text duplicates H361d). They keep
# text_en empty until a UN GHS source is provided; this set must be updated
# deliberately, never by inventing text.
KNOWN_MISSING_EN = {
    "H227", "H303", "H303+H313", "H303+H313+H333", "H303+H333", "H305", "H313",
    "H313+H333", "H315+H320", "H316", "H320", "H333", "H361E", "H401", "H402",
    "P221", "P224", "P225", "P226", "P235+P410", "P281", "P304+P312", "P335+P334",
    "P370+P380", "P374", "P411+P235", "P422",
}
# MSDS-Generator hand-added entries, excluded from the vendored English file:
# their provenance is a maintainer edit, not the EUR-Lex extraction.
EXCLUDED_EN = {"H303", "H313", "H350I", "H360E", "H401", "P281"}


def test_english_resource_covers_seed_codes_except_known_gap():
    from pathlib import Path

    from app.services.plm.regulatory.seed import _code

    res = Path(__file__).resolve().parents[1] / "app" / "resources" / "regulatory"
    en = json.loads((res / "hpstatements_en.json").read_text(encoding="utf-8"))
    assert en["_formunex"]["excluded_codes"] == sorted(EXCLUDED_EN)
    en_codes = {_code(k.split("/")[-1]) for k in en["statements"]}
    # Hand-added entries (except H350I, which the upstream file already carries as
    # H350i) must not be in the English file.
    assert not (en_codes & {"H303", "H313", "H360E", "H401", "P281"})
    assert "H350I" in en_codes  # from upstream H350i, normalised by _code
    seed_codes = set()
    for fname in ("hazard_statements.json", "precaution_statements.json"):
        for s in json.loads((res / fname).read_text(encoding="utf-8"))["statements"]:
            seed_codes.add(_code(s.get("code") or ""))
    assert seed_codes - en_codes == KNOWN_MISSING_EN
    # Spot pairs, verified against legislation.gov.uk CLP Annex III/IV.
    texts = {_code(k.split("/")[-1]): v for k, v in en["statements"].items()}
    assert texts["H200"] == "Unstable explosives."
    assert texts["H300+H310+H330"] == "Fatal if swallowed, in contact with skin or if inhaled"
    assert texts["H350I"] == "May cause cancer by inhalation."
    assert texts["P301+P330+P331"] == "IF SWALLOWED: Rinse mouth. Do NOT induce vomiting."
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
