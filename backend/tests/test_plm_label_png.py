"""Label PNG output: PyMuPDF rasterisation of the stored label PDF + the ``format=png`` API."""
from __future__ import annotations

import struct
import uuid
from datetime import datetime

import pytest

pytest.importorskip("fitz")
pytest.importorskip("reportlab")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.plm.render.label_pdf import render_label_pdf  # noqa: E402
from app.services.plm.render.label_png import LabelPngError, render_label_png  # noqa: E402

SAMPLE = {
    "doc_type": "label", "lang": "zh-CN", "title": "化学品安全标签",
    "meta": {"size": {"width_mm": 100, "height_mm": 150}},
    "sections": [{"key": "label", "fields": [
        {"key": "product_name", "value": "示例产品 FX-100"},
        {"key": "pictograms", "value": ["GHS02"]},
        {"key": "signal_word", "value": "警告"},
        {"key": "hazard_statements", "value": [{"code": "H226", "text": "易燃液体和蒸气"}]},
        {"key": "precautionary_statements", "value": [{"code": "P210", "text": "远离热源。"}]},
        {"key": "supplier", "value": "方枢示例化工有限公司"},
        {"key": "emergency_phone", "value": "021-0001"},
    ]}],
}


def _png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def test_render_label_png_dimensions_match_physical_size_at_300dpi():
    pdf, _ = render_label_pdf(SAMPLE)
    png = render_label_png(pdf, dpi=300)
    # 100 mm x 150 mm at 300 dpi → 1181 x 1772 px (rounding ±2).
    w, h = _png_size(png)
    assert abs(w - 1181) <= 2 and abs(h - 1772) <= 2


def test_render_label_png_dpi_scales():
    pdf, _ = render_label_pdf(SAMPLE)
    w150, _ = _png_size(render_label_png(pdf, dpi=150))
    w300, _ = _png_size(render_label_png(pdf, dpi=300))
    assert abs(w300 - 2 * w150) <= 2


def test_render_label_png_rejects_bad_input():
    with pytest.raises(LabelPngError):
        render_label_png(b"", dpi=300)
    with pytest.raises(LabelPngError):
        render_label_png(b"not a pdf", dpi=300)
    with pytest.raises(LabelPngError):
        render_label_png(b"%PDF-1.4", dpi=50)


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'plm.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion

    with default_session_factory()() as session:
        session.add(FormulationVersion(
            id="fv-1", lineage_id="lin-1", version=1, domain="coating", name="底漆 v1", snapshot={
                "ingredients": [{"name": "xylene", "zh_name": "二甲苯", "cas_no": "1330-20-7", "weight_pct": 100}]},
            created_at=datetime(2026, 10, 1)))
        session.commit()
    from app.main import app

    yield TestClient(app)
    get_settings.cache_clear()


def _label_doc_id(api):
    product = api.post("/api/plm/products", json={"code": "FX-100", "name_zh": "FX-100 底漆"}).json()
    rev = api.post(f"/api/plm/products/{product['id']}/revisions",
                   json={"formulation_version_id": "fv-1"}).json()
    doc = api.post(f"/api/plm/revisions/{rev['id']}/documents",
                   json={"doc_type": "label", "properties": {"phys_state": "liquid", "flash_point_c": 27}}).json()
    return doc["id"]


def test_label_png_download(api):
    doc_id = _label_doc_id(api)
    resp = api.get(f"/api/plm/documents/{doc_id}/file/png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert _png_size(resp.content)[0] > 0


def test_label_png_download_with_dpi(api):
    doc_id = _label_doc_id(api)
    small = api.get(f"/api/plm/documents/{doc_id}/file/png", params={"dpi": 72})
    big = api.get(f"/api/plm/documents/{doc_id}/file/png", params={"dpi": 300})
    assert small.status_code == 200 and big.status_code == 200
    assert _png_size(small.content)[0] < _png_size(big.content)[0]


def test_png_rejected_for_non_label_documents(api):
    product = api.post("/api/plm/products", json={"code": "FX-200", "name_zh": "FX-200"}).json()
    rev = api.post(f"/api/plm/products/{product['id']}/revisions",
                   json={"formulation_version_id": "fv-1"}).json()
    doc = api.post(f"/api/plm/revisions/{rev['id']}/documents", json={"doc_type": "tds"}).json()
    resp = api.get(f"/api/plm/documents/{doc['id']}/file/png")
    assert resp.status_code == 422
    assert "仅适用于标签" in resp.json()["detail"]
