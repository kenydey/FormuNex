"""补齐计划 Phase 3B（3-13 / 3-14 / 3-15）：PDF/A-2b、标签批量+CMYK、模板预览、COA 编号。"""
from __future__ import annotations

import io
import re
import uuid
import zipfile
from datetime import datetime

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("docx")
pytest.importorskip("svglib")

from fastapi.testclient import TestClient  # noqa: E402

from pypdf import PdfReader  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.plm.documents.service import (  # noqa: E402
    DocumentError,
    generate_coa_number,
)
from app.services.plm.render.pdfa import PdfaError, apply_pdfa_2b, srgb_icc_bytes  # noqa: E402


# --------------------------------------------------------------------------- PDF/A-2b


def _embedded_font_pdf() -> bytes:
    """A one-page PDF whose font is really embedded.

    Portable: searches plain TTF files across Linux / Windows / macOS.
    Skips honestly when the platform has no embeddable TTF (no invented
    font, no binary blobs in the repo).
    """
    import io as _io
    from pathlib import Path

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "C:/Windows/Fonts/verdana.ttf",
        "/Library/Fonts/Arial.ttf",
        "/Library/Fonts/Verdana.ttf",
    ]
    font_path = next((p for p in candidates if Path(p).is_file()), None)
    if font_path is None:
        pytest.skip("no embeddable TTF font found on this platform")
    pdfmetrics.registerFont(TTFont("DJV", font_path))
    buf = _io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("DJV", 12)
    c.drawString(72, 720, "PDF/A-2b test")
    c.showPage()
    c.save()
    return buf.getvalue()


def test_pdfa_xmp_and_output_intent():
    pdf, info = apply_pdfa_2b(_embedded_font_pdf(), title="Test", author="FormuNex")
    assert pdf.startswith(b"%PDF")
    assert info == {"pdfa": True, "pdfa_part": 2, "pdfa_conformance": "B",
                    "pdfa_output_intent": True, "pdfa_fonts_embedded": True}
    assert b"<pdfaid:part>2</pdfaid:part>" in pdf
    assert b"<pdfaid:conformance>B</pdfaid:conformance>" in pdf
    # 解析回读（pypdf 会把 -/. 写成八进制转义，直接搜原文不可靠）。
    reader = PdfReader(io.BytesIO(pdf))
    intents = reader.trailer["/Root"].get_object()["/OutputIntents"]
    assert len(intents) == 1
    intent = intents[0].get_object()
    assert intent["/S"] == "/GTS_PDFA1"
    assert intent["/OutputConditionIdentifier"] == "sRGB IEC61966-2.1"
    assert intent["/RegistryName"] == "http://www.color.org"
    icc = intent["/DestOutputProfile"].get_object().get_data()
    assert len(icc) == int.from_bytes(icc[:4], "big")


def test_pdfa_rejects_unembedded_fonts():
    # 标准 14 字体（Helvetica）依法无需嵌入；真正的未嵌入是：描述符里没有
    # FontFile*。剥掉 DejaVu 的 FontFile2 后，检查必须 fail-closed。
    from pypdf import PdfWriter
    from pypdf.generic import NameObject

    reader = PdfReader(io.BytesIO(_embedded_font_pdf()))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    for page in writer.pages:
        for font in page["/Resources"]["/Font"].values():
            obj = font.get_object()
            if obj.get("/Subtype") == "/TrueType":
                desc = obj["/FontDescriptor"].get_object()
                del desc[NameObject("/FontFile2")]
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(PdfaError, match="字体嵌入"):
        apply_pdfa_2b(buf.getvalue())


def test_srgb_icc_is_a_real_profile():
    icc = srgb_icc_bytes()
    if icc is None:
        pytest.skip("no sRGB ICC profile obtainable here (LittleCMS unavailable and no FORMUNEX_PDFA_ICC_PATH)")
    # ICC header bytes 0-3 are the profile size itself.
    assert len(icc) == int.from_bytes(icc[:4], "big") and len(icc) > 100


def test_render_pdf_pdfa_respects_font_embedding():
    from app.services.plm.render.fonts import resolve_font
    from app.services.plm.render.pdf import render_pdf

    content = {"title": "T", "lang": "zh-CN", "header": {}, "sections": []}
    if not resolve_font().embedded:
        with pytest.raises(PdfaError, match="字体嵌入"):
            render_pdf(content, pdfa=True)
    else:  # a machine with an embeddable CJK font gets the real conversion
        _pdf, info = render_pdf(content, pdfa=True)
        assert info["pdfa"] is True


