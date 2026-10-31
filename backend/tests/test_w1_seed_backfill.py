"""W1-9 seed checks: backfill mode, catalog_no fallback, EH40 multi-line notes, review queue API."""
from __future__ import annotations

import gzip
import json
import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, delete, func, select, update

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
from app.services.plm.regulatory.seed import _catalog_no, seed_regulatory_db


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
        {"index": 4, "chinese_name": "氰化物", "alias": "", "english_name": "cyanides", "cas_number": "",
         "hazard_category": "急性毒性-经口,类别 2*", "notes": "剧毒"},
        {"index": 2730, "chinese_name": "黄樟素", "alias": "黄樟素", "english_name": "Safrole",
         "cas_number": "94-59-7", "hazard_category": "易燃液体,类别 3", "notes": "易制毒化学品",
         "source": "非目录条目：不在《危险化学品目录》（2015版）中"},
    ]})
    _write(res, "oel_gbz2_2019.json", {"records": [
        {"name_cn": "乙醇", "name_en": "Ethanol", "cas_number": "64-17-5", "mac_mg_m3": "",
         "pc_twa_mg_m3": "", "pc_stel_mg_m3": "", "critical_effect": "", "remark": "―"},
        {"name_cn": "氨", "name_en": "Ammonia", "cas_number": "7664-41-7", "mac_mg_m3": "",
         "pc_twa_mg_m3": "20", "pc_stel_mg_m3": "30", "critical_effect": "眼和上呼吸道刺激", "remark": "―"},
        {"name_cn": "无号物质", "name_en": "", "cas_number": "", "mac_mg_m3": "1", "pc_twa_mg_m3": "",
         "pc_stel_mg_m3": "", "critical_effect": "", "remark": ""},
    ]})
    eh40_records = [
        {"name_cn": "乙醛", "name_en": "Acetaldehyde", "cas_number": "75-07-0", "oel_8h_ppm": "20",
         "oel_8h_mg_m3": "37", "oel_15m_ppm": "50", "oel_15m_mg_m3": "92"},
        {"name_cn": "铝金属可吸入粉尘", "name_en": "Aluminium metal inhalable dust", "cas_number": "7429-90-5",
         "oel_8h_ppm": "", "oel_8h_mg_m3": "10", "oel_15m_ppm": "", "oel_15m_mg_m3": ""},
        {"name_cn": "铝金属可吸入性粉尘", "name_en": "Aluminium metal respirable dust", "cas_number": "7429-90-5",
         "oel_8h_ppm": "", "oel_8h_mg_m3": "4", "oel_15m_ppm": "", "oel_15m_mg_m3": ""},
        {"name_cn": "无号物质", "name_en": "", "cas_number": "", "oel_8h_ppm": "", "oel_8h_mg_m3": "1",
         "oel_15m_ppm": "", "oel_15m_mg_m3": ""},
    ]
    # Six variants of one CAS with long names: the old "；" join + 200-char cap
    # would have cut the tail variants' limits off.
    for i in range(6):
        eh40_records.append({
            "name_cn": f"多变体粉尘形态{i}可吸入颗粒物", "name_en": f"Multivariant dust form {i} inhalable particulate",
            "cas_number": "111-11-5", "oel_8h_ppm": str(10 + i), "oel_8h_mg_m3": str(20 + i),
            "oel_15m_ppm": str(30 + i), "oel_15m_mg_m3": str(40 + i)})
    _write(res, "oel_eh40.json", {"records": eh40_records})
    _write(res, "hazard_statements.json", {"statements": [
        {"code": "H225", "statement": "高度易燃液体和蒸气", "hazard_type": "易燃液体", "hazard_category": "类别 2"},
    ]})
    _write(res, "precaution_statements.json", {"statements": [
        {"code": "P210", "statement": "远离热源", "precaution_category": "预防措施", "hazard_type": "",
         "hazard_category": "", "usage_condition": ""},
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


def _counts(engine):
    return [_count(engine, m) for m in (SubstanceRow, SubstanceClassificationRow, SubstanceOELRow,
                                        SubstanceTransportRow, SubstanceGenericEntryRow,
                                        GhsStatementRow, RegulatoryReviewItem)]


def test_catalog_no_never_falls_back_to_file_row_number():
    """W1-9: no catalog_no -> None, even when index exists. Only a verified
    catalog_no is ever written; the 232-case stays empty until verified."""
    assert _catalog_no({"index": 2568, "chinese_name": "乙醇"}) is None
    assert _catalog_no({"index": 3, "catalog_no": 3}) == 3
    assert _catalog_no({"index": 2730, "source": "非目录条目：不在目录中"}) is None


def test_backfill_restores_deleted_rows_and_preserves_edits(engine, resources):
    seed_regulatory_db(engine, resources=resources)
    before = _counts(engine)
    assert before[0] > 0 and all(n > 0 for n in before[1:])
    with engine.begin() as c:
        # A human edit and a resolved queue item must survive the completion pass.
        c.execute(update(SubstanceRow).where(SubstanceRow.cas_no == "64-17-5").values(name_zh="无水乙醇"))
        c.execute(update(RegulatoryReviewItem).where(RegulatoryReviewItem.kind == "eh40_unmatched")
                  .values(status="resolved", resolved_by="ehs-1", resolved_at=datetime(2026, 10, 1)))
        # Delete one row per section (water has no child rows, so no FK issue).
        c.execute(delete(SubstanceRow).where(SubstanceRow.cas_no == "7732-18-5"))
        cls_id = c.execute(select(SubstanceClassificationRow.id).limit(1)).scalar_one()
        c.execute(delete(SubstanceClassificationRow).where(SubstanceClassificationRow.id == cls_id))
        oel_id = c.execute(select(SubstanceOELRow.id).limit(1)).scalar_one()
        c.execute(delete(SubstanceOELRow).where(SubstanceOELRow.id == oel_id))
        tr_id = c.execute(select(SubstanceTransportRow.id).limit(1)).scalar_one()
        c.execute(delete(SubstanceTransportRow).where(SubstanceTransportRow.id == tr_id))
        ge_id = c.execute(select(SubstanceGenericEntryRow.id).limit(1)).scalar_one()
        c.execute(delete(SubstanceGenericEntryRow).where(SubstanceGenericEntryRow.id == ge_id))
        st_id = c.execute(select(GhsStatementRow.code).limit(1)).scalar_one()
        c.execute(delete(GhsStatementRow).where(GhsStatementRow.code == st_id))
        rv = c.execute(select(RegulatoryReviewItem.id).where(
            RegulatoryReviewItem.kind == "oel_unmatched").limit(1)).scalar_one()
        c.execute(delete(RegulatoryReviewItem).where(RegulatoryReviewItem.id == rv))
    assert _counts(engine) == [n - 1 for n in before]
    result = seed_regulatory_db(engine, resources=resources, backfill=True)
    assert result["skipped"] is False and result.get("backfilled") is True
    assert _counts(engine) == before
    with engine.connect() as c:
        assert c.execute(select(SubstanceRow.name_zh).where(
            SubstanceRow.cas_no == "64-17-5")).scalar_one() == "无水乙醇"
        assert c.execute(select(func.count()).select_from(RegulatoryReviewItem).where(
            RegulatoryReviewItem.kind == "eh40_unmatched")).scalar_one() == 1
        assert c.execute(select(RegulatoryReviewItem.status).where(
            RegulatoryReviewItem.kind == "eh40_unmatched")).scalar_one() == "resolved"


def test_backfill_on_complete_db_inserts_nothing(engine, resources):
    seed_regulatory_db(engine, resources=resources)
    before = _counts(engine)
    result = seed_regulatory_db(engine, resources=resources, backfill=True)
    assert result.get("backfilled") is True
    stats = result["stats"]
    assert stats["substances_inserted"] == 0
    assert stats["classifications_inserted"] == 0
    assert stats["generic_entries"] == 0
    assert stats["oel_rows"] == 0
    assert stats["eh40_rows"] == 0
    assert stats["transport_rows"] == 0
    assert stats["statements_inserted"] == 0
    assert stats["review_items"] == {}
    assert _counts(engine) == before


def test_eh40_multivariant_notes_keep_every_limit_on_its_own_line(engine, resources):
    seed_regulatory_db(engine, resources=resources)
    with engine.connect() as c:
        notes = c.execute(select(SubstanceOELRow.notes)
                          .join(SubstanceRow, SubstanceRow.id == SubstanceOELRow.substance_id)
                          .where(SubstanceRow.cas_no == "111-11-5",
                                 SubstanceOELRow.standard == "EH40/2005")).scalar_one()
    assert len(notes) > 200  # the old 200-char cap would have cut this
    assert "\n" in notes
    # Variant 0 is the "first wins" row: its own values ride in the first line
    # (no name); variants 1-5 append one named line each.
    assert "8h TWA 10 ppm" in notes and "15min STEL 30 ppm" in notes
    for i in range(1, 6):
        assert f"Multivariant dust form {i} inhalable particulate" in notes
        assert f"8h TWA {10 + i} ppm" in notes


# ── Review queue API ──────────────────────────────────────────────────────────

@pytest.fixture()
def api_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "true")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    from app.config import get_settings

    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        ehs_id = accounts.create_user(s, username="ehs", password="pw-12345678",
                                      roles=["ehs_regulatory"]).id
        for kind, name in (("eh40_unmatched", "无号物质A"), ("eh40_unmatched", "无号物质B"),
                           ("oel_unmatched", "无号物质C")):
            s.add(RegulatoryReviewItem(id=str(uuid.uuid4()), kind=kind, status="open", name=name,
                                       detail={"raw_cas": ""}, created_at=datetime(2026, 10, 1)))
        s.commit()
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.post("/api/identity/login", json={"username": "ehs", "password": "pw-12345678"})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield {"client": client, "h": headers, "ehs_id": ehs_id}
    get_settings.cache_clear()


def test_review_items_list_filter_and_resolve(api_env):
    c, h = api_env["client"], api_env["h"]
    r = c.get("/api/plm/regulatory/review-items", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["open_by_kind"] == {"eh40_unmatched": 2, "oel_unmatched": 1}
    assert len(body["items"]) == 3
    r = c.get("/api/plm/regulatory/review-items", params={"kind": "eh40_unmatched"}, headers=h)
    assert len(r.json()["items"]) == 2
    item_id = body["items"][0]["id"]
    r = c.post(f"/api/plm/regulatory/review-items/{item_id}/resolve",
               headers=h, json={"note": "源文件无 CAS，接受未匹配"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "resolved"
    r = c.post(f"/api/plm/regulatory/review-items/{item_id}/resolve", headers=h, json={})
    assert r.status_code == 409
    r = c.post("/api/plm/regulatory/review-items/no-such-id/resolve", headers=h, json={})
    assert r.status_code == 404
    r = c.get("/api/plm/regulatory/review-items", params={"status": "open"}, headers=h)
    assert len(r.json()["items"]) == 2
    r = c.get("/api/plm/regulatory/review-items")
    assert r.status_code == 401


def test_review_items_resolve_records_actor_and_note(api_env):
    c, h = api_env["client"], api_env["h"]
    item_id = c.get("/api/plm/regulatory/review-items", headers=h).json()["items"][0]["id"]
    c.post(f"/api/plm/regulatory/review-items/{item_id}/resolve",
           headers=h, json={"note": "接受未匹配"})
    item = c.get("/api/plm/regulatory/review-items", params={"status": "resolved"},
                 headers=h).json()["items"][0]
    assert item["resolved_by"] == api_env["ehs_id"]
    assert item["resolved_at"] is not None
    assert item["detail"]["resolution_note"] == "接受未匹配"

