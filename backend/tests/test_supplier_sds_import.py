"""Supplier SDS import (补齐计划 1-5).

All SDS documents below are synthetic test fixtures written for these
tests — product names / suppliers are invented, never a real supplier's
document. CAS numbers are real, well-known ones (methanol 67-56-1,
toluene 108-88-3) used only as parse targets.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.plm.regulatory.supplier_sds import (
    SdsParseError,
    import_sds,
    parse_sds_text,
)

METHANOL_SDS = """安全数据表

第1部分 化学品及企业标识
产品名称：甲醇
供应商：测试化工有限公司
地址：测试市测试区测试路 1 号
应急电话：+86-21-12345678

第2部分 危险性概述
GHS 分类：易燃液体,类别 2；急性毒性-经口,类别 3；急性毒性-经皮,类别 3；急性毒性-吸入,类别 3；特异性靶器官毒性-一次接触,类别 1
危险说明：H225 H301 H311 H331 H370
防范说明：P210 P233 P260 P280 P301+P310

第3部分 成分/组成信息
甲醇 CAS：67-56-1 含量：99.9%

第14部分 运输信息
UN 号：UN1230
"""

ENGLISH_SDS = """SAFETY DATA SHEET

SECTION 1: Identification
Product name: Toluene
Supplier: Test Chemicals Inc.
Emergency telephone: +1-800-555-0100

SECTION 2: Hazards identification
GHS Classification: Flammable liquids, Category 2; Skin corrosion/irritation, Category 2; Reproductive toxicity, Category 2; Specific target organ toxicity, single exposure, Category 3; Aspiration hazard, Category 1
Hazard statements: H225 H315 H361 H336 H304
Precautionary statements: P210 P261 P280

SECTION 3: Composition / information on ingredients
Toluene CAS: 108-88-3 100%
"""

MIXTURE_SDS = """安全数据表

第1部分 化学品及企业标识
产品名称：测试混合溶剂
供应商：测试化工有限公司

第2部分 危险性概述
GHS 分类：易燃液体,类别 2
危险说明：H225

第3部分 成分/组成信息
甲醇 CAS：67-56-1 含量：50%
甲苯 CAS：108-88-3 含量：50%
"""


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Non-API tests use _session() directly — point them at a throwaway DB
    instead of the developer database."""
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'sds-import-direct.db'}")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'sds-import.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as session:
        sync_builtin_roles(session)
        # rd_engineer carries knowledge.write, which the RBAC rules require
        # for POST /api/plm/substances/* (sds-import, pubchem-enrich).
        accounts.create_user(session, username="tester", password="Test-123456",
                             roles=["rd_engineer"])
        session.commit()
    from app.main import app

    client = TestClient(app)
    r = client.post("/api/identity/login", json={"username": "tester", "password": "Test-123456"})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield client, headers
    get_settings.cache_clear()


def _session():
    from app.db.database import default_session_factory

    return default_session_factory()()


def test_parse_chinese_sds():
    parsed = parse_sds_text(METHANOL_SDS)
    assert parsed.product_name == "甲醇"
    assert parsed.cas_no == "67-56-1"
    assert parsed.supplier_name == "测试化工有限公司"
    assert parsed.emergency_phone == "+86-21-12345678"
    by_class = {c.hazard_class: c for c in parsed.classifications}
    assert by_class["flammable_liquid"].category == "2"
    assert by_class["acute_tox_oral"].category == "3"
    assert by_class["acute_tox_dermal"].category == "3"
    assert by_class["acute_tox_inhal"].category == "3"
    assert by_class["stot_se"].category == "1"
    assert parsed.h_codes == ["H225", "H301", "H311", "H331", "H370"]
    assert parsed.p_codes == ["P210", "P233", "P260", "P280", "P301", "P310"]


def test_parse_english_sds():
    parsed = parse_sds_text(ENGLISH_SDS)
    assert parsed.product_name == "Toluene"
    assert parsed.cas_no == "108-88-3"
    assert parsed.supplier_name == "Test Chemicals Inc."
    by_class = {c.hazard_class: c for c in parsed.classifications}
    assert by_class["flammable_liquid"].category == "2"
    assert by_class["skin_corrosion"].category == "2"
    assert by_class["reproductive"].category == "2"
    assert by_class["stot_se"].category == "3"
    assert by_class["aspiration"].category == "1"
    assert "H225" in parsed.h_codes and "H304" in parsed.h_codes


def test_parse_mixture_rejected():
    with pytest.raises(SdsParseError, match="多个 CAS"):
        parse_sds_text(MIXTURE_SDS)


def test_parse_missing_fields():
    with pytest.raises(SdsParseError, match="CAS"):
        parse_sds_text("第1部分 化学品及企业标识\n产品名称：无 CAS 产品\n第2部分 危险性概述\nGHS 分类：易燃液体,类别 2\n")
    with pytest.raises(SdsParseError, match="GHS 分类"):
        parse_sds_text(
            "第1部分 化学品及企业标识\n产品名称：甲醇\n"
            "第2部分 危险性概述\n无分类信息\n"
            "第3部分 成分/组成信息\n甲醇 CAS：67-56-1\n"
        )
    with pytest.raises(SdsParseError, match="内容为空"):
        parse_sds_text("   \n")


