"""Batch document export (整合方案 §4): multi-product × multi-doc-type → one ZIP.

Semantics: for each product, the *latest* revision; for each requested
doc_type, the latest version of that (doc_type, lang) on that revision.
A product / doc_type with no document is NOT silently skipped — it lands in
``missing`` (on the row and in the ZIP manifest) so the exporter sees
exactly what did not ship.

Filenames follow the MSDS-Generator convention
``{company}_{product}_{doc_type}_{lang}_{version}.{ext}`` with illegal
characters (``<>:"/\\|?*``) replaced by ``_``. The ZIP layout groups files
per product (``{product_code}/...``).

Bounded synchronous execution: at most 20 products × 5 doc types per export
(a release-time batch, not an unbounded dump). The export row + GET
endpoint give the same create → poll → download shape as an async job
without new worker wiring.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
import zipfile
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ....clock import utcnow
from ....db.models_plm import BatchExportRow, ProductRevisionRow, ProductRow, RegulatoryDocumentRow
from .. import storage
from .service import DOC_TYPES

MAX_PRODUCTS = 20
MAX_DOC_TYPES = 5
DEFAULT_FILENAME_TEMPLATE = "{company}_{product}_{doc_type}_{lang}_{version}.{ext}"
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TEMPLATE_KEYS = ("company", "product", "doc_type", "lang", "version", "ext")


class BatchExportError(ValueError):
    pass


def sanitize_component(value: str) -> str:
    """One filename component: illegal chars → ``_``, no trailing dots / spaces."""
    out = _ILLEGAL.sub("_", str(value or "")).strip(" .")
    return out or "_"


def render_filename(template: str, *, company: str, product: str, doc_type: str, lang: str,
                    version: str, ext: str) -> str:
    unknown = [m for m in re.findall(r"\{(\w+)\}", template) if m not in _TEMPLATE_KEYS]
    if unknown:
        raise BatchExportError(f"文件名模板含未知占位符：{', '.join(unknown)}（可用：{', '.join(_TEMPLATE_KEYS)}）")
    try:
        name = template.format(company=sanitize_component(company), product=sanitize_component(product),
                               doc_type=sanitize_component(doc_type), lang=sanitize_component(lang),
                               version=sanitize_component(version), ext=ext)
    except (KeyError, IndexError, ValueError) as exc:
        raise BatchExportError(f"文件名模板无法应用：{exc}") from None
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise BatchExportError(f"文件名模板产出非法文件名：{name!r}")
    return name


def _latest_revision(session: Session, product_id: str) -> ProductRevisionRow | None:
    return session.execute(select(ProductRevisionRow).where(ProductRevisionRow.product_id == product_id)
                           .order_by(ProductRevisionRow.created_at.desc()).limit(1)).scalar_one_or_none()


def _latest_document(session: Session, revision_id: str, doc_type: str, lang: str) -> RegulatoryDocumentRow | None:
    return session.execute(
        select(RegulatoryDocumentRow).where(RegulatoryDocumentRow.revision_id == revision_id,
                                            RegulatoryDocumentRow.doc_type == doc_type,
                                            RegulatoryDocumentRow.lang == lang)
        .order_by(RegulatoryDocumentRow.version_no.desc()).limit(1)).scalar_one_or_none()


def build_batch_zip(session: Session, *, product_ids: list[str], doc_types: list[str], lang: str = "zh-CN",
                    filename_template: str = DEFAULT_FILENAME_TEMPLATE, include_docx: bool = False,
                    company_name: str = "") -> tuple[bytes, dict[str, Any], list[dict[str, Any]]]:
    """Build the batch ZIP. Returns (zip_bytes, manifest, missing)."""
    if not product_ids:
        raise BatchExportError("批量导出至少需要一个产品")
    if len(product_ids) > MAX_PRODUCTS:
        raise BatchExportError(f"批量导出一次最多 {MAX_PRODUCTS} 个产品")
    if not doc_types:
        raise BatchExportError("批量导出至少需要一种文档类型")
    if len(doc_types) > MAX_DOC_TYPES:
        raise BatchExportError(f"批量导出一次最多 {MAX_DOC_TYPES} 种文档类型")
    bad = [d for d in doc_types if d not in DOC_TYPES]
    if bad:
        raise BatchExportError(f"未知文档类型：{', '.join(bad)}")
    if lang not in ("zh-CN", "en-US"):
        raise BatchExportError("lang 仅支持 zh-CN / en-US")

    buf = io.BytesIO()
    files: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for product_id in dict.fromkeys(product_ids):
            product = session.get(ProductRow, product_id)
            if product is None:
                missing.append({"product_id": product_id, "product_code": None, "doc_type": None,
                                "reason": "产品不存在"})
                continue
            rev = _latest_revision(session, product.id)
            if rev is None:
                for dt in doc_types:
                    missing.append({"product_id": product.id, "product_code": product.code, "doc_type": dt,
                                    "reason": "产品无修订版"})
                continue
            for dt in doc_types:
                doc = _latest_document(session, rev.id, dt, lang)
                if doc is None:
                    missing.append({"product_id": product.id, "product_code": product.code, "doc_type": dt,
                                    "reason": f"修订版 {rev.rev} 无 {dt}（{lang}）文档"})
                    continue
                version = f"v{doc.version_no}"
                for key, ext in ((doc.rendered_pdf_key, "pdf"), (doc.rendered_docx_key, "docx") if include_docx else (None, None)):
                    if not key:
                        continue
                    if not storage.exists(key):
                        missing.append({"product_id": product.id, "product_code": product.code, "doc_type": dt,
                                        "reason": f"{dt} v{doc.version_no} 缺少 {ext} 渲染文件"})
                        continue
                    data = storage.get(key)
                    name = render_filename(filename_template, company=company_name, product=product.code,
                                           doc_type=dt, lang=lang, version=version, ext=ext)
                    path = f"{sanitize_component(product.code)}/{name}"
                    zf.writestr(path, data)
                    files.append({"path": path, "product_code": product.code, "doc_type": dt, "lang": lang,
                                  "version_no": doc.version_no, "document_id": doc.id, "status": doc.status,
                                  "sha256": hashlib.sha256(data).hexdigest()})
        manifest = {"batch_export": True, "generated_at": utcnow().isoformat(), "lang": lang,
                    "filename_template": filename_template, "company": company_name,
                    "files": files, "missing": missing}
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, default=str))
    return buf.getvalue(), manifest, missing


def run_batch_export(session: Session, *, product_ids: list[str], doc_types: list[str], lang: str = "zh-CN",
                     filename_template: str = DEFAULT_FILENAME_TEMPLATE, include_docx: bool = False,
                     company_name: str = "", created_by: str | None = None) -> BatchExportRow:
    """Build the ZIP, store it, and record the export row (done / failed)."""
    row = BatchExportRow(id=str(uuid.uuid4()), status="pending", product_ids=list(product_ids),
                         doc_types=list(doc_types), lang=lang, filename_template=filename_template,
                         include_docx=include_docx, created_by=created_by)
    session.add(row)
    session.flush()
    try:
        data, _manifest, missing = build_batch_zip(
            session, product_ids=product_ids, doc_types=doc_types, lang=lang,
            filename_template=filename_template, include_docx=include_docx, company_name=company_name)
    except BatchExportError as exc:
        row.status = "failed"
        row.error = str(exc)
        session.flush()
        raise
    row.result_key = storage.put(f"batch-exports/{row.id}.zip", data)
    row.file_count = len(_manifest["files"])
    row.missing = missing
    row.status = "done"
    session.flush()
    return row
