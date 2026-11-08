"""Batch 3 (gate): regressions for P2-C1 (EU source order), P2-C4 (units),
P2-C11 (COA issuable in batch export)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.services.plm.release.gate import Check, _check_objectives, _check_substances
from app.services.plm.release.gate import MeasuredValue


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'gate3.db'}")
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    from app.config import get_settings

    get_settings.cache_clear()
    from app.db.database import default_session_factory

    yield default_session_factory
    get_settings.cache_clear()


def _substance_with_clp_row(session):
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow

    sub = SubstanceRow(id=str(uuid.uuid4()), cas_no="1330-20-7", name_zh="二甲苯",
                       name_en="xylene", aliases=[])
    session.add(sub)
    session.flush()
    session.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=sub.id,
                                           hazard_class="skin_corrosion", category="2", h_code="H315",
                                           source="clp_annex_vi"))
    session.commit()
    return sub


def test_weak_source_check_uses_regulation_source_order(db):
    session = db()()
    _substance_with_clp_row(session)
    items = [{"name": "二甲苯", "cas": "1330-20-7", "pct": 0.5}]
    eu = Check("substance_data", "G2", "物质分类来源可信且关键组分已锁定")
    _check_substances(session, eu, items, ["EU"])
    assert not [i for i in eu.items if "置信度不足" in i["message"]]
    cn = Check("substance_data", "G2", "物质分类来源可信且关键组分已锁定")
    _check_substances(session, cn, items, ["CN"])
    assert [i for i in cn.items if "置信度不足" in i["message"]]


def test_objectives_convert_units_and_fail_closed_on_mismatch():
    c = Check("objectives", "G3", "目标契约关键指标达标")
    _check_objectives(c, [{"metric": "solids", "label": "固含量", "unit": "g/L", "min": 0.9, "max": 1.1}],
                      {"solids": MeasuredValue(id="m1", value=1000.0, unit="mg/L")},
                      {}, None)
    assert c.items == []
    assert c.evidence["solids"]["value"] == 1.0 and c.evidence["solids"]["unit"] == "g/L"

    c2 = Check("objectives", "G3", "目标契约关键指标达标")
    _check_objectives(c2, [{"metric": "solids", "label": "固含量", "unit": "mg/L", "min": 40, "max": 60}],
                      {"solids": MeasuredValue(id="m1", value=50.0, unit="%")},
                      {}, None)
    assert len(c2.items) == 1 and "无法换算" in c2.items[0]["message"]

    c3 = Check("objectives", "G3", "目标契约关键指标达标")
    _check_objectives(c3, [{"metric": "solids", "label": "固含量", "unit": "%", "min": 50, "max": 60}],
                      {"solids": MeasuredValue(id="m1", value=55.0, unit="%")},
                      {}, None)
    assert c3.items == []


def _seed_coa(session, issuable: bool, product_code: str):
    from app.db.models_plm import ProductRevisionRow, ProductRow, RegulatoryDocumentRow
    from app.services.plm import storage

    product = ProductRow(id=str(uuid.uuid4()), code=product_code, name_zh=product_code,
                         lifecycle_state="rd")
    session.add(product)
    session.flush()
    rev = ProductRevisionRow(id=str(uuid.uuid4()), product_id=product.id, rev="A", state="released")
    session.add(rev)
    session.flush()
    pdf_key = storage.put(f"documents/{uuid.uuid4()}/coa.pdf", b"%PDF-FAKE-coa")
    doc = RegulatoryDocumentRow(id=str(uuid.uuid4()), revision_id=rev.id, doc_type="coa", lang="zh-CN",
                                version_no=1, status="approved",
                                content={"meta": {"overall": "PASS" if issuable else "FAIL",
                                                  "issuable": issuable}},
                                field_sources={}, rendered_pdf_key=pdf_key, content_hash="x")
    session.add(doc)
    session.commit()
    return product


def test_batch_export_excludes_unissuable_coa_and_reports_it(db):
    from app.services.plm.documents.batch_export import build_batch_zip

    session = db()()
    bad = _seed_coa(session, False, "FX-BAD")
    good = _seed_coa(session, True, "FX-GOOD")
    _zip, manifest, missing = build_batch_zip(session, product_ids=[bad.id, good.id], doc_types=["coa"])
    files = manifest["files"]
    assert [f for f in files if f["product_code"] == "FX-GOOD"]
    assert not [f for f in files if f["product_code"] == "FX-BAD"]
    assert [m for m in missing if m["product_code"] == "FX-BAD"
            and "不可签发" in m["reason"]]
