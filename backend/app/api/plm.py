"""PLM & Release Center API — GB 30000 classification and the substance database (Workspace 5)."""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from ._principal import CurrentUser, current_user, optional_user

router = APIRouter(prefix="/plm", tags=["plm"])

# WebSocket router: mounted WITHOUT the /api prefix (see main.py), so the
# endpoint is /ws/formulations/{id}/compliance. Browsers cannot set an
# Authorization header on a WebSocket handshake; classification is a read-only
# computation with no persistence, same as POST /api/plm/ghs/classify, so the
# endpoint carries no auth dependency (parity with the REST endpoint).


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
    # Aerosol tests (GB 30000.4); foam_test_result = {"flame_height_cm": .., "flame_duration_s": ..}.
    heat_of_combustion_kj_g: float | None = Field(default=None, ge=0)
    ignition_distance_cm: float | None = Field(default=None, ge=0)
    foam_test_result: dict[str, float] | None = None
    sources: dict[str, str] = Field(default_factory=dict)


class BridgingReferenceIn(BaseModel):
    ref_id: str = ""
    label: str = ""
    components: list[ComponentIn] = Field(default_factory=list)
    hazards: list[HazardIn] = Field(default_factory=list)


class BridgingIn(BaseModel):
    principle: Literal["dilution", "batching", "concentration", "interpolation", "substantially_similar"]
    references: list[BridgingReferenceIn] = Field(default_factory=list)


class ClassifyRequest(BaseModel):
    components: list[ComponentIn] = Field(min_length=1, max_length=500)
    properties: PropertiesIn = Field(default_factory=PropertiesIn)
    mode: Literal["max", "typical"] = "max"
    include_transport: bool = True
    formulation_version_id: str | None = None  # persist the result against this version
    bridging: BridgingIn | None = None  # GB 30000 bridging principle + reference mixture(s)


class ClassifyVersionRequest(BaseModel):
    properties: PropertiesIn = Field(default_factory=PropertiesIn)
    mode: Literal["max", "typical"] = "max"
    include_transport: bool = True
    persist: bool = True
    bridging: BridgingIn | None = None


def _session():
    from ..db.database import default_session_factory

    return default_session_factory()()


@router.get("/ghs/ruleset")
def get_ruleset() -> dict[str, Any]:
    from ..services.plm.ghs.ruleset import available_rulesets, load_ruleset

    rs = load_ruleset()
    return {"version": rs.version, "status": rs.status, "available": available_rulesets(), "parameters": rs.as_dict()}


@router.post("/rulesets/{version}/recompute")
def recompute_ruleset(version: str, apply: bool = Query(default=False)) -> dict[str, Any]:
    """Plan 3-10: batch re-classification on a rule-set release.

    ``apply=false`` (default) is a dry run: reports affected products and
    what would change, persists nothing. ``apply=true`` persists each fresh
    result as a *new* classification row; old rows are never mutated, and
    documents citing them still need regeneration (listed per product).
    Requires ``plm.document.write`` (see ``_rbac_guard``).
    """
    from ..services.plm.release import ruleset_recompute

    with _session() as session:
        try:
            out = ruleset_recompute.recompute_ruleset(session, version, apply=apply)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if apply:
            session.commit()
        return out


@router.post("/ghs/classify")
def classify_composition(body: ClassifyRequest) -> dict[str, Any]:
    """Classify a composition (real-time: called by the formulation editor's compliance panel)."""
    from ..services.plm.ghs import service

    items = [c.model_dump(exclude_none=True) for c in body.components]
    with _session() as session:
        result = service.classify_items(session, items, body.properties.model_dump(), mode=body.mode,
                                        include_transport=body.include_transport,
                                        bridging=body.bridging.model_dump() if body.bridging else None)
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
                                        include_transport=body.include_transport,
                                        bridging=body.bridging.model_dump() if body.bridging else None)
        response = service.to_response(session, result)
        if body.persist:
            row = service.persist(session, result, formulation_version_id=version_id)
            session.commit()
            response["classification_id"] = row.id
    return response