def test_parse_not_classified():
    parsed = parse_sds_text(
        "第1部分 化学品及企业标识\n产品名称：去离子水\n"
        "第2部分 危险性概述\n本物质未分类，不属于危险品。\n"
        "第3部分 成分/组成信息\n水 CAS：7732-18-5\n"
    )
    assert parsed.not_classified is True
    assert parsed.classifications == []


def test_import_creates_substance_and_replaces():
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow

    with _session() as session:
        result = import_sds(session, parse_sds_text(METHANOL_SDS),
                            filename="methanol-sds.txt", digest="abc")
        session.commit()
        assert result["cas_no"] == "67-56-1"
        assert result["classifications_written"] == 5
        assert result["classifications_replaced"] == 0
        sub = session.query(SubstanceRow).filter(SubstanceRow.cas_no == "67-56-1").one()
        assert sub.name_zh == "甲醇"
        rows = session.query(SubstanceClassificationRow).filter(
            SubstanceClassificationRow.substance_id == sub.id).all()
        assert len(rows) == 5
        assert all(r.source == "supplier_sds" and r.confidence == 0.95 for r in rows)
        assert all(r.source_ref == "sds-import:methanol-sds.txt" for r in rows)

        # Re-import replaces instead of duplicating.
        result2 = import_sds(session, parse_sds_text(METHANOL_SDS),
                             filename="methanol-sds-v2.txt", digest="def")
        session.commit()
        assert result2["classifications_replaced"] == 5
        rows2 = session.query(SubstanceClassificationRow).filter(
            SubstanceClassificationRow.substance_id == sub.id).all()
        assert len(rows2) == 5
        assert all(r.source_ref == "sds-import:methanol-sds-v2.txt" for r in rows2)


def test_import_keeps_locked_and_other_sources():
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow

    with _session() as session:
        sub = SubstanceRow(id=str(uuid.uuid4()), cas_no="67-56-1", name_zh="甲醇", aliases=[])
        session.add(sub)
        session.flush()
        locked = SubstanceClassificationRow(
            id=str(uuid.uuid4()), substance_id=sub.id, hazard_class="carcinogenicity",
            category="1B", h_code="H350", source="supplier_sds",
            locked_by="ehs-1", locked_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )
        catalog = SubstanceClassificationRow(
            id=str(uuid.uuid4()), substance_id=sub.id, hazard_class="flammable_liquid",
            category="2", h_code="H225", source="cn_catalog",
        )
        session.add_all([locked, catalog])
        session.commit()
        import_sds(session, parse_sds_text(METHANOL_SDS), filename="x.txt", digest="d")
        session.commit()
        rows = session.query(SubstanceClassificationRow).filter(
            SubstanceClassificationRow.substance_id == sub.id).all()
        sources = {(r.hazard_class, r.source, bool(r.locked_at)) for r in rows}
        assert ("carcinogenicity", "supplier_sds", True) in sources  # locked kept
        assert ("flammable_liquid", "cn_catalog", False) in sources  # other source kept
        # New supplier_sds rows added; the locked one was NOT replaced.
        assert sum(1 for r in rows if r.source == "supplier_sds") == 6


def test_api_upload_txt(api):
    client, headers = api
    r = client.post(
        "/api/plm/substances/sds-import",
        files={"file": ("methanol-sds.txt", METHANOL_SDS.encode(), "text/plain")},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["cas_no"] == "67-56-1"
    assert body["classifications_written"] == 5
    assert body["product_name"] == "甲醇"


def test_api_mixture_and_unsupported(api):
    client, headers = api
    r = client.post(
        "/api/plm/substances/sds-import",
        files={"file": ("mix.txt", MIXTURE_SDS.encode(), "text/plain")},
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "多个 CAS" in r.json()["detail"]
    r = client.post(
        "/api/plm/substances/sds-import",
        files={"file": ("x.xlsx", b"PK\x03\x04", "application/octet-stream")},
        headers=headers,
    )
    assert r.status_code == 422, r.text
    assert "不支持的文件类型" in r.json()["detail"]


def test_api_pdf_extraction(api):
    client, headers = api
    import fitz

    # fitz's built-in fonts cannot encode CJK, so the PDF fixture uses the
    # English SDS; the PDF path (extraction -> parse) is what is exercised.
    doc = fitz.open()
    page = doc.new_page(width=1200, height=1200)
    page.insert_textbox(fitz.Rect(40, 40, 1160, 1160), ENGLISH_SDS, fontsize=11)
    pdf_bytes = doc.tobytes()
    doc.close()
    r = client.post(
        "/api/plm/substances/sds-import",
        files={"file": ("toluene.pdf", pdf_bytes, "application/pdf")},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["cas_no"] == "108-88-3"