# --------------------------------------------------------------------------- API fixture (mirrors test_plm_documents.py)


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'docs3b.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    monkeypatch.setenv("FORMUNEX_PLM_COMPANY_NAME", "方枢示例化工有限公司")
    monkeypatch.setenv("FORMUNEX_PLM_COMPANY_ADDRESS", "上海市示例路 1 号")
    monkeypatch.setenv("FORMUNEX_PLM_EMERGENCY_PHONE", "021-0001")
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceOELRow, SubstanceRow

    with default_session_factory()() as s:
        xyl = SubstanceRow(id=str(uuid.uuid4()), cas_no="1330-20-7", name_zh="二甲苯", name_en="xylene", aliases=[],
                           hazardous_catalog_no=2289)
        s.add(xyl)
        s.add(SubstanceRow(id=str(uuid.uuid4()), cas_no="7732-18-5", name_zh="水", name_en="water", aliases=[]))
        s.flush()
        for hc, cat, h in [("flammable_liquid", "3", "H226"), ("skin_corrosion", "2", "H315")]:
            s.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=xyl.id, hazard_class=hc, category=cat,
                                             h_code=h, source="cn_catalog"))
        s.add(SubstanceOELRow(id=str(uuid.uuid4()), substance_id=xyl.id, pc_twa=50, pc_stel=100))
        s.add(FormulationVersion(id="fv-1", lineage_id="l", version=1, domain="coating", name="v1", created_at=datetime(2026, 10, 1),
                                 snapshot={"ingredients": [{"name": "二甲苯", "cas_no": "1330-20-7", "weight_pct": 30},
                                                           {"name": "水", "cas_no": "7732-18-5", "weight_pct": 70}]}))
        s.commit()
    from app.main import app

    client = TestClient(app)
    pid = client.post("/api/plm/products", json={"code": "FX-100", "name_zh": "FX-100 环氧底漆"}).json()["id"]
    rid = client.post(f"/api/plm/products/{pid}/revisions", json={
        "formulation_version_id": "fv-1",
        "spec_snapshot": {"release_specs": [{"metric": "solids", "label": "固含量", "unit": "%", "min": 55, "max": 60},
                                            {"metric": "visc", "label": "黏度", "unit": "s", "max": 90}]}}).json()["id"]
    yield client, pid, rid
    get_settings.cache_clear()


def _label_content():
    from app.services.plm.documents.label_builder import LabelContext, build_label
    from app.services.plm.ghs.engine import classify
    from app.services.plm.ghs.model import Component, ComponentHazard, MixtureProperties

    comps = [Component("二甲苯", 25, "1330-20-7", hazards=[ComponentHazard("flammable_liquid", "3", "H226")])]
    cls = classify(comps, MixtureProperties(flash_point_c=27)).to_dict()
    cls["statements"] = {c: {"zh": f"{c}中文", "en": ""} for c in cls["h_codes"] + cls["p_codes"]}
    return build_label(LabelContext({"name_zh": "FX-100", "code": "FX-100"},
                                    {"name": "方枢示例化工有限公司", "address": "上海市示例路 1 号",
                                     "phone": "021-0000", "emergency_phone": "021-0001"},
                                    cls, cls["statements"], ["二甲苯"], container_volume_l=18, size_mm=(100, 150)))


# --------------------------------------------------------------------------- 3-14 标签批量 + CMYK


def test_label_cmyk_emits_device_cmyk():
    import re

    from pypdf import PdfReader

    from app.services.plm.render.label_pdf import render_label_pdf

    def _has_cmyk(pdf: bytes) -> bool:
        # ReportLab emits CMYK as the 4-operand `k`/`K` operator, never the
        # literal string "DeviceCMYK" — search the decompressed content stream.
        reader = PdfReader(io.BytesIO(pdf))
        stream = b"".join(p.get_contents().get_data() for p in reader.pages)
        return re.search(rb"[\d.]+ [\d.]+ [\d.]+ [\d.]+ [kK]\b", stream) is not None

    cmyk, info = render_label_pdf(_label_content(), color_space="cmyk")
    assert cmyk.startswith(b"%PDF") and info["color_space"] == "cmyk"
    assert _has_cmyk(cmyk)  # our text / border / QR are CMYK inks
    rgb, _ = render_label_pdf(_label_content(), color_space="rgb")
    assert not _has_cmyk(rgb)
    with pytest.raises(ValueError):
        render_label_pdf(_label_content(), color_space="spot")