# --------------------------------------------------------------------------- WebSocket: real-time compliance


ws_router = APIRouter()


def _classify_ws_sync(payload: dict[str, Any]) -> dict[str, Any]:
    """Classification for the WebSocket path (runs in a worker thread so the event loop stays responsive).

    Same response structure as POST /api/plm/ghs/classify, via the cached
    lookup path the real-time editor needs to meet its 150 ms budget.
    """
    from ..services.plm.ghs import service

    req = ClassifyRequest(**payload)
    with _session() as session:
        result, _built = service.build_and_classify(
            session, [c.model_dump(exclude_none=True) for c in req.components],
            req.properties.model_dump(), mode=req.mode, include_transport=req.include_transport, cached=True)
        return service.to_response(session, result, cached=True)


@ws_router.websocket("/ws/formulations/{formulation_id}/compliance")
async def formulation_compliance_ws(websocket: WebSocket, formulation_id: str) -> None:
    """Real-time GB 30000 compliance over WebSocket (方案 §4.6 / §3.6).

    Protocol (JSON text frames):
      client → {"type": "classify", "components": [...], "properties": {...},
                "mode": "max", "include_transport": true}
        ``components`` omitted → classify the saved formulation version
        ``formulation_id`` (``"new"`` = unsaved editor: components required).
      client → {"type": "ping"} → server {"type": "pong"}
      server → {"type": "classification", ...same fields as POST /api/plm/ghs/classify...}
      server → {"type": "error", "detail": "..."} (the socket stays open)

    The socket never persists anything: classification results are computed
    in-memory only; saving still goes through the REST endpoints.
    """
    from ..services.plm.ghs import service

    await websocket.accept()
    while True:
        try:
            msg = await websocket.receive_json()
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001 - malformed frame: tell the client, keep the socket open
            try:
                await websocket.send_json({"type": "error", "detail": "消息必须是 JSON 对象"})
            except Exception:  # noqa: BLE001 - client went away mid-send
                return
            continue
        if not isinstance(msg, dict):
            await websocket.send_json({"type": "error", "detail": "消息必须是 JSON 对象"})
            continue
        mtype = msg.get("type", "classify")
        if mtype == "ping":
            await websocket.send_json({"type": "pong"})
            continue
        if mtype != "classify":
            await websocket.send_json({"type": "error", "detail": f"未知消息类型：{mtype}"})
            continue
        try:
            payload = {"components": msg.get("components"), "properties": msg.get("properties") or {},
                       "mode": msg.get("mode", "max"), "include_transport": msg.get("include_transport", True)}
            if payload["components"] is None:
                if formulation_id == "new":
                    raise ValueError("未保存的配方请提供组分（components）")
                with _session() as session:
                    try:
                        payload["components"] = service.formulation_version_items(session, formulation_id)
                    except KeyError:
                        raise ValueError("配方版本不存在") from None
                if not payload["components"]:
                    raise ValueError("该配方版本没有组分")
            response = await asyncio.to_thread(_classify_ws_sync, payload)
        except ValidationError as exc:
            await websocket.send_json({"type": "error", "detail": f"请求校验失败：{exc.errors()}"})
            continue
        except ValueError as exc:
            await websocket.send_json({"type": "error", "detail": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 - classification must never kill the socket
            await websocket.send_json({"type": "error", "detail": f"分类失败：{exc}"})
            continue
        await websocket.send_json({"type": "classification", **response})


@router.get("/substances")
def search_substances(q: str = Query(..., min_length=1, max_length=200), limit: int = Query(20, ge=1, le=100)
                      ) -> list[dict[str, Any]]:
    """Look up substances by CAS (exact) or Chinese / English name (substring)."""
    from ..agents import regulatory as reg_agent

    with _session() as session:
        return reg_agent.lookup_substance(session, q, limit=limit)


@router.post("/substances/sds-import", status_code=201)
async def import_supplier_sds(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(current_user),
) -> dict[str, Any]:
    """Import one supplier SDS file (PDF / DOCX / text) into the substance database.

    补齐计划 1-5: parse → upsert substance by CAS → replace this substance's
    unlocked ``supplier_sds`` classifications (confidence 0.95). A mixture SDS
    (several CAS numbers), a missing product name / CAS / classification, or
    an unparsable file is a 422 with an explicit reason — never a silent skip.
    """
    from ..api._uploads import read_upload_capped
    from ..services.plm.regulatory import supplier_sds

    filename = file.filename or "upload"
    try:
        content = await read_upload_capped(file, filename)
        parsed = supplier_sds.parse_sds_text(supplier_sds.extract_text(content, filename))
    except supplier_sds.SdsParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    with _session() as session:
        try:
            result = supplier_sds.import_sds(
                session, parsed, filename=filename, digest=supplier_sds.file_digest(content)
            )
        except supplier_sds.SdsParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        session.commit()
        return result


@router.post("/substances/{substance_id}/pubchem-enrich", status_code=202)
def enrich_substance_pubchem(
    substance_id: str, user: CurrentUser = Depends(current_user)
) -> Any:
    """Enqueue the PubChem GHS enrichment task (补齐计划 1-6) for one substance.

    The worker resolves the substance to a PubChem CID, fetches its GHS
    classification from PUG-View, and writes ``source='pubchem'`` rows
    (confidence 0.6). 404 when the substance does not exist; 503 when the
    task queue (Redis) is unreachable.
    """
    from ..db.models_regulatory import SubstanceRow
    from ..worker.tasks import run_pubchem_enrich_task
    from ._dispatch import submit

    with _session() as session:
        exists = session.query(SubstanceRow.id).filter(SubstanceRow.id == substance_id).first()
    if not exists:
        raise HTTPException(status_code=404, detail=f"物质不存在：{substance_id}")
    return submit(run_pubchem_enrich_task, {"substance_id": substance_id}, kind="pubchem_enrich")


class RegulatoryAskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    intent: Literal["auto", "substance", "explain", "substitute", "standard"] = "auto"
    components: list[dict[str, Any]] | None = None  # explain / substitute need these; role hint per item optional
    properties: PropertiesIn | None = None
    limit: int = Field(default=5, ge=1, le=20)


@router.post("/regulatory/ask")
def regulatory_ask(body: RegulatoryAskIn) -> dict[str, Any]:
    """Read-only regulatory agent: substance lookup, classification explanation, hazard-reduction
    suggestions, GB / national-standard Q&A. Never writes anything."""
    from ..agents import regulatory as reg_agent

    with _session() as session:
        return reg_agent.ask(session, question=body.question, intent=body.intent,
                             components=body.components,
                             properties=body.properties.model_dump() if body.properties else None,
                             limit=body.limit)


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
    pdfa: bool = False  # PDF/A-2b 输出（3-13；字体未嵌入时 422）


def _doc_summary(row) -> dict[str, Any]:
    v = (row.content or {}).get("validation") or {}
    return {"id": row.id, "revision_id": row.revision_id, "batch_id": row.batch_id, "doc_type": row.doc_type,
            "lang": row.lang, "version_no": row.version_no, "status": row.status, "content_hash": row.content_hash,
            "supersedes_id": row.supersedes_id, "created_at": row.created_at, "created_by": row.created_by,
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
    from ..services.plm.render import MissingRenderDependency, PdfaError

    payload = body.model_dump(exclude={"doc_type", "lang", "pdfa"})
    payload["properties"] = body.properties.model_dump() if body.properties else None
    with _session() as session:
        try:
            row = docs.generate_document(session, revision_id, body.doc_type, lang=body.lang, payload=payload,
                                         created_by=user.id if user else None, pdfa=body.pdfa)
        except PdfaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except MissingRenderDependency as exc:
            # 缺渲染依赖（reportlab / python-docx 未安装）是 503，不是 500。
            raise HTTPException(status_code=503, detail=str(exc)) from None
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
def download_document(document_id: str, fmt: Literal["pdf", "docx", "png"],
                      dpi: int = Query(300, ge=72, le=600)):
    """Download a document file. ``png`` is label-only: the stored label PDF rasterised on demand."""
    from fastapi.responses import Response

    from ..db.models_plm import RegulatoryDocumentRow
    from ..services.plm import storage

    with _session() as session:
        row = session.get(RegulatoryDocumentRow, document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        if fmt == "png":
            if row.doc_type != "label":
                raise HTTPException(status_code=422, detail="PNG 导出仅适用于标签文档")
            if not row.rendered_pdf_key or not storage.exists(row.rendered_pdf_key):
                raise HTTPException(status_code=404, detail="该标签没有 PDF 文件")
            from ..services.plm.render.label_png import LabelPngError, render_label_png

            try:
                data = render_label_png(storage.get(row.rendered_pdf_key), dpi=dpi)
            except LabelPngError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            name = f"{row.doc_type}-{row.lang}-v{row.version_no}.png"
            return Response(data, media_type="image/png",
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})
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


@router.get("/templates")
def list_templates(doc_type: str | None = None, lang: str | None = None) -> list[dict[str, Any]]:
    """List uploaded Word templates (Phase 3B, 3-13: template admin needs a list, not just upload)."""
    from sqlalchemy import select

    from ..db.models_plm import DocTemplateRow

    with _session() as session:
        q = select(DocTemplateRow).where(DocTemplateRow.engine == "docx")
        if doc_type:
            q = q.where(DocTemplateRow.doc_type == doc_type)
        if lang:
            q = q.where(DocTemplateRow.lang == lang)
        return [{"id": r.id, "doc_type": r.doc_type, "lang": r.lang, "name": r.name, "version": r.version,
                 "is_default": r.is_default, "created_at": r.created_at}
                for r in session.execute(q.order_by(DocTemplateRow.created_at.desc())).scalars()]


@router.post("/templates/preview")
async def preview_template(
    file: UploadFile | None = File(None),
    template_id: str | None = Form(None),
    doc_type: Literal["sds", "tds", "coa"] = Form(...),
    lang: Literal["zh-CN", "en-US"] = Form("zh-CN"),
    document_id: str | None = Form(None),
):
    """Preview a Word template (Phase 3B, 3-13): render sample content — or a real
    document's content when ``document_id`` is given — with the uploaded template
    (or a stored one via ``template_id``) and return the DOCX. Nothing is saved."""
    from fastapi.responses import Response

    from ..db.models_plm import DocTemplateRow, RegulatoryDocumentRow
    from ..services.plm import storage
    from ..services.plm.render import MissingRenderDependency
    from ..services.plm.render.docx import render_docx

    if file is None and template_id is None:
        raise HTTPException(status_code=422, detail="需上传模板文件或提供 template_id")
    with _session() as session:
        if file is not None:
            data = await file.read()
            if not data.startswith(b"PK"):
                raise HTTPException(status_code=422, detail="模板必须是 .docx 文件")
        else:
            row = session.get(DocTemplateRow, template_id)
            if row is None or row.engine != "docx":
                raise HTTPException(status_code=404, detail="模板不存在")
            data = storage.get(row.content_key)
        if document_id:
            doc = session.get(RegulatoryDocumentRow, document_id)
            if doc is None:
                raise HTTPException(status_code=404, detail="文档不存在")
            content = doc.content
        else:
            # Minimal sample content shaped like real builder output, so a template
            # can be previewed before any document exists.
            content = {"title": f"{doc_type.upper()} 模板预览", "lang": lang,
                       "header": {"product_name": "示例产品", "product_code": "SAMPLE-001",
                                  "version": "v1", "revision_date": "2026-10-18", "standard": "GB 15258-2009"},
                       "sections": [{"no": 1, "title": "示例章节",
                                     "fields": [{"label": "产品名称", "key": "product_name", "value": "示例产品", "source": "manual"}],
                                     "tables": [], "paragraphs": ["这是一段示例正文，用于预览模板排版。"]}]}
    try:
        out = render_docx(content, data)
    except MissingRenderDependency as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - template tag errors surface here
        raise HTTPException(status_code=422, detail=f"模板渲染失败：{exc}") from None
    return Response(out, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="template-preview-{doc_type}-{lang}.docx"'})


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


@router.get("/revisions/{revision_id}/property-candidates")
def property_candidates(revision_id: str) -> dict[str, Any]:
    """SDS section 9 values from the winning experiment's reviewed measurements (pass them as ``sds_properties``)."""
    from ..services.lab.candidates import sds_property_candidates

    with _session() as session:
        try:
            return sds_property_candidates(session, revision_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="产品修订版不存在") from None


# --------------------------------------------------------------------------- F4: lists and label layouts for the web UI


@router.get("/products")
def list_products(q: str | None = None, limit: int = Query(200, ge=1, le=1000)) -> list[dict[str, Any]]:
    from sqlalchemy import func, or_, select

    from ..db.models_plm import ProductRevisionRow, ProductRow

    with _session() as session:
        stmt = select(ProductRow)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(ProductRow.code.ilike(like), ProductRow.name_zh.like(like), ProductRow.name_en.ilike(like)))
        rows = session.execute(stmt.order_by(ProductRow.updated_at.desc()).limit(limit)).scalars().all()
        counts = dict(session.execute(select(ProductRevisionRow.product_id, func.count()).where(
            ProductRevisionRow.product_id.in_([r.id for r in rows])).group_by(ProductRevisionRow.product_id)).all()) if rows else {}
        return [{"id": r.id, "code": r.code, "name_zh": r.name_zh, "name_en": r.name_en, "project_id": r.project_id,
                 "lifecycle_state": r.lifecycle_state, "revisions": counts.get(r.id, 0), "updated_at": r.updated_at}
                for r in rows]


@router.get("/revisions/{revision_id}")
def get_revision(revision_id: str) -> dict[str, Any]:
    from ..db.models_plm import ProductRevisionRow, ProductRow

    with _session() as session:
        rev = session.get(ProductRevisionRow, revision_id)
        if rev is None:
            raise HTTPException(status_code=404, detail="产品修订版不存在")
        p = session.get(ProductRow, rev.product_id)
        return {"id": rev.id, "rev": rev.rev, "state": rev.state, "frozen_at": rev.frozen_at,
                "formulation_version_id": rev.formulation_version_id, "winning_experiment_id": rev.winning_experiment_id,
                "spec_snapshot": rev.spec_snapshot or {}, "created_at": rev.created_at,
                "product": {"id": p.id, "code": p.code, "name_zh": p.name_zh, "name_en": p.name_en,
                            "project_id": p.project_id, "lifecycle_state": p.lifecycle_state}}


class RevisionSpecIn(BaseModel):
    spec_snapshot: dict[str, Any]


@router.put("/revisions/{revision_id}/spec")
def update_revision_spec(revision_id: str, body: RevisionSpecIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    """Edit release specs / acceptance / red lines of a revision that is not yet submitted."""
    from ..db.models_plm import ProductRevisionRow, ProductRow
    from ..services.audit import hash_chain

    with _session() as session:
        rev = session.get(ProductRevisionRow, revision_id)
        if rev is None:
            raise HTTPException(status_code=404, detail="产品修订版不存在")
        user.require("spec.write", session.get(ProductRow, rev.product_id).project_id)
        if rev.state not in ("draft", "candidate"):
            raise HTTPException(status_code=409, detail=f"修订版状态为 {rev.state}，规格已冻结")
        before = rev.spec_snapshot or {}
        rev.spec_snapshot = body.spec_snapshot
        hash_chain.append(session, actor=user.id, action="revision.spec", entity_type="product_revision",
                          entity_id=rev.id, before=before, after=body.spec_snapshot)
        session.commit()
        return {"id": rev.id, "spec_snapshot": rev.spec_snapshot}


@router.get("/packages")
def list_all_packages(status: str | None = None, limit: int = Query(100, ge=1, le=500)) -> list[dict[str, Any]]:
    """Release packages (newest first) with product, revision and the step waiting for a signature."""
    from sqlalchemy import select

    from ..db.models_plm import ApprovalFlowRow, ApprovalStepRow, ProductRevisionRow, ProductRow, ReleasePackageRow

    with _session() as session:
        q = select(ReleasePackageRow)
        if status:
            q = q.where(ReleasePackageRow.status == status)
        out = []
        for pkg in session.execute(q.order_by(ReleasePackageRow.created_at.desc()).limit(limit)).scalars():
            rev = session.get(ProductRevisionRow, pkg.revision_id)
            product = session.get(ProductRow, rev.product_id) if rev else None
            flow = session.execute(select(ApprovalFlowRow).where(
                ApprovalFlowRow.subject_type == "release_package", ApprovalFlowRow.subject_id == pkg.id)
                .order_by(ApprovalFlowRow.created_at.desc()).limit(1)).scalar_one_or_none()
            step = session.execute(select(ApprovalStepRow).where(
                ApprovalStepRow.flow_id == flow.id, ApprovalStepRow.seq == flow.current_step)).scalar_one_or_none() \
                if flow is not None and flow.status == "pending" else None
            out.append({"id": pkg.id, "status": pkg.status, "revision_id": pkg.revision_id,
                        "rev": rev.rev if rev else None, "product_id": product.id if product else None,
                        "product_code": product.code if product else None, "package_hash": pkg.package_hash,
                        "created_at": pkg.created_at, "released_at": pkg.released_at,
                        "current_step": {"seq": step.seq, "role": step.role_code} if step else None})
        return out


class LabelTemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    lang: Literal["zh-CN", "en-US"] = "zh-CN"
    is_default: bool = True
    layout: dict[str, Any]


class LabelPreviewIn(BaseModel):
    layout: dict[str, Any]
    document_id: str | None = None  # a generated label whose content fills the layout
    variables: dict[str, Any] = Field(default_factory=dict)


_SAMPLE_LABEL = {
    "doc_type": "label", "lang": "zh-CN", "title": "化学品安全标签", "meta": {"qr_payload": "FORMUNEX-SAMPLE"},
    "sections": [{"key": "label", "fields": [
        {"key": "product_name", "value": "示例产品 FX-100 环氧底漆"},
        {"key": "ingredients", "value": ["二甲苯", "环氧树脂"]},
        {"key": "pictograms", "value": ["GHS02", "GHS07"]},
        {"key": "signal_word", "value": "警告"},
        {"key": "hazard_statements", "value": [{"code": "H226", "text": "易燃液体和蒸气"},
                                               {"code": "H315", "text": "造成皮肤刺激"}]},
        {"key": "precautionary_statements", "value": [{"code": "P210", "text": "远离热源、火花、明火、热表面。禁止吸烟。"},
                                                      {"code": "P280", "text": "戴防护手套/穿防护服/戴防护眼罩/戴防护面具。"}]},
        {"key": "supplier", "value": "方枢示例化工有限公司 上海市示例路 1 号 021-0000"},
        {"key": "emergency_phone", "value": "021-0001"},
        {"key": "sds_reference", "value": "请参阅化学品安全技术说明书"},
    ]}],
}


@router.get("/label-templates")
def list_label_templates(lang: str | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import select

    from ..db.models_plm import DocTemplateRow

    with _session() as session:
        q = select(DocTemplateRow).where(DocTemplateRow.engine == "label_json")
        if lang:
            q = q.where(DocTemplateRow.lang == lang)
        return [{"id": r.id, "name": r.name, "lang": r.lang, "version": r.version, "is_default": r.is_default,
                 "created_at": r.created_at} for r in session.execute(q.order_by(DocTemplateRow.created_at.desc())).scalars()]


@router.get("/label-templates/{template_id}")
def get_label_template(template_id: str) -> dict[str, Any]:
    from ..db.models_plm import DocTemplateRow
    from ..services.plm.documents import service as docs

    with _session() as session:
        row = session.get(DocTemplateRow, template_id)
        if row is None or row.engine != "label_json":
            raise HTTPException(status_code=404, detail="标签版式不存在")
        _id, layout = docs.label_layout(session, row.lang, row.id)
        return {"id": row.id, "name": row.name, "lang": row.lang, "version": row.version, "is_default": row.is_default,
                "layout": layout}


@router.post("/label-templates", status_code=201)
def save_label_template(body: LabelTemplateIn, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    from ..services.plm.documents import service as docs
    from ..services.plm.render.label_layout import LayoutError

    if not (user.principal.can("plm.document.write") or user.principal.can("admin.templates")):
        raise HTTPException(status_code=403, detail="缺少权限 plm.document.write")
    with _session() as session:
        try:
            row = docs.save_label_template(session, name=body.name, lang=body.lang, layout=body.layout,
                                           is_default=body.is_default, created_by=user.id)
        except LayoutError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        session.commit()
        return {"id": row.id, "name": row.name, "version": row.version, "is_default": row.is_default}


@router.post("/label-templates/preview")
def preview_label_layout(body: LabelPreviewIn):
    """Render a layout with a real label's content (or sample content) to PDF, without saving anything."""
    from fastapi.responses import Response

    from ..db.models_plm import RegulatoryDocumentRow
    from ..services.plm.render import MissingRenderDependency
    from ..services.plm.render.label_layout import LayoutError, render_layout_pdf

    content = _SAMPLE_LABEL
    if body.document_id:
        with _session() as session:
            doc = session.get(RegulatoryDocumentRow, body.document_id)
            if doc is None or doc.doc_type != "label":
                raise HTTPException(status_code=404, detail="标签文档不存在")
            content = doc.content
    try:
        pdf, info = render_layout_pdf(content, body.layout, body.variables)
    except MissingRenderDependency as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except LayoutError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return Response(pdf, media_type="application/pdf", headers={
        "X-Label-Overflow": ",".join(info["overflow_elements"]),
        "X-Label-Missing": ",".join(info["missing_elements"])})


class LabelBatchItem(BaseModel):
    content: dict[str, Any]  # label content, same shape as a generated label document's content
    filename: str | None = None  # default: label-{index}.pdf


class LabelBatchIn(BaseModel):
    labels: list[LabelBatchItem] = Field(min_length=1, max_length=50)
    color_space: Literal["rgb", "cmyk"] = "rgb"
    pdfa: bool = False


@router.post("/labels/batch")
def batch_labels(body: LabelBatchIn):
    """Batch-generate labels (Phase 3B, 3-14): render N label contents to one ZIP of PDFs.

    Pure render — nothing is saved, no document versions are created. ``color_space="cmyk"``
    draws text / borders / QR with DeviceCMYK inks (SVG pictograms keep their source colours);
    ``pdfa=True`` post-processes each PDF to PDF/A-2b.
    """
    import zipfile
    from io import BytesIO

    from fastapi.responses import Response

    from ..services.plm.render import MissingRenderDependency, PdfaError
    from ..services.plm.render.label_pdf import render_label_pdf

    buf = BytesIO()
    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, item in enumerate(body.labels):
                pdf, _info = render_label_pdf(item.content, pdfa=body.pdfa, color_space=body.color_space)
                name = (item.filename or f"label-{i + 1}.pdf").replace("/", "_")
                if not name.endswith(".pdf"):
                    name += ".pdf"
                zf.writestr(name, pdf)
    except MissingRenderDependency as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except (PdfaError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="labels.zip"'})
