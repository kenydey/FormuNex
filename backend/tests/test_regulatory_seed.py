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
    _write(res, "oel_eh40.json", {"records": [
        {"name_cn": "乙醛", "name_en": "Acetaldehyde", "cas_number": "75-07-0", "oel_8h_ppm": "20",
         "oel_8h_mg_m3": "37", "oel_15m_ppm": "50", "oel_15m_mg_m3": "92"},
        {"name_cn": "铝金属可吸入粉尘", "name_en": "Aluminium metal inhalable dust", "cas_number": "7429-90-5",
         "oel_8h_ppm": "", "oel_8h_mg_m3": "10", "oel_15m_ppm": "", "oel_15m_mg_m3": ""},
        {"name_cn": "铝金属可吸入性粉尘", "name_en": "Aluminium metal respirable dust", "cas_number": "7429-90-5",
         "oel_8h_ppm": "", "oel_8h_mg_m3": "4", "oel_15m_ppm": "", "oel_15m_mg_m3": ""},
        {"name_cn": "无号物质", "name_en": "", "cas_number": "", "oel_8h_ppm": "", "oel_8h_mg_m3": "1",
         "oel_15m_ppm": "", "oel_15m_mg_m3": ""},
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
        # W1-9：未核实的条目不再回退文件行号——丙烯酸 fixture 只有 index 3、无
        # catalog_no，种子后序号为空（§15 不宣称），待 232 条逐条核对后再填实。
        assert acrylic.explosive_precursor is True and acrylic.hazardous_catalog_no is None
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
        # G4: EH40 rows seed under their own standard; ppm rides in notes and
        # a same-CAS variant (respirable dust) appends to the kept row.
        eh40 = c.execute(
            select(SubstanceOELRow.standard, SubstanceOELRow.pc_twa, SubstanceOELRow.pc_stel,
                   SubstanceOELRow.notes)
            .join(SubstanceRow, SubstanceRow.id == SubstanceOELRow.substance_id)
            .where(SubstanceRow.cas_no.in_(["75-07-0", "7429-90-5"]))
            .order_by(SubstanceRow.cas_no)
        ).all()
        assert eh40[0][0] == "EH40/2005" and eh40[0][1] == 10.0
        assert "respirable dust" in eh40[0][3] and "4 mg/m³" in eh40[0][3]
        # W1-9：多变体 notes 分行存（不再用"；"拼后截断）。
        assert eh40[1] == ("EH40/2005", 37.0, 92.0, "8h TWA 20 ppm\n15min STEL 50 ppm")
        assert stats["eh40_rows"] == 2 and stats["eh40_unmatched"] == 1
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
        "oel_unmatched": 1,  # "不存在的物质" (UN 9999) names no substance: unlinked, no review item
        "eh40_unmatched": 1,
    }


def test_bundled_non_catalog_entries_never_get_a_catalog_no():
    """P1-23 + W1-9：真实捆绑文件中的 29 条非目录条目，种子逻辑一律不给序号；
    其余 232 条无 catalog_no 条目同样为空——W1-9 起行号回退已删除，未核实
    的序号不再宣称（空 ≠ 宣称未列入，只是未核实；232 条逐条核对完成前
    不得据此断言它们列入与否，核对后逐条填实）。

    只读断言（不跑全量种子、不改数据）：逐条核对 source 标记与
    _catalog_no 回退，29 条与 232 条全为空。
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
    assert all(_catalog_no(c) is None for c in unnumbered_unmarked)


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


# Codes in the bundled Chinese seed files the CLP file does not cover: GHS-only categories (CLP does
# not adopt Cat 5 / Skin 3 / Eye 2B / Asp. Tox. 2 / Aquatic Acute 3 / Flam. Liq. 4) and P codes
# deleted after GHS Rev.4. hpstatements_en_ghs.json supplies them from UN GHS Rev.4 annex 3.
CLP_GAP = {
    "H227", "H303", "H303+H313", "H303+H313+H333", "H303+H333", "H305", "H313",
    "H313+H333", "H315+H320", "H316", "H320", "H333", "H401", "H402",
    "P221", "P235+P410", "P281", "P304+P312", "P335+P334",
    "P370+P380", "P374", "P411+P235", "P422",
}
# Still without trustworthy English text: none at present. History: H361E was not a GHS code
# (its Chinese text duplicated H361d) and was deleted from hazard_statements.json 2026-10-26;
# P281 left this set 2026-10-26 when its historical GHS text was filled into
# hpstatements_en_ghs.json. This set must be updated deliberately, never by inventing text.
KNOWN_MISSING_EN = set()
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
    assert seed_codes - en_codes == CLP_GAP
    ghs = json.loads((res / "hpstatements_en_ghs.json").read_text(encoding="utf-8"))
    ghs_codes = {_code(k) for k in ghs["statements"]}
    assert not (ghs_codes & en_codes)  # only fills gaps
    assert seed_codes - en_codes - ghs_codes == KNOWN_MISSING_EN
    assert set(ghs["_source"]["not_covered"]) == KNOWN_MISSING_EN
    # Spot pairs, verified against ST/SG/AC.10/30/Rev.4 annex 3 (and Rev.10 where still listed).
    g = {k: v["text"] for k, v in ghs["statements"].items()}
    assert g["H303+H313+H333"] == "May be harmful if swallowed, in contact with skin or if inhaled"
    assert g["H401"] == "Toxic to aquatic life"
    assert g["P374"] == "Fight fire with normal precautions from a reasonable distance."
    # P281 predates Rev.4 (deleted before it, merged into P280); its historical GHS text,
    # consistent with the Chinese seed text 使用所需的个人防护设备. MSDS-Generator's P281
    # text duplicates P280 and was deliberately not used.
    assert g["P281"] == "Use personal protective equipment as required."
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


def test_iecsc_list_marks_listed_cas_and_leaves_the_rest_unknown(engine, resources):
    _write(resources, "iecsc.json.gz", {"cas": {"64-17-5": "2013", "7664-41-7": "2013"}})
    stats = seed_regulatory_db(engine, resources=resources)["stats"]
    assert stats["iecsc_cas"] == 2 and stats["iecsc_marked"] == 2  # ethanol + ammonia (created by the OEL list)

    def listed(cas):
        with engine.connect() as c:
            return c.execute(select(SubstanceRow.iecsc_listed).where(SubstanceRow.cas_no == cas)).scalar_one()

    assert listed("64-17-5") is True and listed("7664-41-7") is True
    assert listed("7732-18-5") is None  # not in the public list: may be confidential, so unknown — never False
    with engine.begin() as c:
        c.execute(update(SubstanceRow).where(SubstanceRow.cas_no == "108-95-2").values(iecsc_listed=False))
    # A re-run follows the list: a CAS dropped from it goes back to unknown; a manual False is not touched.
    _write(resources, "iecsc.json.gz", {"cas": {"7664-41-7": "2013"}})
    seed_regulatory_db(engine, resources=resources)
    assert listed("64-17-5") is None and listed("7664-41-7") is True and listed("108-95-2") is False


def test_bundled_iecsc_resource_is_sane():
    from app.services.plm.regulatory import cas as cas_mod
    from app.services.plm.regulatory.seed import RESOURCES_DIR, _load_json

    data = _load_json(RESOURCES_DIR / "iecsc.json.gz")
    listed = data["cas"]
    assert len(listed) > 38000
    assert all(cas_mod.is_valid(c) for c in listed)
    # 2013 版: 42,342 public rows, each either a CAS, a 流水号 or a misprint kept as printed.
    base = next(s for s in data["meta"]["sources"] if s["id"] == "2013")
    assert base["entries"] == 42342
    misprints = [i for i in data["invalid"] if i["source"] == "2013"]
    assert base["cas"] + sum(1 for v in data["serials"].values() if v == "2013") + len(misprints) == 42342
    for cas in ("64-17-5", "7732-18-5", "108-95-2", "7664-41-7"):  # ethanol, water, phenol, ammonia
        assert listed[cas] == "2013"
    assert any(src != "2013" for src in listed.values())  # later 增补 / 新化学物质 公告 are included


def test_sds_section_15_names_iecsc_listed_ingredients(engine, resources):
    from sqlalchemy.orm import Session

    from app.services.plm.documents.service import _listings

    _write(resources, "iecsc.json.gz", {"cas": {"64-17-5": "2013"}})
    seed_regulatory_db(engine, resources=resources)
    with Session(engine) as s:
        lines = _listings(s, ["64-17-5", "7732-18-5"])
    assert "乙醇（64-17-5）：列入《中国现有化学物质名录》" in lines
    assert not any("水" in line and "现有化学物质" in line for line in lines)


def test_full_gb12268_rows_link_by_name_and_flag_only_ambiguous_names(engine, resources):
    _write(resources, "common_chemicals.json.gz", {"rows": [
        ["64-17-5", "乙醇", "Ethanol", "C2H6O"],
        ["71-41-0", "戊醇", "1-Pentanol", "C5H12O"],
        ["6032-29-7", "戊醇", "2-Pentanol", "C5H12O"],
    ]})
    _write(resources, "gb12268_2025.json", {"entries": [
        {"un": "1170", "name_cn": "乙醇", "name_en": "ETHANOL", "class": "3", "subsidiary": "", "pg": "II",
         "special_provisions": "144 601"},
        {"un": "1170", "name_cn": "乙醇", "name_en": "ETHANOL", "class": "3", "subsidiary": "", "pg": "III",
         "special_provisions": "144 223 601"},
        {"un": "1105", "name_cn": "戊醇", "name_en": "PENTANOLS", "class": "3", "subsidiary": "", "pg": "II"},
        {"un": "1105", "name_cn": "戊醇", "name_en": "PENTANOLS", "class": "3", "subsidiary": "", "pg": "III"},
        {"un": "1993", "name_cn": "易燃液体，未另作规定的", "name_en": "FLAMMABLE LIQUID, N.O.S.", "class": "3",
         "subsidiary": "", "pg": "III"},
    ]})
    stats = seed_regulatory_db(engine, resources=resources)["stats"]
    assert (stats["transport_rows"], stats["transport_linked"], stats["transport_ambiguous"]) == (5, 2, 1)
    with engine.connect() as c:
        rows = c.execute(select(SubstanceTransportRow.un_number, SubstanceTransportRow.packing_group,
                                SubstanceTransportRow.substance_id, SubstanceTransportRow.special_provisions)
                         .order_by(SubstanceTransportRow.un_number, SubstanceTransportRow.packing_group)).all()
        reviews = c.execute(select(RegulatoryReviewItem.name).where(
            RegulatoryReviewItem.kind == "transport_unlinked")).scalars().all()
    assert [(r.un_number, r.packing_group, r.substance_id is not None) for r in rows] == [
        ("1105", "II", False), ("1105", "III", False), ("1170", "II", True), ("1170", "III", True),
        ("1993", "III", False)]
    assert rows[3].special_provisions == "144 223 601"
    assert reviews == ["戊醇"]  # two substances share the name; the generic UN 1993 raises nothing


def test_gb12268_alias_links_only_unambiguous_simple_names():
    from app.services.plm.regulatory.seed import _name_aliases

    assert _name_aliases("乙醇（酒精）或乙醇溶液（酒精溶液）") == ["乙醇", "酒精", "乙醇溶液", "酒精溶液"]
    assert _name_aliases("易燃液体，未另作规定的") == []
    assert _name_aliases("硝酸，发红烟的除外，含硝酸低于65%") == []
    assert _name_aliases("丙酮") == []


def test_person_decided_gb12268_links_win_over_name_matching(engine, resources):
    _write(resources, "common_chemicals.json.gz", {"rows": [
        ["67-56-1", "甲醇", "Methanol", "CH4O"],
        ["170082-17-4", "甲醇", "METHANOL", "CH4O"],
        ["12013-55-7", "硅化钙", "Calcium silicide", "CaSi"],
        ["12013-56-8", "硅化钙", "Calcium silicide", "CaSi2"],
        ["7553-56-2", "碘", "Iodine", "I2"],
        ["12190-71-5", "碘", "Iodine", "I2"],
    ]})
    _write(resources, "gb12268_2025.json", {"entries": [
        {"un": "1230", "name_cn": "甲醇", "name_en": "METHANOL", "class": "3", "pg": "II"},
        {"un": "1405", "name_cn": "硅化钙", "name_en": "CALCIUM SILICIDE", "class": "4.3", "pg": "II"},
        {"un": "1405", "name_cn": "硅化钙", "name_en": "CALCIUM SILICIDE", "class": "4.3", "pg": "III"},
        {"un": "3495", "name_cn": "碘", "name_en": "IODINE", "class": "8", "pg": "III"},
    ]})
    _write(resources, "gb12268_links.json", {"links": [
        {"un": "1230", "name_cn": "甲醇", "cas": ["67-56-1"]},
        {"un": "1405", "name_cn": "硅化钙", "cas": ["12013-55-7", "12013-56-8"]},
        {"un": "3495", "name_cn": "碘", "cas": ["7553-56-2", "9999-99-9"]},  # second CAS not in the library
    ]})
    stats = seed_regulatory_db(engine, resources=resources)["stats"]
    assert stats["transport_decided"] == 4 and stats["transport_ambiguous"] == 0 and stats["transport_link_missing"] == 1
    with engine.connect() as c:
        got = c.execute(select(SubstanceTransportRow.un_number, SubstanceTransportRow.packing_group, SubstanceRow.cas_no)
                        .join(SubstanceRow, SubstanceRow.id == SubstanceTransportRow.substance_id, isouter=True)
                        .order_by(SubstanceTransportRow.un_number, SubstanceTransportRow.packing_group,
                                  SubstanceRow.cas_no)).all()
        kinds = dict(c.execute(select(RegulatoryReviewItem.kind, func.count()).group_by(RegulatoryReviewItem.kind)).all())
    assert [tuple(r) for r in got] == [
        ("1230", "II", "67-56-1"),
        ("1405", "II", "12013-55-7"), ("1405", "II", "12013-56-8"),     # one row per chosen substance
        ("1405", "III", "12013-55-7"), ("1405", "III", "12013-56-8"),
        ("3495", "III", "7553-56-2"),
    ]
    assert "transport_unlinked" not in kinds and kinds["transport_link_missing"] == 1


def test_an_empty_decision_leaves_the_entry_deliberately_unlinked(engine, resources):
    _write(resources, "common_chemicals.json.gz", {"rows": [["67-56-1", "甲醇", "Methanol", "CH4O"],
                                                           ["170082-17-4", "甲醇", "METHANOL", "CH4O"]]})
    _write(resources, "gb12268_2025.json", {"entries": [{"un": "1230", "name_cn": "甲醇", "class": "3", "pg": "II"}]})
    _write(resources, "gb12268_links.json", {"links": [{"un": "1230", "name_cn": "甲醇", "cas": []}]})
    seed_regulatory_db(engine, resources=resources)
    with engine.connect() as c:
        assert c.execute(select(SubstanceTransportRow.substance_id)).scalars().all() == [None]
        assert c.execute(select(func.count()).select_from(RegulatoryReviewItem)
                         .where(RegulatoryReviewItem.kind == "transport_unlinked")).scalar_one() == 0


def test_a_name_borrowed_from_another_catalogue_substance_is_replaced(engine, resources):
    # The common list calls KO₂ (12030-88-5) 过氧化钾, the catalogue name of K₂O₂ (17014-71-0).
    _write(resources, "common_chemicals.json.gz", {"rows": [
        ["12030-88-5", "过氧化钾", "Potassium superoxide", "KO2"],
        ["17014-71-0", "过氧化钾", "Potassium peroxide", "K2O2"],
        ["111-40-0", "二乙烯三胺", "Diethylenetriamine", "C4H13N3"],  # a synonym, not borrowed: kept
    ]})
    _write(resources, "hazard_chemical_info.json", {"chemicals": [
        {"index": 160, "catalog_no": 159, "chinese_name": "超氧化钾", "alias": "", "english_name": "potassium superoxide",
         "cas_number": "12030-88-5", "hazard_category": "氧化性固体,类别 1", "notes": ""},
        {"index": 888, "catalog_no": 894, "chinese_name": "过氧化钾", "alias": "", "english_name": "potassium peroxide",
         "cas_number": "17014-71-0", "hazard_category": "氧化性固体,类别 1", "notes": ""},
        {"index": 637, "catalog_no": 636, "chinese_name": "二亚乙基三胺", "alias": "", "english_name": "diethylenetriamine",
         "cas_number": "111-40-0", "hazard_category": "皮肤腐蚀/刺激,类别 1B", "notes": ""},
    ]})
    _write(resources, "gb12268_2025.json", {"entries": [
        {"un": "1491", "name_cn": "过氧化钾", "name_en": "POTASSIUM PEROXIDE", "class": "5.1", "pg": "I"},
        {"un": "2466", "name_cn": "超氧化钾", "name_en": "POTASSIUM SUPEROXIDE", "class": "5.1", "pg": "I"},
    ]})

    def names():
        with engine.connect() as c:
            return dict(c.execute(select(SubstanceRow.cas_no, SubstanceRow.name_zh).where(
                SubstanceRow.cas_no.in_(["12030-88-5", "17014-71-0", "111-40-0"]))).all())

    stats = seed_regulatory_db(engine, resources=resources)["stats"]
    assert stats["catalog_borrowed_names_fixed"] == 1
    assert names() == {"12030-88-5": "超氧化钾", "17014-71-0": "过氧化钾", "111-40-0": "二乙烯三胺"}
    with engine.connect() as c:
        linked = dict(c.execute(select(SubstanceTransportRow.un_number, SubstanceRow.cas_no)
                                .join(SubstanceRow, SubstanceRow.id == SubstanceTransportRow.substance_id)).all())
        unlinked = c.execute(select(func.count()).select_from(RegulatoryReviewItem)
                             .where(RegulatoryReviewItem.kind == "transport_unlinked")).scalar_one()
    assert linked == {"1491": "17014-71-0", "2466": "12030-88-5"} and unlinked == 0

    # A database written by an earlier import still carries the wrong name: re-import corrects it,
    # while a name a person edited in the application stays.
    with engine.begin() as c:
        c.execute(update(SubstanceRow).where(SubstanceRow.cas_no == "12030-88-5").values(name_zh="过氧化钾"))
        c.execute(update(SubstanceRow).where(SubstanceRow.cas_no == "111-40-0").values(name_zh="二乙撑三胺"))
    seed_regulatory_db(engine, resources=resources, force=True)
    assert names()["12030-88-5"] == "超氧化钾" and names()["111-40-0"] == "二乙撑三胺"


def test_un_ghs_english_fills_clp_gaps_and_a_misnumbered_chinese_text_is_corrected(engine, resources):
    _write(resources, "hazard_statements.json", {"statements": [
        {"code": "H225", "statement": "高度易燃液体和蒸气", "hazard_type": "易燃液体", "hazard_category": "类别 2"},
        {"code": "H303", "statement": "吞咽可能有害", "hazard_type": "急性毒性-经口", "hazard_category": "5"},
    ]})
    _write(resources, "precaution_statements.json", {"statements": [
        {"code": "P220", "statement": "避开/贮存处远离服装/……/可燃材料"},
        {"code": "P221", "statement": "采取一切防范措施，避免与可燃物/……混合"},
    ]})
    _write(resources, "hpstatements_en_ghs.json", {"statements": {
        "H303": {"text": "May be harmful if swallowed"},
        "H225": {"text": "never used: the CLP text wins"},
    }})
    seed_regulatory_db(engine, resources=resources)

    def texts():
        with engine.connect() as c:
            return {r.code: (r.text_zh, r.text_en) for r in c.execute(select(GhsStatementRow))}

    got = texts()
    assert got["H303"] == ("吞咽可能有害", "May be harmful if swallowed")
    assert got["H225"][1] == "Highly flammable liquid and vapour."
    assert got["P221"][0] == "采取一切防范措施，避免与可燃物/……混合"

    # An earlier import wrote P220's text under P221 (misnumbered source row): corrected on re-import.
    # A Chinese text edited in the application (not another code's text) stays.
    with engine.begin() as c:
        c.execute(update(GhsStatementRow).where(GhsStatementRow.code == "P221")
                  .values(text_zh="避开/贮存处远离服装/……/可燃材料"))
        c.execute(update(GhsStatementRow).where(GhsStatementRow.code == "H303").values(text_zh="人工修改"))
    seed_regulatory_db(engine, resources=resources, force=True)
    got = texts()
    assert got["P221"][0] == "采取一切防范措施，避免与可燃物/……混合"
    assert got["H303"][0] == "人工修改"
