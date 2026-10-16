"""PLM & Release Center API — GB 30000 classification and the substance database (Workspace 5)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from ._principal import CurrentUser, current_user, optional_user

router = APIRouter(prefix="/plm", tags=["plm"])


class HazardIn(BaseModel):
    hazard_class: str
    category: str
    h_code: str | None = None
    scl: list[dict] | None = None
    m_factor_acute: int | None = None
    m_factor_chronic: int | None = None
    ate_value: float | None = None
    ate_unit: str | None = None
    non_additive: bool = False


class ComponentIn(BaseModel):
    name: str = ""
    pct: float = Field(ge=0, le=100)
    cas: str | None = None
    substance_id: str | None = None
    material_id: str | None = None
    # Given → used as-is; omitted → looked up in the substance database.
    hazards: list[HazardIn] | None = None
    assessed: bool = True
    trade_secret: bool = False


class PropertiesIn(BaseModel):
    phys_state: Literal["liquid", "solid", "gas", "aerosol", "paste"] = "liquid"
    flash_point_c: float | None = None
    initial_boiling_point_c: float | None = None
    ph: float | None = Field(default=None, ge=0, le=14)
    kinematic_viscosity_40c: float | None = Field(default=None, ge=0)
    metal_corrosion_rate_mm_per_year: float | None = Field(default=None, ge=0)
    sustained_combustion: bool | None = None
    inhalation_form: Literal["gas", "vapour", "dust_mist"] | None = None
    product_category: str | None = None
    sources: dict[str, str] = Field(default_factory=dict)


class ClassifyRequest(BaseModel):
    components: list[ComponentIn] = Field(min_length=1, max_length=500)
    properties: PropertiesIn = Field(default_factory=PropertiesIn)
    mode: Literal["max", "typical"] = "max"
    include_transport: bool = True
    formulation_version_id: str | None = None  # persist the result against this version


class ClassifyVersionRequest(BaseModel):
    properties: PropertiesIn = Field(default_factory=PropertiesIn)
    mode: Literal["max", "typical"] = "max"
    include_transport: bool = True
    persist: bool = True


def _session():
    from ..db.database import default_session_factory

    return default_session_factory()()


@router.get("/ghs/ruleset")
def get_ruleset() -> dict[str, Any]:
    from ..services.plm.ghs.ruleset import available_rulesets, load_ruleset

    rs = load_ruleset()
    return {"version": rs.version, "status": rs.status, "available": available_rulesets(), "parameters": rs.as_dict()}


@router.post("/ghs/classify")
def classify_composition(body: ClassifyRequest) -> dict[str, Any]:
    """Classify a composition (real-time: called by the formulation editor's compliance panel)."""
    from ..services.plm.ghs import service

    items = [c.model_dump(exclude_none=True) for c in body.components]
    with _session() as session:
        result = service.classify_items(session, items, body.properties.model_dump(), mode=body.mode,
                                        include_transport=body.include_transport)
        response = service.to_response(session, result)
        if body.formulation_version_id:
            row = service.persist(session, result, formulation_version_id=body.formulation_version_id)
            session.commit()
            response["classification_id"] = row.id
    return response


@router.post("/ghs/classify/formulation-versions/{version_id}")
def classify_formulation_version(version_id: str, body: ClassifyVersionRequest) -> dict[str, Any]:
    from ..services.plm.ghs import service

    with _session() as session:
        try:
            items = service.formulation_version_items(session, version_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="配方版本不存在") from None
        if not items:
            raise HTTPException(status_code=422, detail="该配方版本没有组分")
        result = service.classify_items(session, items, body.properties.model_dump(), mode=body.mode,
                                        include_transport=body.include_transport)
        response = service.to_response(session, result)
        if body.persist:
            row = service.persist(session, result, formulation_version_id=version_id)
            session.commit()
            response["classification_id"] = row.id
    return response


@router.get("/substances")
def search_substances(q: str = Query(..., min_length=1, max_length=200), limit: int = Query(20, ge=1, le=100)
                      ) -> list[dict[str, Any]]:
    """Look up substances by CAS (exact) or Chinese / English name (substring)."""
    from sqlalchemy import or_, select

    from ..db.models_regulatory import SubstanceClassificationRow, SubstanceRow
    from ..services.plm.regulatory import cas as cas_mod
    from ..services.plm.regulatory.source_priority import effective

    with _session() as session:
        norm = cas_mod.normalize(q)
        if norm:
            stmt = select(SubstanceRow).where(SubstanceRow.cas_no == norm)
        else:
            like = f"%{q.strip()}%"
            stmt = (select(SubstanceRow)
                    .where(or_(SubstanceRow.name_zh.like(like), SubstanceRow.name_en.ilike(like)))
                    .order_by(SubstanceRow.hazardous_catalog_no.is_(None), SubstanceRow.name_zh)
                    .limit(limit))
        subs = session.execute(stmt).scalars().all()
        ids = [s.id for s in subs]
        cls_rows = session.execute(
            select(SubstanceClassificationRow).where(SubstanceClassificationRow.substance_id.in_(ids))
        ).scalars().all() if ids else []
        by_sub: dict[str, list] = {}
        for r in cls_rows:
            by_sub.setdefault(r.substance_id, []).append(r)
        return [{
            "id": s.id, "cas_no": s.cas_no, "name_zh": s.name_zh, "name_en": s.name_en,
            "hazardous_catalog_no": s.hazardous_catalog_no, "highly_toxic": s.highly_toxic,
            "precursor_control": s.precursor_control, "explosive_precursor": s.explosive_precursor,
            "classifications": [{"hazard_class": r.hazard_class, "category": r.category, "h_code": r.h_code,
                                 "source": r.source, "is_minimum": r.is_minimum, "locked": r.locked_at is not None}
                                for r in effective(by_sub.get(s.id, []))],
        } for s in subs]


# =========================================================================== products & documents (M4)


class ProductIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name_zh: str = Field(min_length=1, max_length=200)
    name_en: str = ""
    project_id: str | None = None


class RevisionIn(BaseModel):
    formulation_version_id: str | None = None
    # e.g. {"release_specs": [{"metric", "label", "unit", "method", "min", "max", "text", "comparison"}]}
    spec_snapshot: dict = Field(default_factory=dict)


class BatchIn(BaseModel):
    batch_no: str = Field(min_length=1, max_length=64)
    revision_id: str | None = None
    produced_at: str | None = None  # ISO date
    quantity_kg: float | None = Field(default=None, ge=0)


class QcResultIn(BaseModel):
    metric: str
    value: float | None = None
    value_text: str | None = None
    unit: str = ""
    test_method: str = ""
    measurement_id: str | None = None


class QcResultsIn(BaseModel):
    results: list[QcResultIn] = Field(min_length=1)


class DocumentIn(BaseModel):
    doc_type: Literal["sds", "tds", "coa", "label"]
    lang: Literal["zh-CN", "en-US"] = "zh-CN"
    properties: PropertiesIn | None = None  # mixture properties for classification (sds / label)
    sds_properties: dict[str, Any] = Field(default_factory=dict)  # section 9 values: key → value or {value, source, method}
    trade_secrets: dict[str, str] = Field(default_factory=dict)  # CAS or name → generic name
    band_edges: list[float] | None = None
    company: dict[str, Any] = Field(default_factory=dict)
    product: dict[str, Any] = Field(default_factory=dict)  # recommended_use, description, features, ...
    change_summary: str | None = None
    tds: dict[str, Any] = Field(default_factory=dict)
    coa: dict[str, Any] = Field(default_factory=dict)
    label: dict[str, Any] = Field(default_factory=dict)


def _doc_summary(row) -> dict[str, Any]:
    v = (row.content or {}).get("validation") or {}
    return {"id": row.id, "revision_id": row.revision_id, "batch_id": row.batch_id, "doc_type": row.doc_type,
            "lang": row.lang, "version_no": row.version_no, "status": row.status, "content_hash": row.content_hash,
            "supersedes_id": row.supersedes_id, "created_at": row.created_at,
            "has_pdf": bool(row.rendered_pdf_key), "has_docx": bool(row.rendered_docx_key),
            "missing": v.get("missing", []), "warnings": v.get("warnings", [])}


def _doc_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.post("/products", status_code=201)
def create_product(body: ProductIn) -> dict[str, Any]:
    from ..services.plm.documents import service as docs

    with _session() as session:
        try:
            row = docs.create_product(session, code=body.code, name_zh=body.name_zh, name_en=body.name_en,
                                      project_id=body.project_id)
        except docs.DocumentError as exc:
            raise _doc_error(exc) from None
        session.commit()
        return {"id": row.id, "code": row.code, "lifecycle_state": row.lifecycle_state}


@router.get("/products/{product_id}")
def get_product(product_id: str) -> dict[str, Any]:
    from sqlalchemy import select

    from ..db.models_plm import BatchRow, ProductRevisionRow, ProductRow

    with _session() as session:
        p = session.get(ProductRow, product_id)
        if p is None:
            raise HTTPException(status_code=404, detail="产品不存在")
        revs = session.execute(select(ProductRevisionRow).where(ProductRevisionRow.product_id == p.id)
                               .order_by(ProductRevisionRow.created_at)).scalars().all()
        batches = session.execute(select(BatchRow).where(BatchRow.product_id == p.id)).scalars().all()
        return {"id": p.id, "code": p.code, "name_zh": p.name_zh, "name_en": p.name_en,
                "lifecycle_state": p.lifecycle_state, "project_id": p.project_id,
                "revisions": [{"id": r.id, "rev": r.rev, "state": r.state, "formulation_version_id": r.formulation_version_id,
                               "frozen_at": r.frozen_at} for r in revs],
                "batches": [{"id": b.id, "batch_no": b.batch_no, "status": b.status} for b in batches]}


@router.post("/products/{product_id}/revisions", status_code=201)
def create_revision(product_id: str, body: RevisionIn) -> dict[str, Any]:
    from ..services.plm.documents import service as docs

    with _session() as session:
        try:
            row = docs.create_revision(session, product_id, formulation_version_id=body.formulation_version_id,
                                       spec_snapshot=body.spec_snapshot)
        except docs.DocumentError as exc:
            raise _doc_error(exc) from None
        session.commit()
        return {"id": row.id, "rev": row.rev, "state": row.state}


@router.post("/products/{product_id}/batches", status_code=201)
def create_batch(product_id: str, body: BatchIn) -> dict[str, Any]:
    from datetime import datetime

    from ..services.plm.documents import service as docs

    with _session() as session:
        try:
            row = docs.create_batch(session, product_id, batch_no=body.batch_no, revision_id=body.revision_id,
                                    produced_at=datetime.fromisoformat(body.produced_at) if body.produced_at else None,
                                    quantity_kg=body.quantity_kg)
        except (docs.DocumentError, ValueError) as exc:
            raise _doc_error(exc) from None
        session.commit()
        return {"id": row.id, "batch_no": row.batch_no}


@router.post("/batches/{batch_id}/qc-results", status_code=201)
def add_qc_results(batch_id: str, body: QcResultsIn) -> dict[str, Any]:
    from ..services.plm.documents import service as docs

    with _session() as session:
        try:
            rows = docs.add_qc_results(session, batch_id, [r.model_dump() for r in body.results])
        except docs.DocumentError as exc:
            raise _doc_error(exc) from None
        session.commit()
        return {"count": len(rows)}


@router.post("/revisions/{revision_id}/documents", status_code=201)
def generate_document(revision_id: str, body: DocumentIn,
                      user: CurrentUser | None = Depends(optional_user)) -> dict[str, Any]:
    """Generate a new version of an SDS / TDS / COA / label (never edits an existing version)."""
    from ..services.plm.documents import service as docs

    payload = body.model_dump(exclude={"doc_type", "lang"})
    payload["properties"] = body.properties.model_dump() if body.properties else None
    with _session() as session:
        try:
            row = docs.generate_document(session, revision_id, body.doc_type, lang=body.lang, payload=payload,
                                         created_by=user.id if user else None)
        except (docs.DocumentError, KeyError) as exc:
            raise _doc_error(exc) from None
        session.commit()
        return {**_doc_summary(row), "content": row.content}


@router.get("/revisions/{revision_id}/documents")
def list_documents(revision_id: str) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from ..db.models_plm import RegulatoryDocumentRow

    with _session() as session:
        rows = session.execute(select(RegulatoryDocumentRow).where(RegulatoryDocumentRow.revision_id == revision_id)
                               .order_by(RegulatoryDocumentRow.doc_type, RegulatoryDocumentRow.lang,
                                         RegulatoryDocumentRow.version_no)).scalars().all()
        return [_doc_summary(r) for r in rows]


@router.get("/documents/{document_id}")
def get_document(document_id: str) -> dict[str, Any]:
    from ..db.models_plm import RegulatoryDocumentRow

    with _session() as session:
        row = session.get(RegulatoryDocumentRow, document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        return {**_doc_summary(row), "content": row.content, "field_sources": row.field_sources}


@router.get("/documents/{document_id}/file/{fmt}")
def download_document(document_id: str, fmt: Literal["pdf", "docx"]):
    from fastapi.responses import Response

    from ..db.models_plm import RegulatoryDocumentRow
    from ..services.plm import storage

    with _session() as session:
        row = session.get(RegulatoryDocumentRow, document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        key = row.rendered_pdf_key if fmt == "pdf" else row.rendered_docx_key
        if not key or not storage.exists(key):
            raise HTTPException(status_code=404, detail=f"该文档没有 {fmt} 文件")
        media = "application/pdf" if fmt == "pdf" else \
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        name = f"{row.doc_type}-{row.lang}-v{row.version_no}.{fmt}"
        return Response(storage.get(key), media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/documents/{document_id}/diff")
def diff_document(document_id: str, against: str | None = None) -> dict[str, Any]:
    """Field-level diff against ``against`` (default: the version this one supersedes)."""
    from ..db.models_plm import RegulatoryDocumentRow
    from ..services.plm.diff import diff_documents

    with _session() as session:
        row = session.get(RegulatoryDocumentRow, document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        other_id = against or row.supersedes_id
        if not other_id:
            raise HTTPException(status_code=404, detail="没有可比较的上一版本")
        other = session.get(RegulatoryDocumentRow, other_id)
        if other is None or other.doc_type != row.doc_type:
            raise HTTPException(status_code=422, detail="只能比较同一类型的文档")
        return {"from": other.id, "to": row.id, **diff_documents(other.content, row.content)}


@router.post("/templates", status_code=201)
async def upload_template(
    file: UploadFile = File(...),
    doc_type: Literal["sds", "tds", "coa"] = Form(...),
    lang: Literal["zh-CN", "en-US"] = Form("zh-CN"),
    name: str = Form(..., min_length=1, max_length=120),
    is_default: bool = Form(True),
) -> dict[str, Any]:
    """Upload a Word template (docxtpl / Jinja tags) used for future renderings of that document type."""
    from ..services.plm.documents import service as docs

    data = await file.read()
    if not data.startswith(b"PK"):
        raise HTTPException(status_code=422, detail="模板必须是 .docx 文件")
    try:
        from io import BytesIO

        from docxtpl import DocxTemplate

        DocxTemplate(BytesIO(data)).get_undeclared_template_variables()
    except Exception as exc:  # noqa: BLE001 - any parse failure means an unusable template
        raise HTTPException(status_code=422, detail=f"模板无法解析：{exc}") from None
    with _session() as session:
        try:
            row = docs.save_template(session, doc_type=doc_type, lang=lang, name=name, data=data, is_default=is_default)
        except docs.DocumentError as exc:
            raise _doc_error(exc) from None
        session.commit()
        return {"id": row.id, "doc_type": row.doc_type, "lang": row.lang, "name": row.name, "version": row.version}


# --------------------------------------------------------------------------- M5: release gate & sign-off


class PasswordConfirm(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    comment: str = Field(default="", max_length=2000)


class WaiverIn(PasswordConfirm):
    check: Literal["objectives", "data_gaps"]
    item: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)


class SignIn(PasswordConfirm):
    decision: Literal["approve", "reject"]


class WithdrawIn(PasswordConfirm):
    reason: str = Field(min_length=1, max_length=2000)


def _release_call(session, fn, *args, **kwargs):
    """Run a sign-off operation, mapping its errors to HTTP. A wrong password is committed first so the
    failed attempt (and lock-out counter) is kept."""
    from ..services.identity.accounts import AuthError
    from ..services.plm.release import gate, signoff

    try:
        return fn(session, *args, **kwargs)
    except AuthError as exc:
        session.commit()
        raise HTTPException(status_code=401, detail=f"电子签名身份验证失败：{exc}") from None
    except signoff.GateFailed as exc:
        session.commit()  # keeps the gate_failed audit record
        raise HTTPException(status_code=409, detail={"message": str(exc), "gate": exc.result}) from None
    except signoff.Forbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    except (signoff.ReleaseError, gate.GateError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/documents/{document_id}/approve")
def approve_document(document_id: str, body: PasswordConfirm, user: CurrentUser = Depends(current_user)
                     ) -> dict[str, Any]:
    """Review e-signature on one document (COA: issue). Required before the release gate passes."""
    from ..services.plm.release import signoff

    with _session() as session:
        row = _release_call(session, signoff.approve_document, document_id, user_id=user.id,
                            principal=user.principal, password=body.password, comment=body.comment)
        session.commit()
        return _doc_summary(row)


@router.post("/revisions/{revision_id}/candidate")
def mark_candidate(revision_id: str, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.plm.release import signoff

    with _session() as session:
        rev = _release_call(session, signoff.mark_candidate, revision_id, user_id=user.id, principal=user.principal)
        session.commit()
        return {"id": rev.id, "rev": rev.rev, "state": rev.state}


@router.get("/revisions/{revision_id}/gate")
def evaluate_gate(revision_id: str) -> dict[str, Any]:
    """Run the ten release-gate checks now (read-only; the checklist view)."""
    from ..services.plm.release import gate

    with _session() as session:
        try:
            return gate.evaluate(session, revision_id)
        except gate.GateError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None


@router.post("/revisions/{revision_id}/gate/waivers", status_code=201)
def add_gate_waiver(revision_id: str, body: WaiverIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.plm.release import signoff

    with _session() as session:
        row = _release_call(session, signoff.add_waiver, revision_id, check_key=body.check, item=body.item,
                            reason=body.reason, user_id=user.id, principal=user.principal, password=body.password)
        session.commit()
        return {"id": row.id, "check": row.check_code, "item": row.item_key, "reason": row.reason}


@router.post("/revisions/{revision_id}/submit", status_code=201)
def submit_release(revision_id: str, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    """Gate + freeze the package + open the 4-step sign-off. 409 with the gate result when it fails."""
    from ..services.plm.release import signoff

    with _session() as session:
        pkg = _release_call(session, signoff.submit, revision_id, user_id=user.id, principal=user.principal)
        session.commit()
        return signoff.package_dict(session, pkg)


@router.get("/revisions/{revision_id}/packages")
def list_packages(revision_id: str) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from ..db.models_plm import ReleasePackageRow
    from ..services.plm.release import signoff

    with _session() as session:
        rows = session.execute(select(ReleasePackageRow).where(ReleasePackageRow.revision_id == revision_id)
                               .order_by(ReleasePackageRow.created_at)).scalars().all()
        return [signoff.package_dict(session, r) for r in rows]


@router.get("/packages/{package_id}")
def get_package(package_id: str) -> dict[str, Any]:
    from ..db.models_plm import ReleasePackageRow
    from ..services.plm.release import signoff

    with _session() as session:
        pkg = session.get(ReleasePackageRow, package_id)
        if pkg is None:
            raise HTTPException(status_code=404, detail="发布包不存在")
        return signoff.package_dict(session, pkg)


@router.post("/packages/{package_id}/sign")
def sign_package(package_id: str, body: SignIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    """Sign the current step (approve / reject). The last approval releases the product."""
    from ..services.plm.release import signoff

    with _session() as session:
        pkg = _release_call(session, signoff.sign_step, package_id, decision=body.decision, user_id=user.id,
                            principal=user.principal, password=body.password, comment=body.comment)
        session.commit()
        if pkg.status == "released":
            signoff.run_connectors(session, pkg.id, action="publish")
            session.commit()
        return signoff.package_dict(session, pkg)


@router.post("/packages/{package_id}/withdraw")
def withdraw_package(package_id: str, body: WithdrawIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.plm.release import signoff

    with _session() as session:
        pkg = _release_call(session, signoff.withdraw, package_id, reason=body.reason, user_id=user.id,
                            principal=user.principal, password=body.password)
        session.commit()
        signoff.run_connectors(session, pkg.id, action="withdraw", reason=body.reason)
        session.commit()
        return signoff.package_dict(session, pkg)


@router.post("/packages/{package_id}/publish")
def republish_package(package_id: str, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    """Retry the publish connectors for a released package."""
    from ..db.models_plm import ProductRevisionRow, ProductRow, ReleasePackageRow
    from ..services.plm.release import signoff

    with _session() as session:
        pkg = session.get(ReleasePackageRow, package_id)
        if pkg is None:
            raise HTTPException(status_code=404, detail="发布包不存在")
        product = session.get(ProductRow, session.get(ProductRevisionRow, pkg.revision_id).product_id)
        user.require("release.publish", product.project_id)
        if pkg.status != "released":
            raise HTTPException(status_code=409, detail="只能推送已发布的发布包")
        results = signoff.run_connectors(session, pkg.id, action="publish")
        session.commit()
        return {"results": results}


@router.get("/packages/{package_id}/delivery")
def download_delivery(package_id: str):
    """The delivery ZIP (all PDF / Word files + manifest.json with hashes and signatures)."""
    from fastapi.responses import Response

    from ..db.models_plm import ReleasePackageRow
    from ..services.plm import storage

    with _session() as session:
        pkg = session.get(ReleasePackageRow, package_id)
        if pkg is None or not pkg.delivery_key:
            raise HTTPException(status_code=404, detail="交付包尚未生成（发布后生成）")
        name = pkg.delivery_key.rsplit("/", 1)[-1]
        return Response(storage.get(pkg.delivery_key), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/connectors")
def list_connectors() -> dict[str, Any]:
    from ..services.connectors.base import RESERVED_CONNECTORS, configured_connectors

    return {"configured": [{"name": c.name, "target": getattr(c, "url", "")} for c in configured_connectors()],
            "reserved": [{"name": k, "description": v} for k, v in RESERVED_CONNECTORS.items()]}


@router.get("/audit")
def audit_log(entity_type: str | None = None, entity_id: str | None = None, limit: int = Query(200, ge=1, le=1000),
              user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    """Hash-chained audit records (newest first) and a chain-integrity check."""
    from sqlalchemy import select

    from ..db.models_plm import AuditLogRow
    from ..services.audit.hash_chain import verify_chain

    user.require("audit.read")
    with _session() as session:
        q = select(AuditLogRow)
        if entity_type:
            q = q.where(AuditLogRow.entity_type == entity_type)
        if entity_id:
            q = q.where(AuditLogRow.entity_id == entity_id)
        rows = session.execute(q.order_by(AuditLogRow.seq.desc()).limit(limit)).scalars().all()
        check = verify_chain(session)
        return {"chain_ok": check.ok, "chain_rows": check.rows, "first_bad_seq": check.first_bad_seq,
                "records": [{"seq": r.seq, "ts": r.ts.isoformat(), "actor": r.actor, "action": r.action,
                             "entity_type": r.entity_type, "entity_id": r.entity_id, "detail": r.detail,
                             "hash": r.hash} for r in rows]}
