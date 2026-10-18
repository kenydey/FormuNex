"""整合方案 §4: 批量文档导出 — 文件名、ZIP、缺失报告、API。"""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("docx")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.plm.documents.batch_export import BatchExportError, render_filename, sanitize_component  # noqa: E402

PASSWORD = "Test-Pass-1!"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'batch.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.commit()
    from app.main import app

    client = TestClient(app)
    yield {"client": client, "tmp": tmp_path}
    get_settings.cache_clear()


def _seed_doc(session, product_code: str, doc_type: str, version_no: int = 1):
    """A product + revision + one regulatory document row with a stored fake PDF (no classification chain)."""
    from app.db.models_plm import ProductRevisionRow, ProductRow, RegulatoryDocumentRow
    from app.services.plm import storage

    product = ProductRow(id=str(uuid.uuid4()), code=product_code, name_zh=product_code, lifecycle_state="rd")
    session.add(product)
    session.flush()
    rev = ProductRevisionRow(id=str(uuid.uuid4()), product_id=product.id, rev="A", state="draft")
    session.add(rev)
    session.flush()
    pdf_key = storage.put(f"documents/{uuid.uuid4()}/{doc_type}.pdf", b"%PDF-FAKE-" + doc_type.encode())
    doc = RegulatoryDocumentRow(id=str(uuid.uuid4()), revision_id=rev.id, doc_type=doc_type, lang="zh-CN",
                                version_no=version_no, status="draft", content={}, field_sources={},
                                rendered_pdf_key=pdf_key, content_hash="x")
    session.add(doc)
    session.commit()
    return product, doc


def test_sanitize_component():
    assert sanitize_component("A/B:C*D") == "A_B_C_D"
    assert sanitize_component("  ") == "_"
    assert sanitize_component("正常产品") == "正常产品"


def test_render_filename_template():
    out = render_filename("{company}_{product}_{doc_type}_{lang}_{version}.{ext}",
                          company="Chemetall", product="FX-100", doc_type="sds", lang="zh-CN",
                          version="v2", ext="pdf")
    assert out == "Chemetall_FX-100_sds_zh-CN_v2.pdf"
    with pytest.raises(BatchExportError, match="未知占位符"):
        render_filename("{bogus}.pdf", company="", product="", doc_type="", lang="", version="", ext="pdf")


def test_batch_export_zip_groups_per_product_and_reports_missing(env):
    from app.db.database import default_session_factory
    from app.services.plm.documents import batch_export as batch

    with default_session_factory()() as s:
        p1, _ = _seed_doc(s, "P-1", "sds")
        p2, _ = _seed_doc(s, "P-2", "tds")
        row = batch.run_batch_export(s, product_ids=[p1.id, p2.id], doc_types=["sds", "tds"], lang="zh-CN",
                                     company_name="方枢")
        s.commit()
        assert row.status == "done" and row.file_count == 2
        assert {m["product_code"] for m in row.missing} == {"P-1", "P-2"}  # 各缺一种文档，如实报告
    from app.services.plm import storage

    data = storage.get(row.result_key)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "P-1/方枢_P-1_sds_zh-CN_v1.pdf" in names
    assert "P-2/方枢_P-2_tds_zh-CN_v1.pdf" in names
    assert "manifest.json" in names
    assert row.missing[0]["reason"]


def test_batch_export_rejects_unknown_type_and_overflow(env):
    from app.db.database import default_session_factory
    from app.services.plm.documents import batch_export as batch

    with default_session_factory()() as s:
        with pytest.raises(BatchExportError, match="未知文档类型"):
            batch.run_batch_export(s, product_ids=["x"], doc_types=["bogus"])
        with pytest.raises(BatchExportError, match="最多 20"):
            batch.run_batch_export(s, product_ids=[f"p{i}" for i in range(21)], doc_types=["sds"])


def test_batch_export_api_end_to_end(env):
    from app.db.database import default_session_factory

    with default_session_factory()() as s:
        p1, _ = _seed_doc(s, "API-1", "sds")
    c = env["client"]
    r = c.post("/api/plm/documents/batch-export", json={"product_ids": [p1.id], "doc_types": ["sds", "ts"]})
    assert r.status_code == 201, r.text
    export = r.json()
    assert export["status"] == "done" and export["file_count"] == 1
    assert export["missing"][0]["doc_type"] == "ts"  # 无 TS 文档，如实报告而非静默
    got = c.get(f"/api/plm/documents/batch-export/{export['id']}")
    assert got.json()["has_file"] is True
    dl = c.get(f"/api/plm/documents/batch-export/{export['id']}/download")
    assert dl.status_code == 200 and dl.headers["content-type"] == "application/zip"
    assert "API-1/" in zipfile.ZipFile(io.BytesIO(dl.content)).namelist()[0]
    assert c.get("/api/plm/documents/batch-export/nope/download").status_code == 404
