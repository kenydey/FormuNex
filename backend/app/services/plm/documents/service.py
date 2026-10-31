"""Generate, version and render PLM documents for a product revision.

``generate_document`` assembles the context a builder needs from the database
(product, the revision's formulation version, a fresh per-regulation
classification, GBZ 2.1 limits, ingredient listings, batch QC results), builds
the structured content, renders PDF / Word, stores the files and writes a *new*
``regulatory_documents`` row — an earlier version is never modified, so a
released document stays exactly what was signed.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ....clock import utcnow
from ....db.models_plm import (
    BatchQcResultRow,
    BatchRow,
    ClassificationResultRow,
    DocTemplateRow,
    ProductRevisionRow,
    ProductRow,
    RegulatoryDocumentRow,
    TsTemplateRow,
)
from ....db.models_regulatory import (
    RegulatoryDataRelease,
    SubstanceOELRow,
    SubstanceRow,
)
from ...provenance import link_many_in_session
from .. import storage
from ..diff import diff_documents
from ..ghs import service as ghs_service

log = logging.getLogger(__name__)


def _cleanup_rendered_keys(keys: list[str | None]) -> None:
    """Best-effort delete of rendered files whose row will never exist.

    A key that cannot be deleted is logged (not swallowed silently) so a
    later reclamation sweep can find it by scanning the storage dir for
    files no row references.
    """
    for key in keys:
        if not key:
            continue
        try:
            storage.delete(key)
        except Exception:  # noqa: BLE001 - cleanup must never mask the original error
            log.error("plm document cleanup failed, orphaned render needs reclamation: key=%s", key)
from ..render.docx import render_docx
from ..render.label_pdf import render_label_pdf
from ..render.pdf import render_pdf
from .coa_builder import CoaContext, ResultItem, SpecItem, build_coa
from .common import content_hash, field_sources
from .confidential_ranges import DEFAULT_EDGES
from .label_builder import LabelContext, build_label
from .sds_builder import SdsContext, build_sds
from .tds_builder import TdsContext, build_tds, split_list_field
from .ts_builder import TsContext, build_ts, clean_indicators
from ..company import resolve_company
from ..ghs.regulations import DEFAULT_REGULATION, ruleset_for, standard_for, validate_regulations
from ..ghs.ruleset import DEFAULT_RULESET

DOC_TYPES = ("sds", "tds", "coa", "label", "ts")


class DocumentError(ValueError):
    pass


# --------------------------------------------------------------------------- COA numbering (Phase 3B, 3-15)

DEFAULT_COA_NUMBER_RULE = "COA-{product_code}-{batch_no}-{n}"
_COA_PLACEHOLDERS = ("product_code", "batch_no", "n", "date")


def coa_number_rule() -> str:
    """Configurable COA number rule (env ``FORMUNEX_COA_NUMBER_RULE``).

    Placeholders: ``{product_code}``, ``{batch_no}``, ``{n}`` (per-batch COA
    version), ``{date}`` (YYYY-MM-DD). Default: ``COA-{product_code}-{batch_no}-{n}``.
    """
    return os.environ.get("FORMUNEX_COA_NUMBER_RULE") or DEFAULT_COA_NUMBER_RULE


def generate_coa_number(rule: str, *, product_code: str, batch_no: str, n: int, date: str) -> str:
    values = {"product_code": product_code, "batch_no": batch_no, "n": n, "date": date}
    unknown = [m for m in re.findall(r"\{(\w+)\}", rule) if m not in _COA_PLACEHOLDERS]
    if unknown:
        raise DocumentError(f"COA 编号规则含未知占位符：{', '.join(unknown)}（可用：{', '.join(_COA_PLACEHOLDERS)}）")
    try:
        return rule.format(**values)
    except (KeyError, IndexError, ValueError) as exc:
        raise DocumentError(f"COA 编号规则无法应用：{exc}") from None


# --------------------------------------------------------------------------- products / revisions / batches


def create_product(session: Session, *, code: str = "", name_zh: str, name_en: str = "", project_id: str | None = None,
                   created_by: str | None = None, category_identifier: str | None = None,
                   legacy_code: str = "") -> ProductRow:
    """Create a product, manually coded or auto-coded under the G8 scheme.

    ``category_identifier`` + ``code`` together is ambiguous (422);
    a category without configured default-company rules is 422; without
    either, a manual code is required (previous behaviour). Auto-codes
    retry on unique-constraint races (fresh row per attempt, inside a
    SAVEPOINT, so the retry no longer requires the caller to use a
    fresh session — W1-4). A pre-flush first moves the caller's pending
    work into the outer transaction, so rolling the savepoint back
    cannot discard it. The savepoint is left open on success (see
    ``chunk_store.replace_for_source_in`` for why it is not committed).
    The code belongs to the product: revisions never change it.
    """
    from sqlalchemy.exc import IntegrityError

    from ..coding import default_company_rules, generate_product_code

    code = (code or "").strip()
    category_identifier = (category_identifier or "").strip() or None
    legacy_code = (legacy_code or "").strip()
    if len(legacy_code) > 64:
        raise DocumentError("旧码长度不能超过 64 字符")
    if category_identifier and code:
        raise DocumentError("产品代码与自动编码 Category 二选一，不能同时提供")
    if category_identifier:
        rules = default_company_rules(session)
        if rules is None or not rules.get("categories"):
            raise DocumentError("默认公司未配置产品编码规则，无法自动编码")
        try:
            auto_code = generate_product_code(session, rules, category_identifier)
        except ValueError as exc:
            raise DocumentError(str(exc)) from None
        last_error: Exception | None = None
        # See the docstring: flush the caller's pending work first so the
        # savepoint below contains only this attempt's row.
        session.flush()
        for _ in range(5):
            sp = session.begin_nested()
            row = ProductRow(id=str(uuid.uuid4()), code=auto_code, name_zh=name_zh, name_en=name_en,
                             project_id=project_id, lifecycle_state="rd", created_by=created_by,
                             category_identifier=category_identifier,
                             legacy_code=legacy_code or None)
            session.add(row)
            try:
                session.flush()
                return row
            except IntegrityError as exc:
                # A concurrent creation won this serial; only this attempt
                # is undone, then recompute and retry with a fresh row.
                sp.rollback()
                last_error = exc
                try:
                    auto_code = generate_product_code(session, rules, category_identifier)
                except ValueError as exc2:
                    raise DocumentError(str(exc2)) from None
        raise DocumentError(f"自动编码失败（并发冲突重试耗尽）: {last_error}")
    if not code:
        raise DocumentError("产品代码不能为空（或选择自动编码 Category）")
    if session.execute(select(ProductRow.id).where(ProductRow.code == code)).first():
        raise DocumentError(f"产品代码 {code} 已存在")
    row = ProductRow(id=str(uuid.uuid4()), code=code, name_zh=name_zh, name_en=name_en, project_id=project_id,
                     lifecycle_state="rd", created_by=created_by, legacy_code=legacy_code or None)
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
    # Phase 2D (2-22): product -> revision edge (same transaction, fail-open).
    link_many_in_session(session, [("product", product_id, "product_revision", row.id, "has_revision")],
                         created_by=created_by)
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


def _company(session: Session, override: dict | None) -> dict[str, Any]:
    """Company for a document: explicit override, else the default company row, else env settings."""
    return resolve_company(session, override)


def _product_dict(product: ProductRow, extra: dict | None) -> dict[str, Any]:
    return {"name_zh": product.name_zh, "name_en": product.name_en, "code": product.code, **(extra or {})}


def _classify_revision(session: Session, rev: ProductRevisionRow, properties: dict | None,
                       ruleset: str | None = None) -> tuple[dict, str]:
    if not rev.formulation_version_id:
        raise DocumentError("产品修订版未关联配方版本，无法分类")
    items = ghs_service.formulation_version_items(session, rev.formulation_version_id)
    result = ghs_service.classify_items(session, items, properties, ruleset=ruleset or DEFAULT_RULESET)
    row = ghs_service.persist(session, result, formulation_version_id=rev.formulation_version_id, revision_id=rev.id)
    return ghs_service.to_response(session, result), row.id


def _data_release(session: Session, classification_id: str | None) -> dict[str, Any] | None:
    """Release info for an SDS's classification, for the §16 version line (P1-1).

    Returns None when the classification or its release is unknown — the
    builder then renders no version line rather than inventing one.
    """
    if not classification_id:
        return None
    row = session.get(ClassificationResultRow, classification_id)
    if row is None or not row.data_release_id:
        return None
    release = session.get(RegulatoryDataRelease, row.data_release_id)
    if release is None:
        return {"id": row.data_release_id, "label": None, "sources": {}}
    return {
        "id": release.id,
        "label": release.label,
        "sources": (release.stats or {}).get("sources", {}),
    }


def _oel(session: Session, cas_numbers: list[str]) -> dict[str, dict]:
    """SDS §8 limits: GBZ only. EH40 rows share the table (standard
    EH40/2005) and would otherwise collapse into the same CAS key
    nondeterministically; they are queryable via the OEL endpoint."""
    from ..regulatory.seed import OEL_STANDARD

    if not cas_numbers:
        return {}
    rows = session.execute(
        select(SubstanceRow.cas_no, SubstanceOELRow.mac, SubstanceOELRow.pc_twa, SubstanceOELRow.pc_stel)
        .join(SubstanceOELRow, SubstanceOELRow.substance_id == SubstanceRow.id)
        .where(SubstanceRow.cas_no.in_(cas_numbers), SubstanceOELRow.standard == OEL_STANDARD)
    ).all()
    return {r.cas_no: {"mac": r.mac, "pc_twa": r.pc_twa, "pc_stel": r.pc_stel} for r in rows}


def _tox_eco(session: Session, cas_numbers: list[str]) -> dict[str, dict]:
    """G6 measured test records by CAS (same shape as _oel, for SDS 11/12)."""
    from ..test_records import records_by_cas

    return records_by_cas(session, cas_numbers)


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
        if s.iecsc_listed:
            out.append(f"{name}（{s.cas_no}）：列入《中国现有化学物质名录》")
    return out


def _components(disclosure: list[dict], trade_secrets: dict[str, str] | None) -> list[dict]:
    secrets = trade_secrets or {}
    out = []
    for d in disclosure:
        key = d.get("cas") if d.get("cas") in secrets else d["name"] if d["name"] in secrets else None
        out.append({**d, "trade_secret": bool(key) or d.get("trade_secret", False),
                    "generic_name": secrets.get(key) if key else None})
    return out


def _revision_meta(session: Session, rev: ProductRevisionRow, doc_type: str, lang: str, batch_id: str | None,
                   regulation: str = DEFAULT_REGULATION) -> dict:
    n = _next_version(session, rev.id, doc_type, lang, batch_id, regulation)
    return {"version": f"{rev.rev}.{n}", "date": utcnow().date().isoformat()}


def _next_version(session: Session, revision_id: str, doc_type: str, lang: str, batch_id: str | None,
                  regulation: str = DEFAULT_REGULATION) -> int:
    q = select(func.max(RegulatoryDocumentRow.version_no)).where(
        RegulatoryDocumentRow.revision_id == revision_id, RegulatoryDocumentRow.doc_type == doc_type,
        RegulatoryDocumentRow.lang == lang, RegulatoryDocumentRow.regulation == regulation)
    if batch_id:
        q = q.where(RegulatoryDocumentRow.batch_id == batch_id)
    return (session.execute(q).scalar() or 0) + 1


def _ts_factory_specs(session: Session, revision_id: str, lang: str) -> list[dict]:
    """COA spec fallback (整合方案 §3.4): the latest TS's factory-inspection indicators.

    Order: same-lang TS first, else any lang. Only indicators marked
    factory-inspection, with at least one limit, become COA specs.
    """
    ts = session.execute(
        select(RegulatoryDocumentRow).where(RegulatoryDocumentRow.revision_id == revision_id,
                                            RegulatoryDocumentRow.doc_type == "ts")
        .order_by(RegulatoryDocumentRow.lang != lang, RegulatoryDocumentRow.version_no.desc()).limit(1)
    ).scalar_one_or_none()
    if ts is None:
        return []
    return [{"metric": i["key"], "label": i.get("label") or i["key"], "unit": i.get("unit") or "",
             "method": i.get("test_method") or "", "min": i.get("spec_min"), "max": i.get("spec_max"),
             "text": None, "comparison": "full"}
            for i in (ts.content.get("meta") or {}).get("indicators") or []
            if i.get("is_factory_inspection")
            and (i.get("spec_min") is not None or i.get("spec_max") is not None or i.get("spec_target") is not None)]


def _ts_indicators(session: Session, rev: ProductRevisionRow, ts_payload: dict) -> tuple[list[dict], list[str]]:
    """TS indicator rows: explicit payload > TS template structure > revision release_specs.

    Template rows carry structure only (empty limits); release_specs are
    contract-defined limits. Nothing is fabricated: no payload / template /
    spec source means no indicators (the TS reports them missing).
    """
    if ts_payload.get("indicators"):
        return ts_payload["indicators"], []
    template_id = ts_payload.get("template_id")
    if template_id:
        row = session.get(TsTemplateRow, template_id)
        if row is None:
            raise DocumentError("TS 模板不存在")
        seeded = [{**i, "source": "template"} for i in (row.indicators_json or [])]
        return seeded, []
    specs = (rev.spec_snapshot or {}).get("release_specs") or []
    return [{"key": s.get("metric"), "label": s.get("label") or s.get("metric"), "unit": s.get("unit") or "",
             "spec_min": s.get("min"), "spec_max": s.get("max"), "spec_target": s.get("target"),
             "test_method": s.get("test_method_standard") or s.get("method") or "",
             "is_factory_inspection": True, "source": "contract"} for s in specs if s.get("metric")], []


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


def build_content(session: Session, rev: ProductRevisionRow, doc_type: str, lang: str, payload: dict,
                  regulation: str = DEFAULT_REGULATION) -> tuple[dict, dict]:
    """Return (content, extra row fields). ``regulation`` applies to SDS /
    label only (their classification); other doc types ignore it."""
    product = session.get(ProductRow, rev.product_id)
    prod = _product_dict(product, payload.get("product"))
    company = _company(session, payload.get("company"))
    extra: dict[str, Any] = {}
    if doc_type in ("sds", "label"):
        try:
            ruleset = ruleset_for(regulation)
        except KeyError:
            raise DocumentError(f"未知适用法规：{regulation}") from None
        cls, classification_id = _classify_revision(session, rev, payload.get("properties"), ruleset=ruleset)
        extra["classification_id"] = classification_id
        extra["classification_properties"] = payload.get("properties") or {}
        cas = [d["cas"] for d in cls["disclosure"] if d.get("cas")]
        if doc_type == "sds":
            props = {k: ({"value": v} if not isinstance(v, dict) else v)
                     for k, v in (payload.get("sds_properties") or {}).items()}
            # Values used for classification are SDS section 9 values too.
            for k, v in (payload.get("properties") or {}).items():
                if k in ("flash_point_c", "initial_boiling_point_c", "ph", "kinematic_viscosity_40c") and v is not None:
                    props.setdefault(k, {"value": v, "source": (payload.get("properties") or {}).get("sources", {}).get(k, "manual")})
            tox_eco = _tox_eco(session, cas)
            content = build_sds(SdsContext(
                product=prod, company=company, classification=cls, statements=cls["statements"], properties=props,
                data_release=_data_release(session, classification_id),
                components=_components(cls["disclosure"], payload.get("trade_secrets")), oel=_oel(session, cas),
                tox_records=tox_eco["tox"], eco_records=tox_eco["eco"], eco_profiles=tox_eco["profiles"],
                # Ingredient listings are China-jurisdiction data (hazard
                # catalogue / IECSC); other regulations render their own
                # core legislation lines only (per-jurisdiction listings
                # are follow-up work, not silently copied from CN).
                regulations=_listings(session, cas) if regulation == "CN" else [],
                regulation=regulation, regulation_standard=standard_for(regulation),
                revision={**_revision_meta(session, rev, doc_type, lang, None, regulation),
                          "change_summary": payload.get("change_summary")},
                band_edges=tuple(payload.get("band_edges") or DEFAULT_EDGES), lang=lang))
        else:
            label = payload.get("label") or {}
            layout_id, layout = label_layout(session, lang, label.get("template_id"))
            if layout is not None:
                label = {**label, "size_mm": layout["size_mm"]}  # the designed size is the printed size
            content = build_label(LabelContext(
                product=prod, company=company, classification=cls, statements=cls["statements"],
                hazardous_ingredients=label.get("hazardous_ingredients") or [
                    d["name"] for d in cls["disclosure"] if not d.get("trade_secret")][:4],
                container_volume_l=label.get("container_volume_l"),
                size_mm=tuple(label.get("size_mm") or (100, 150)), qr_payload=label.get("qr_payload"), lang=lang))
            content["meta"]["layout_template_id"] = layout_id
            content["meta"]["variables"] = {k: v for k, v in (label.get("variables") or {}).items() if v not in (None, "")}
    elif doc_type == "tds":
        tds = payload.get("tds") or {}
        # P1-22: normalise here so a bad type is a 422 (DocumentError), not a
        # 500 from inside the builder; build_tds splits strings itself too,
        # so direct builder callers get the same behaviour.
        tds_product = {**prod, **(tds.get("product") or {})}
        try:
            for _field in ("features", "applications"):
                if tds_product.get(_field) is not None:
                    tds_product[_field] = split_list_field(tds_product[_field], _field)
        except ValueError as exc:
            raise DocumentError(str(exc)) from None
        content = build_tds(TdsContext(
            product=tds_product, company=company, properties=tds.get("properties") or [],
            performance=tds.get("performance") or [], application=tds.get("application") or {},
            storage=tds.get("storage") or {}, revision=_revision_meta(session, rev, doc_type, lang, None), lang=lang))
    elif doc_type == "ts":
        ts = payload.get("ts") or {}
        raw_indicators, _seed_warnings = _ts_indicators(session, rev, ts)
        indicators, warnings = clean_indicators(raw_indicators)
        content = build_ts(TsContext(
            product={**prod, "category": ts.get("category") or (payload.get("product") or {}).get("category")},
            company=company, indicators=indicators,
            properties=ts.get("properties") or [], inspection=ts.get("inspection") or {},
            packaging=ts.get("packaging") or {},
            revision={**_revision_meta(session, rev, doc_type, lang, None),
                      "change_summary": payload.get("change_summary")}, lang=lang))
        for w in warnings:
            content["validation"]["warnings"].append(w)
        content["meta"]["ts_template_id"] = ts.get("template_id")
    elif doc_type == "coa":
        coa = payload.get("coa") or {}
        batch = session.get(BatchRow, coa.get("batch_id") or "")
        if batch is None or batch.product_id != rev.product_id:
            raise DocumentError("COA 需要本产品的批次（coa.batch_id）")
        # 整合方案 §3.4: COA specs = explicit > revision release_specs > latest TS factory indicators.
        specs_raw = coa.get("specs") or (rev.spec_snapshot or {}).get("release_specs") or []
        if not specs_raw:
            specs_raw = _ts_factory_specs(session, rev.id, lang)
        if not specs_raw:
            raise DocumentError("COA 需要出厂规格（coa.specs、修订版 spec_snapshot.release_specs 或 TS 出厂检验指标）")
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
            coa_no = coa.get("coa_no") or generate_coa_number(
                coa_number_rule(), product_code=product.code, batch_no=batch.batch_no, n=n,
                date=(batch.produced_at.date().isoformat() if batch.produced_at else utcnow().date().isoformat())),
            lang=lang))
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
                      payload: dict | None = None, created_by: str | None = None,
                      pdfa: bool = False, regulation: str = DEFAULT_REGULATION) -> RegulatoryDocumentRow:
    if doc_type not in DOC_TYPES:
        raise DocumentError(f"未知文档类型 {doc_type}")
    if lang not in ("zh-CN", "en-US"):
        raise DocumentError("lang 仅支持 zh-CN / en-US")
    if doc_type not in ("sds", "label") and regulation != DEFAULT_REGULATION:
        raise DocumentError("适用法规仅适用于 SDS / 标签")
    if doc_type in ("sds", "label"):
        try:
            ruleset_for(regulation)
        except KeyError:
            raise DocumentError(f"未知适用法规：{regulation}") from None
    rev = session.get(ProductRevisionRow, revision_id)
    if rev is None:
        raise DocumentError("产品修订版不存在")
    # W1-8: an archived product's released documents must not be served
    # as live documents by generating new versions against them.
    product = session.get(ProductRow, rev.product_id)
    if product is not None and product.lifecycle_state == "withdrawn":
        raise DocumentError("产品已归档，不能生成文档")
    if rev.frozen_at is not None and doc_type != "coa":
        raise DocumentError("修订版已冻结（已发布），只能生成新的修订版文档")
    if rev.state == "under_review" and doc_type != "coa":
        raise DocumentError("修订版正在签批，签批结束（发布或驳回）前不能重新生成文档")
    payload = payload or {}
    content, extra = build_content(session, rev, doc_type, lang, payload, regulation=regulation)
    content["meta"]["regulation"] = regulation
    batch_id = extra.get("batch_id")
    prev = session.execute(
        select(RegulatoryDocumentRow).where(
            RegulatoryDocumentRow.revision_id == rev.id, RegulatoryDocumentRow.doc_type == doc_type,
            RegulatoryDocumentRow.lang == lang, RegulatoryDocumentRow.regulation == regulation,
            *( [RegulatoryDocumentRow.batch_id == batch_id] if batch_id else [] ))
        .order_by(RegulatoryDocumentRow.version_no.desc()).limit(1)
    ).scalar_one_or_none()
    version_no = (prev.version_no if prev else 0) + 1
    doc_id = str(uuid.uuid4())
    if extra.get("classification_id"):
        content["meta"]["classification_id"] = extra["classification_id"]
        # The release gate recomputes the classification from these to prove it is still current.
        content["meta"]["classification_properties"] = extra["classification_properties"]
    if prev is not None:
        content["meta"]["diff_from_previous"] = {k: v for k, v in diff_documents(prev.content, content).items()
                                                 if k in ("major", "count")}

    # File stem carries the regulation for non-CN versions (CN keeps the
    # historical name, so existing downloads / links do not change).
    stem = f"{doc_type}-{lang}-v{version_no}" if regulation == "CN" \
        else f"{doc_type}-{lang}-{regulation}-v{version_no}"
    # W1-4: renders are written to storage BEFORE the row that references
    # them exists, so any failure after a put used to leave orphaned
    # files. Track every key written here; on failure delete them
    # (best-effort) and log what could not be deleted for reclamation,
    # then re-raise the original error unchanged.
    written_keys: list[str] = []
    try:
        if doc_type == "label":
            layout_id = content["meta"].get("layout_template_id")
            _id, layout = label_layout(session, lang, layout_id) if layout_id else (None, None)
            if layout is not None:
                from ..render.label_layout import render_layout_pdf

                pdf, info = render_layout_pdf(content, layout, content["meta"].get("variables"), pdfa=pdfa)
                # The layout-element check is GB 15258-specific: only warn for CN labels.
                if regulation == "CN" and info["missing_elements"]:
                    content["validation"]["warnings"].append(
                        "标签版式缺少 GB 15258 要素：" + "、".join(info["missing_elements"]))
            else:
                pdf, info = render_label_pdf(content, pdfa=pdfa)
            if info.get("overflow"):
                content["validation"]["warnings"].append("标签内容超出版面（文字被截断），请调整版式或增大标签尺寸")
            docx_key = None
        else:
            pdf, info = render_pdf(content, pdfa=pdfa)
            template_id, template = _template(session, doc_type, lang)
            docx_key = storage.put(f"documents/{doc_id}/{stem}.docx",
                                   render_docx(content, template))
            written_keys.append(docx_key)
            extra["template_id"] = template_id
        content["meta"]["render"] = info
        if not info.get("font_embedded"):
            content["validation"]["warnings"].append("PDF 未嵌入中文字体（未找到可嵌入的 TrueType 字体），发布前须配置 plm_doc_font_path")
        if info.get("pdfa") and not info.get("pdfa_output_intent"):
            content["validation"]["warnings"].append(
                "PDF/A-2b 输出缺少 OutputIntent（未获得 sRGB ICC 配置文件）：仅为 PDF/A-2b-ready，"
                "配置 FORMUNEX_PDFA_ICC_PATH 或安装 liblcms2 后才是完整 PDF/A-2b")
        pdf_key = storage.put(f"documents/{doc_id}/{stem}.pdf", pdf)
        written_keys.append(pdf_key)

        row = RegulatoryDocumentRow(
            id=doc_id, revision_id=rev.id, batch_id=batch_id, doc_type=doc_type, lang=lang,
            regulation=regulation, version_no=version_no,
            status="draft", template_id=extra.get("template_id"), content=content, field_sources=field_sources(content),
            rendered_pdf_key=pdf_key, rendered_docx_key=docx_key, content_hash=content_hash(content),
            supersedes_id=prev.id if prev else None, created_by=created_by,
        )
        session.add(row)
        session.flush()
    except Exception:
        _cleanup_rendered_keys(written_keys)
        raise
    # Phase 2D (2-22): revision -> document edge (same transaction, fail-open).
    link_many_in_session(session, [("product_revision", rev.id, "document", row.id, "documented_as")],
                         created_by=created_by)
    return row


def generate_documents(session: Session, revision_id: str, doc_type: str, *, lang: str = "zh-CN",
                       payload: dict | None = None, created_by: str | None = None,
                       pdfa: bool = False, regulations: list[str] | None = None) -> list[RegulatoryDocumentRow]:
    """G2: one document version per selected regulation (SDS / label only).

    Each version classifies with its own regulation's rule set and stores
    its own classification row, so versions never overwrite each other.
    An explicitly empty selection is a 422 (the caller must select); the
    company-default resolution lives in the API route, not here.
    """
    if doc_type not in ("sds", "label"):
        raise DocumentError("多法规生成仅适用于 SDS / 标签")
    try:
        regs = validate_regulations(regulations if regulations is not None else [])
    except ValueError as exc:
        raise DocumentError(str(exc)) from None
    if not regs:
        raise DocumentError("请至少选择一个适用法规")
    # W1-4: one loop, not a comprehension — if a later regulation's
    # document fails, the earlier ones' rows roll back with the session
    # but their rendered files would not, so clean those up here too
    # (the failed document cleans its own files inside generate_document).
    rows: list[RegulatoryDocumentRow] = []
    try:
        for r in regs:
            rows.append(generate_document(session, revision_id, doc_type, lang=lang, payload=payload,
                                          created_by=created_by, pdfa=pdfa, regulation=r))
    except Exception:
        _cleanup_rendered_keys([k for row in rows for k in (row.rendered_pdf_key, row.rendered_docx_key)])
        raise
    return rows


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


# --------------------------------------------------------------------------- label layouts (label designer)


def save_label_template(session: Session, *, name: str, lang: str, layout: dict, is_default: bool = True,
                        created_by: str | None = None) -> DocTemplateRow:
    import json

    from ..render.label_layout import validate_layout

    clean = validate_layout(layout)
    version = (session.execute(select(func.max(DocTemplateRow.version)).where(
        DocTemplateRow.doc_type == "label", DocTemplateRow.lang == lang, DocTemplateRow.name == name)).scalar() or 0) + 1
    tid = str(uuid.uuid4())
    key = storage.put(f"templates/label/{lang}/{tid}.json", json.dumps(clean, ensure_ascii=False).encode("utf-8"))
    if is_default:
        for old in session.execute(select(DocTemplateRow).where(
                DocTemplateRow.doc_type == "label", DocTemplateRow.lang == lang, DocTemplateRow.engine == "label_json",
                DocTemplateRow.is_default.is_(True))).scalars():
            old.is_default = False
    row = DocTemplateRow(id=tid, doc_type="label", lang=lang, name=name, version=version, engine="label_json",
                         content_key=key, is_default=is_default, created_by=created_by)
    session.add(row)
    session.flush()
    return row


def label_layout(session: Session, lang: str, template_id: str | None = None) -> tuple[str | None, dict | None]:
    """The requested label layout, else the default one for *lang*; ``(None, None)`` = built-in layout."""
    import json

    if template_id:
        row = session.get(DocTemplateRow, template_id)
        if row is None or row.engine != "label_json":
            raise DocumentError("标签版式不存在")
    else:
        row = session.execute(select(DocTemplateRow).where(
            DocTemplateRow.doc_type == "label", DocTemplateRow.lang == lang, DocTemplateRow.engine == "label_json",
            DocTemplateRow.is_default.is_(True)).order_by(DocTemplateRow.version.desc()).limit(1)).scalar_one_or_none()
    if row is None or not storage.exists(row.content_key):
        return None, None
    return row.id, json.loads(storage.get(row.content_key))
