"""Generate, version and render PLM documents for a product revision.

``generate_document`` assembles the context a builder needs from the database
(product, the revision's formulation version, a fresh GB 30000 classification,
GBZ 2.1 limits, ingredient listings, batch QC results), builds the structured
content, renders PDF / Word, stores the files and writes a *new*
``regulatory_documents`` row — an earlier version is never modified, so a
released document stays exactly what was signed.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ....clock import utcnow
from ....config import get_settings
from ....db.models_plm import (
    BatchQcResultRow,
    BatchRow,
    DocTemplateRow,
    ProductRevisionRow,
    ProductRow,
    RegulatoryDocumentRow,
)
from ....db.models_regulatory import SubstanceOELRow, SubstanceRow
from .. import storage
from ..diff import diff_documents
from ..ghs import service as ghs_service
from ..render.docx import render_docx
from ..render.label_pdf import render_label_pdf
from ..render.pdf import render_pdf
from .coa_builder import CoaContext, ResultItem, SpecItem, build_coa
from .common import content_hash, field_sources
from .confidential_ranges import DEFAULT_EDGES
from .label_builder import LabelContext, build_label
from .sds_builder import SdsContext, build_sds
from .tds_builder import TdsContext, build_tds

DOC_TYPES = ("sds", "tds", "coa", "label")


class DocumentError(ValueError):
    pass


# --------------------------------------------------------------------------- products / revisions / batches


def create_product(session: Session, *, code: str, name_zh: str, name_en: str = "", project_id: str | None = None,
                   created_by: str | None = None) -> ProductRow:
    if session.execute(select(ProductRow.id).where(ProductRow.code == code)).first():
        raise DocumentError(f"产品代码 {code} 已存在")
    row = ProductRow(id=str(uuid.uuid4()), code=code, name_zh=name_zh, name_en=name_en, project_id=project_id,
                     lifecycle_state="rd", created_by=created_by)
    session.add(row)
    session.flush()
    return row


def _next_rev(existing: list[str]) -> str:
    """A, B, ..., Z, AA, AB, ..."""
    def num(rev: str) -> int:
        n = 0
        for ch in rev:
            n = n * 26 + (ord(ch) - 64)
        return n

    n = max((num(r) for r in existing), default=0) + 1
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def create_revision(session: Session, product_id: str, *, formulation_version_id: str | None = None,
                    spec_snapshot: dict | None = None, created_by: str | None = None) -> ProductRevisionRow:
    if session.get(ProductRow, product_id) is None:
        raise DocumentError("产品不存在")
    revs = session.execute(select(ProductRevisionRow.rev).where(ProductRevisionRow.product_id == product_id)).scalars()
    row = ProductRevisionRow(id=str(uuid.uuid4()), product_id=product_id, rev=_next_rev(list(revs)),
                             formulation_version_id=formulation_version_id, spec_snapshot=spec_snapshot or {},
                             state="draft", created_by=created_by)
    session.add(row)
    session.flush()
    return row


def create_batch(session: Session, product_id: str, *, batch_no: str, revision_id: str | None = None,
                 produced_at=None, quantity_kg: float | None = None) -> BatchRow:
    if session.get(ProductRow, product_id) is None:
        raise DocumentError("产品不存在")
    if session.execute(select(BatchRow.id).where(BatchRow.product_id == product_id, BatchRow.batch_no == batch_no)).first():
        raise DocumentError(f"批号 {batch_no} 已存在")
    row = BatchRow(id=str(uuid.uuid4()), product_id=product_id, revision_id=revision_id, batch_no=batch_no,
                   produced_at=produced_at, quantity_kg=quantity_kg)
    session.add(row)
    session.flush()
    return row


def add_qc_results(session: Session, batch_id: str, results: list[dict[str, Any]]) -> list[BatchQcResultRow]:
    if session.get(BatchRow, batch_id) is None:
        raise DocumentError("批次不存在")
    rows = []
    for r in results:
        row = BatchQcResultRow(id=str(uuid.uuid4()), batch_id=batch_id, metric=r["metric"], value=r.get("value"),
                               value_text=r.get("value_text") or "", unit=r.get("unit") or "",
                               test_method=r.get("test_method") or "", measurement_id=r.get("measurement_id"))
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


# --------------------------------------------------------------------------- context helpers


def _company(override: dict | None) -> dict[str, Any]:
    s = get_settings()
    base = {"name": s.plm_company_name, "address": s.plm_company_address, "phone": s.plm_company_phone,
            "email": s.plm_company_email, "emergency_phone": s.plm_emergency_phone}
    base.update({k: v for k, v in (override or {}).items() if v not in (None, "")})
    return base


def _product_dict(product: ProductRow, extra: dict | None) -> dict[str, Any]:
    return {"name_zh": product.name_zh, "name_en": product.name_en, "code": product.code, **(extra or {})}


def _classify_revision(session: Session, rev: ProductRevisionRow, properties: dict | None) -> tuple[dict, str]:
    if not rev.formulation_version_id:
        raise DocumentError("产品修订版未关联配方版本，无法进行 GB 30000 分类")
    items = ghs_service.formulation_version_items(session, rev.formulation_version_id)
    result = ghs_service.classify_items(session, items, properties)
    row = ghs_service.persist(session, result, formulation_version_id=rev.formulation_version_id, revision_id=rev.id)
    return ghs_service.to_response(session, result), row.id


def _oel(session: Session, cas_numbers: list[str]) -> dict[str, dict]:
    if not cas_numbers:
        return {}
    rows = session.execute(
        select(SubstanceRow.cas_no, SubstanceOELRow.mac, SubstanceOELRow.pc_twa, SubstanceOELRow.pc_stel)
        .join(SubstanceOELRow, SubstanceOELRow.substance_id == SubstanceRow.id)
        .where(SubstanceRow.cas_no.in_(cas_numbers))
    ).all()
    return {r.cas_no: {"mac": r.mac, "pc_twa": r.pc_twa, "pc_stel": r.pc_stel} for r in rows}


def _listings(session: Session, cas_numbers: list[str]) -> list[str]:
    if not cas_numbers:
        return []
    out = []
    for s in session.execute(select(SubstanceRow).where(SubstanceRow.cas_no.in_(cas_numbers))).scalars():
        name = s.name_zh or s.name_en or s.cas_no
        if s.hazardous_catalog_no is not None:
            out.append(f"{name}（{s.cas_no}）：列入《危险化学品目录（2015 版）》，序号 {s.hazardous_catalog_no}")
        if s.highly_toxic:
            out.append(f"{name}：剧毒化学品")
        if s.precursor_control:
            out.append(f"{name}：易制毒化学品")
        if s.explosive_precursor:
            out.append(f"{name}：易制爆危险化学品")
    return out


def _components(disclosure: list[dict], trade_secrets: dict[str, str] | None) -> list[dict]:
    secrets = trade_secrets or {}
    out = []
    for d in disclosure:
        key = d.get("cas") if d.get("cas") in secrets else d["name"] if d["name"] in secrets else None
        out.append({**d, "trade_secret": bool(key) or d.get("trade_secret", False),
                    "generic_name": secrets.get(key) if key else None})
    return out


def _revision_meta(session: Session, rev: ProductRevisionRow, doc_type: str, lang: str, batch_id: str | None) -> dict:
    n = _next_version(session, rev.id, doc_type, lang, batch_id)
    return {"version": f"{rev.rev}.{n}", "date": utcnow().date().isoformat()}


def _next_version(session: Session, revision_id: str, doc_type: str, lang: str, batch_id: str | None) -> int:
    q = select(func.max(RegulatoryDocumentRow.version_no)).where(
        RegulatoryDocumentRow.revision_id == revision_id, RegulatoryDocumentRow.doc_type == doc_type,
        RegulatoryDocumentRow.lang == lang)
    if batch_id:
        q = q.where(RegulatoryDocumentRow.batch_id == batch_id)
    return (session.execute(q).scalar() or 0) + 1


def _template(session: Session, doc_type: str, lang: str) -> tuple[str | None, bytes | None]:
    row = session.execute(
        select(DocTemplateRow).where(DocTemplateRow.doc_type == doc_type, DocTemplateRow.lang == lang,
                                     DocTemplateRow.engine == "docx", DocTemplateRow.is_default.is_(True))
        .order_by(DocTemplateRow.version.desc()).limit(1)
    ).scalar_one_or_none()
    if row is None or not storage.exists(row.content_key):
        return None, None
    return row.id, storage.get(row.content_key)


# --------------------------------------------------------------------------- generation


def build_content(session: Session, rev: ProductRevisionRow, doc_type: str, lang: str, payload: dict) -> tuple[dict, dict]:
    """Return (content, extra row fields)."""
    product = session.get(ProductRow, rev.product_id)
    prod = _product_dict(product, payload.get("product"))
    company = _company(payload.get("company"))
    extra: dict[str, Any] = {}
    if doc_type in ("sds", "label"):
        cls, classification_id = _classify_revision(session, rev, payload.get("properties"))
        extra["classification_id"] = classification_id
        cas = [d["cas"] for d in cls["disclosure"] if d.get("cas")]
        if doc_type == "sds":
            props = {k: ({"value": v} if not isinstance(v, dict) else v)
                     for k, v in (payload.get("sds_properties") or {}).items()}
            # Values used for classification are SDS section 9 values too.
            for k, v in (payload.get("properties") or {}).items():
                if k in ("flash_point_c", "initial_boiling_point_c", "ph", "kinematic_viscosity_40c") and v is not None:
                    props.setdefault(k, {"value": v, "source": (payload.get("properties") or {}).get("sources", {}).get(k, "manual")})
            content = build_sds(SdsContext(
                product=prod, company=company, classification=cls, statements=cls["statements"], properties=props,
                components=_components(cls["disclosure"], payload.get("trade_secrets")), oel=_oel(session, cas),
                regulations=_listings(session, cas), revision={**_revision_meta(session, rev, doc_type, lang, None),
                                                               "change_summary": payload.get("change_summary")},
                band_edges=tuple(payload.get("band_edges") or DEFAULT_EDGES), lang=lang))
        else:
            label = payload.get("label") or {}
            content = build_label(LabelContext(
                product=prod, company=company, classification=cls, statements=cls["statements"],
                hazardous_ingredients=label.get("hazardous_ingredients") or [
                    d["name"] for d in cls["disclosure"] if not d.get("trade_secret")][:4],
                container_volume_l=label.get("container_volume_l"),
                size_mm=tuple(label.get("size_mm") or (100, 150)), qr_payload=label.get("qr_payload"), lang=lang))
    elif doc_type == "tds":
        tds = payload.get("tds") or {}
        content = build_tds(TdsContext(
            product={**prod, **(tds.get("product") or {})}, company=company, properties=tds.get("properties") or [],
            performance=tds.get("performance") or [], application=tds.get("application") or {},
            storage=tds.get("storage") or {}, revision=_revision_meta(session, rev, doc_type, lang, None), lang=lang))
    elif doc_type == "coa":
        coa = payload.get("coa") or {}
        batch = session.get(BatchRow, coa.get("batch_id") or "")
        if batch is None or batch.product_id != rev.product_id:
            raise DocumentError("COA 需要本产品的批次（coa.batch_id）")
        specs_raw = coa.get("specs") or (rev.spec_snapshot or {}).get("release_specs") or []
        if not specs_raw:
            raise DocumentError("COA 需要出厂规格（coa.specs 或修订版 spec_snapshot.release_specs）")
        qc = session.execute(select(BatchQcResultRow).where(BatchQcResultRow.batch_id == batch.id)).scalars().all()
        n = _next_version(session, rev.id, "coa", lang, batch.id)
        content = build_coa(CoaContext(
            product=prod, company=company,
            batch={"batch_no": batch.batch_no, "produced_at": batch.produced_at and batch.produced_at.date().isoformat(),
                   "quantity_kg": batch.quantity_kg, "expiry": coa.get("expiry")},
            specs=[SpecItem(**s) for s in specs_raw],
            results=[ResultItem(metric=r.metric, value=r.value, value_text=r.value_text or None,
                                conforms=None, measurement_id=r.measurement_id) for r in qc]
            + [ResultItem(**r) for r in coa.get("qualitative_results") or []],
            coa_no=f"COA-{product.code}-{batch.batch_no}-{n}", lang=lang))
        extra["batch_id"] = batch.id
        # Store the verdicts back on the QC rows for traceability.
        verdicts = content["meta"]["verdicts"]
        spec_by_metric = {s["metric"]: s for s in specs_raw}
        for r in qc:
            if r.metric in verdicts:
                r.verdict = verdicts[r.metric]
                r.spec_min = spec_by_metric[r.metric].get("min")
                r.spec_max = spec_by_metric[r.metric].get("max")
    else:
        raise DocumentError(f"未知文档类型 {doc_type}")
    return content, extra


def generate_document(session: Session, revision_id: str, doc_type: str, *, lang: str = "zh-CN",
                      payload: dict | None = None, created_by: str | None = None) -> RegulatoryDocumentRow:
    if doc_type not in DOC_TYPES:
        raise DocumentError(f"未知文档类型 {doc_type}")
    if lang not in ("zh-CN", "en-US"):
        raise DocumentError("lang 仅支持 zh-CN / en-US")
    rev = session.get(ProductRevisionRow, revision_id)
    if rev is None:
        raise DocumentError("产品修订版不存在")
    if rev.frozen_at is not None and doc_type != "coa":
        raise DocumentError("修订版已冻结（已发布），只能生成新的修订版文档")
    payload = payload or {}
    content, extra = build_content(session, rev, doc_type, lang, payload)
    batch_id = extra.get("batch_id")
    prev = session.execute(
        select(RegulatoryDocumentRow).where(
            RegulatoryDocumentRow.revision_id == rev.id, RegulatoryDocumentRow.doc_type == doc_type,
            RegulatoryDocumentRow.lang == lang,
            *( [RegulatoryDocumentRow.batch_id == batch_id] if batch_id else [] ))
        .order_by(RegulatoryDocumentRow.version_no.desc()).limit(1)
    ).scalar_one_or_none()
    version_no = (prev.version_no if prev else 0) + 1
    doc_id = str(uuid.uuid4())
    if extra.get("classification_id"):
        content["meta"]["classification_id"] = extra["classification_id"]
    if prev is not None:
        content["meta"]["diff_from_previous"] = {k: v for k, v in diff_documents(prev.content, content).items()
                                                 if k in ("major", "count")}

    if doc_type == "label":
        pdf, info = render_label_pdf(content)
        docx_key = None
    else:
        pdf, info = render_pdf(content)
        template_id, template = _template(session, doc_type, lang)
        docx_key = storage.put(f"documents/{doc_id}/{doc_type}-{lang}-v{version_no}.docx",
                               render_docx(content, template))
        extra["template_id"] = template_id
    content["meta"]["render"] = info
    if not info.get("font_embedded"):
        content["validation"]["warnings"].append("PDF 未嵌入中文字体（未找到可嵌入的 TrueType 字体），发布前须配置 plm_doc_font_path")
    pdf_key = storage.put(f"documents/{doc_id}/{doc_type}-{lang}-v{version_no}.pdf", pdf)

    row = RegulatoryDocumentRow(
        id=doc_id, revision_id=rev.id, batch_id=batch_id, doc_type=doc_type, lang=lang, version_no=version_no,
        status="draft", template_id=extra.get("template_id"), content=content, field_sources=field_sources(content),
        rendered_pdf_key=pdf_key, rendered_docx_key=docx_key, content_hash=content_hash(content),
        supersedes_id=prev.id if prev else None, created_by=created_by,
    )
    session.add(row)
    session.flush()
    return row


def save_template(session: Session, *, doc_type: str, lang: str, name: str, data: bytes, is_default: bool = True,
                  created_by: str | None = None) -> DocTemplateRow:
    if doc_type not in ("sds", "tds", "coa"):
        raise DocumentError("Word 模板仅适用于 sds / tds / coa")
    version = (session.execute(select(func.max(DocTemplateRow.version)).where(
        DocTemplateRow.doc_type == doc_type, DocTemplateRow.lang == lang, DocTemplateRow.name == name)).scalar() or 0) + 1
    tid = str(uuid.uuid4())
    key = storage.put(f"templates/{doc_type}/{lang}/{tid}.docx", data)
    if is_default:
        for old in session.execute(select(DocTemplateRow).where(
                DocTemplateRow.doc_type == doc_type, DocTemplateRow.lang == lang, DocTemplateRow.is_default.is_(True))).scalars():
            old.is_default = False
    row = DocTemplateRow(id=tid, doc_type=doc_type, lang=lang, name=name, version=version, engine="docx",
                         content_key=key, is_default=is_default, created_by=created_by)
    session.add(row)
    session.flush()
    return row
