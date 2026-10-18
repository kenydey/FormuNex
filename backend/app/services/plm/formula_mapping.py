"""Formula database mapping (整合方案 §6): MSDS-Generator FormulaDatabase → FormuNex.

Concept mapping: an external record (company + product + formula) becomes
a ``FormulationVersion`` (snapshot carries ``source: "msds_generator"``,
ingredients in the standard ``{"ingredients": [{name, pct, cas}]}`` shape)
linked by a ``FormulaMappingRow`` to the company and product. Products and
companies are created when they do not exist yet; existing ones are reused
and never overwritten.

Conflict rule: the same (company, product, source, source_ref) mapping
already existing means the record was imported before — it is *skipped*
and reported, never duplicated or overwritten. A ``ProductRevision`` is
NOT created by import; call :func:`create_revision_from_mapping` to bind a
mapping to a revision on demand (an explicit lifecycle decision).
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models import FormulationVersion
from ...db.models_plm import CompanyRow, FormulaMappingRow, ProductRevisionRow, ProductRow
from .documents import service as docs

SOURCE = "msds_generator"


class FormulaImportError(ValueError):
    pass


def _clean_components(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise FormulaImportError("配方组分必须是列表")
    out = []
    total = 0.0
    for i, c in enumerate(raw):
        if not isinstance(c, dict) or not str(c.get("name") or "").strip():
            raise FormulaImportError(f"第 {i + 1} 个组分缺少名称")
        try:
            pct = float(c.get("pct", c.get("percent", 0)) or 0)
        except (TypeError, ValueError):
            raise FormulaImportError(f"组分 {c.get('name')} 的含量不是数值") from None
        if pct < 0 or pct > 100:
            raise FormulaImportError(f"组分 {c.get('name')} 的含量 {pct} 超出 0~100")
        total += pct
        out.append({"name": str(c["name"]).strip(), "pct": pct, "cas": str(c.get("cas") or "") or None})
    if not out:
        raise FormulaImportError("配方至少需要一个组分")
    if abs(total - 100.0) > 0.5:
        raise FormulaImportError(f"配方组分合计 {total:g}%，与 100% 相差超过 0.5%")
    return out


def _find_or_create_company(session: Session, company: dict | None, *, created_by: str | None,
                            warnings: list[str]) -> CompanyRow | None:
    """Find by name or create. A record without an emergency phone does NOT
    create a company — a placeholder emergency number on documents would be
    worse than no company link."""
    if not company:
        return None
    name = str(company.get("name") or "").strip()
    if not name:
        return None
    row = session.execute(select(CompanyRow).where(CompanyRow.name == name)).scalar_one_or_none()
    if row is not None:
        return row
    emergency = str(company.get("emergency_phone") or "").strip()
    if not emergency:
        warnings.append(f"公司 {name} 缺少 24h 应急电话，未创建公司记录，映射不关联公司")
        return None
    from .company import create_company

    return create_company(session, {"name": name, "name_en": str(company.get("name_en") or ""),
                                    "address": str(company.get("address") or ""),
                                    "phone": str(company.get("phone") or ""),
                                    "email": str(company.get("email") or ""),
                                    "emergency_phone": emergency},
                          created_by=created_by)


def _find_or_create_product(session: Session, product: dict, *, created_by: str | None) -> ProductRow:
    code = str(product.get("product_code") or product.get("code") or "").strip()
    if not code:
        raise FormulaImportError("配方记录缺少产品代码（product_code）")
    row = session.execute(select(ProductRow).where(ProductRow.code == code)).scalar_one_or_none()
    if row is not None:
        return row
    return docs.create_product(session, code=code, name_zh=str(product.get("product_name_zh")
                                                                or product.get("product_name") or code),
                               name_en=str(product.get("product_name_en") or ""), created_by=created_by)


def import_formula(session: Session, record: dict, *, created_by: str | None = None) -> dict[str, Any]:
    """Import one external formula record. Returns {action: created|skipped, ...}."""
    if not isinstance(record, dict):
        raise FormulaImportError("配方记录必须是对象")
    source_ref = str(record.get("source_ref") or record.get("id") or "").strip()
    warnings: list[str] = []
    company = _find_or_create_company(session, record.get("company"), created_by=created_by, warnings=warnings)
    product = _find_or_create_product(session, record, created_by=created_by)
    if source_ref:
        existing = session.execute(
            select(FormulaMappingRow).where(FormulaMappingRow.product_id == product.id,
                                            FormulaMappingRow.source == SOURCE,
                                            FormulaMappingRow.source_ref == source_ref)).scalar_one_or_none()
        if existing is not None:
            return {"action": "skipped", "reason": "同公司同产品同 source_ref 的映射已存在", "mapping_id": existing.id,
                    "product_code": product.code}
    components = _clean_components(record.get("components") or record.get("ingredients"))
    version = record.get("version")
    try:
        version_no = int(version) if version is not None else 1
    except (TypeError, ValueError):
        raise FormulaImportError(f"配方版本号不是整数：{version!r}") from None
    fv = FormulationVersion(id=str(uuid.uuid4()), lineage_id=str(uuid.uuid4()), version=version_no,
                            project_id=None, name=str(record.get("product_name") or product.code)[:255],
                            domain="plm_import",
                            snapshot={"ingredients": components, "source": SOURCE,
                                      "source_ref": source_ref,
                                      "note": str(record.get("note") or "")},
                            change_summary=f"从 {SOURCE} 导入（source_ref={source_ref or '—'}）",
                            created_by=(created_by or "")[:80])
    session.add(fv)
    session.flush()
    mapping = FormulaMappingRow(id=str(uuid.uuid4()), company_id=company.id if company else None,
                                product_id=product.id, formulation_version_id=fv.id, source=SOURCE,
                                source_ref=source_ref, source_payload=record, created_by=created_by)
    session.add(mapping)
    session.flush()
    return {"action": "created", "mapping_id": mapping.id, "product_code": product.code,
            "formulation_version_id": fv.id, "warnings": warnings}


def import_formulas(session: Session, records: list[dict], *, created_by: str | None = None,
                    dry_run: bool = False) -> dict[str, Any]:
    """Import many records. ``dry_run`` validates without persisting (a nested rollback)."""
    if dry_run:
        # Validate everything in a savepoint, then roll it back: the caller
        # commits nothing for a dry run and the DB is untouched.
        savepoint = session.begin_nested()
        try:
            report = _import_all(session, records, created_by=created_by)
        finally:
            savepoint.rollback()
        report["dry_run"] = True
        return report
    return _import_all(session, records, created_by=created_by)


def _import_all(session: Session, records: list[dict], *, created_by: str | None) -> dict[str, Any]:
    report: dict[str, Any] = {"created": [], "skipped": [], "errors": [], "warnings": []}
    for i, record in enumerate(records):
        try:
            out = import_formula(session, record, created_by=created_by)
        except (FormulaImportError, ValueError) as exc:
            report["errors"].append({"index": i, "product_code": (record or {}).get("product_code"),
                                     "error": str(exc)})
            continue
        report["warnings"].extend(out.get("warnings") or [])
        report["created" if out["action"] == "created" else "skipped"].append(out)
    return report


def create_revision_from_mapping(session: Session, mapping_id: str, *, created_by: str | None = None,
                                  spec_snapshot: dict | None = None) -> ProductRevisionRow:
    mapping = session.get(FormulaMappingRow, mapping_id)
    if mapping is None:
        raise FormulaImportError("配方映射不存在")
    if mapping.product_id is None or mapping.formulation_version_id is None:
        raise FormulaImportError("该映射缺少产品或配方版本，无法创建修订版")
    return docs.create_revision(session, mapping.product_id, formulation_version_id=mapping.formulation_version_id,
                                 spec_snapshot=spec_snapshot, created_by=created_by)