def test_label_batch_zip_and_cmyk(api):
    client, _pid, _rid = api
    payload = {"labels": [{"content": _label_content()}, {"content": _label_content(), "filename": "b.pdf"},
                              {"content": _label_content()}]}
    r = client.post("/api/plm/labels/batch", json=payload)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(zf.namelist()) == ["b.pdf", "label-1.pdf", "label-3.pdf"]
    for name in zf.namelist():
        assert zf.read(name).startswith(b"%PDF")
    r = client.post("/api/plm/labels/batch", json={**payload, "color_space": "cmyk"})
    assert r.status_code == 200, r.text
    first_pdf = zipfile.ZipFile(io.BytesIO(r.content)).read("label-1.pdf")
    reader = PdfReader(io.BytesIO(first_pdf))
    stream = b"".join(p.get_contents().get_data() for p in reader.pages)
    assert re.search(rb"[\d.]+ [\d.]+ [\d.]+ [\d.]+ [kK]\b", stream) is not None
    assert client.post("/api/plm/labels/batch", json={"labels": []}).status_code == 422


# --------------------------------------------------------------------------- 3-13 模板预览 + 列表


def test_template_preview_and_list(api):
    from docx import Document

    client, _pid, _rid = api
    tpl = Document()
    tpl.add_paragraph("企业模板：{{ header.product_name }}")
    buf = io.BytesIO()
    tpl.save(buf)
    r = client.post("/api/plm/templates", data={"doc_type": "sds", "lang": "zh-CN", "name": "公司版式"},
                    files={"file": ("sds.docx", buf.getvalue())})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]

    listing = client.get("/api/plm/templates", params={"doc_type": "sds"}).json()
    assert [t["id"] for t in listing] == [tid] and listing[0]["name"] == "公司版式"

    # Preview with the uploaded file (nothing saved by preview).
    r = client.post("/api/plm/templates/preview", data={"doc_type": "sds", "lang": "zh-CN"},
                    files={"file": ("sds.docx", buf.getvalue())})
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"PK")
    assert client.get("/api/plm/templates").json()[0]["version"] == 1  # preview did not create a version

    # Preview with the stored template + a real document's content.
    doc = client.post(f"/api/plm/revisions/{_rid}/documents", json={
        "doc_type": "sds", "properties": {"flash_point_c": 30, "initial_boiling_point_c": 138}}).json()
    r = client.post("/api/plm/templates/preview",
                    data={"doc_type": "sds", "lang": "zh-CN", "template_id": tid, "document_id": doc["id"]})
    assert r.status_code == 200, r.text
    word = Document(io.BytesIO(r.content))
    assert word.paragraphs[0].text == "企业模板：FX-100 环氧底漆"

    assert client.post("/api/plm/templates/preview", data={"doc_type": "sds"}).status_code == 422
    assert client.post("/api/plm/templates/preview", data={"doc_type": "sds", "template_id": "nope"}).status_code == 404


# --------------------------------------------------------------------------- 3-15 COA 编号


def test_coa_number_rule():
    assert generate_coa_number("COA-{product_code}-{batch_no}-{n}", product_code="FX", batch_no="B1", n=1,
                               date="2026-10-18") == "COA-FX-B1-1"
    assert generate_coa_number("COA/{date}/{n:03d}", product_code="FX", batch_no="B1", n=7,
                               date="2026-10-18") == "COA/2026-10-18/007"
    with pytest.raises(DocumentError, match="未知占位符"):
        generate_coa_number("COA-{serial}", product_code="FX", batch_no="B1", n=1, date="2026-10-18")


def _coa_no_of(doc: dict) -> str | None:
    for s in doc["content"]["sections"]:
        for f in s.get("fields", []):
            if f["key"] == "coa_no":
                return f["value"]
    return None


def test_coa_number_auto_and_caller_provided(api, monkeypatch):
    client, pid, rid = api
    bid = client.post(f"/api/plm/products/{pid}/batches", json={"batch_no": "B2610-01", "produced_at": "2026-10-15"}).json()["id"]
    client.post(f"/api/plm/batches/{bid}/qc-results",
                json={"results": [{"metric": "solids", "value": 57.1}, {"metric": "visc", "value": 80}]})
    # Default rule: auto-generated.
    first = client.post(f"/api/plm/revisions/{rid}/documents",
                        json={"doc_type": "coa", "coa": {"batch_id": bid}}).json()
    assert _coa_no_of(first) == "COA-FX-100-B2610-01-1"
    # Caller-provided number is respected, never overwritten.
    second = client.post(f"/api/plm/revisions/{rid}/documents",
                         json={"doc_type": "coa", "coa": {"batch_id": bid, "coa_no": "CUSTOM-2026-001"}}).json()
    assert _coa_no_of(second) == "CUSTOM-2026-001"
    # Configurable rule via env.
    monkeypatch.setenv("FORMUNEX_COA_NUMBER_RULE", "COA/{date}/{product_code}-{batch_no}-{n}")
    third = client.post(f"/api/plm/revisions/{rid}/documents",
                        json={"doc_type": "coa", "coa": {"batch_id": bid}}).json()
    assert _coa_no_of(third) == "COA/2026-10-15/FX-100-B2610-01-3"
