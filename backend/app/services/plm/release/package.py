"""Release package: manifest, package hash and the delivery ZIP.

The manifest lists every document in the release with the hashes of its
structured content *and* of the rendered files, so ``package_hash`` covers
exactly what a customer receives. :func:`verify_files` re-reads the stored
files before each signature and at release; a file changed on disk after the
package was frozen stops the release.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any

from sqlalchemy.orm import Session

from ....db.models_plm import ProductRevisionRow, ProductRow, RegulatoryDocumentRow
from ...audit.hash_chain import canonical_json
from .. import storage

MANIFEST_VERSION = 1


def _file_hash(key: str | None) -> str | None:
    if not key:
        return None
    return hashlib.sha256(storage.get(key)).hexdigest()


def build_manifest(session: Session, rev: ProductRevisionRow, docs: list[RegulatoryDocumentRow],
                   gate_summary: dict[str, Any]) -> dict[str, Any]:
    product = session.get(ProductRow, rev.product_id)
    return {
        "manifest_version": MANIFEST_VERSION,
        "product": {"id": product.id, "code": product.code, "name_zh": product.name_zh, "name_en": product.name_en},
        "revision": {"id": rev.id, "rev": rev.rev, "formulation_version_id": rev.formulation_version_id},
        "documents": sorted(
            ({"id": d.id, "doc_type": d.doc_type, "lang": d.lang, "version_no": d.version_no, "batch_id": d.batch_id,
              "content_hash": d.content_hash, "pdf_sha256": _file_hash(d.rendered_pdf_key),
              "docx_sha256": _file_hash(d.rendered_docx_key)} for d in docs),
            key=lambda x: (x["doc_type"], x["lang"], x["id"])),
        "gate": gate_summary,
    }


def package_hash(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def verify_files(session: Session, manifest: dict[str, Any]) -> list[str]:
    """Documents whose stored content or files no longer match the manifest."""
    bad = []
    for entry in manifest.get("documents", []):
        d = session.get(RegulatoryDocumentRow, entry["id"])
        if d is None:
            bad.append(f"{entry['doc_type']} {entry['lang']}：文档记录不存在")
            continue
        if d.content_hash != entry["content_hash"]:
            bad.append(f"{entry['doc_type']} {entry['lang']}：内容哈希不符")
        try:
            if _file_hash(d.rendered_pdf_key) != entry["pdf_sha256"] or _file_hash(d.rendered_docx_key) != entry["docx_sha256"]:
                bad.append(f"{entry['doc_type']} {entry['lang']}：渲染文件已被修改")
        except FileNotFoundError:
            bad.append(f"{entry['doc_type']} {entry['lang']}：渲染文件丢失")
    return bad


def _filename(d: RegulatoryDocumentRow, product_code: str, rev: str, ext: str) -> str:
    batch = f"-{d.batch_id[:8]}" if d.batch_id else ""
    return f"{d.doc_type}/{product_code}-{rev}-{d.doc_type}-{d.lang}{batch}-v{d.version_no}.{ext}"


def build_zip(session: Session, manifest: dict[str, Any], phash: str, signatures: list[dict[str, Any]]) -> bytes:
    """Delivery ZIP: every rendered file plus ``manifest.json`` (manifest, package hash, signatures)."""
    buf = io.BytesIO()
    code, rev = manifest["product"]["code"], manifest["revision"]["rev"]
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        files = []
        for entry in manifest["documents"]:
            d = session.get(RegulatoryDocumentRow, entry["id"])
            for key, ext in ((d.rendered_pdf_key, "pdf"), (d.rendered_docx_key, "docx")):
                if key:
                    name = _filename(d, code, rev, ext)
                    data = storage.get(key)
                    zf.writestr(name, data)
                    files.append({"path": name, "sha256": hashlib.sha256(data).hexdigest()})
        zf.writestr("manifest.json", json.dumps(
            {"package_hash": phash, "manifest": manifest, "files": files, "signatures": signatures},
            ensure_ascii=False, indent=2, default=str))
    return buf.getvalue()
