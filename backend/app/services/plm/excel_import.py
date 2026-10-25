"""G7 Excel import (narrowed): formulations + companies.

The materials catalog already has ``POST /api/materials/import`` (json/csv/
xlsx + template), so this module covers only the two legs the review found
missing: formulation records and company records, both from .xlsx files.

Conventions follow the MSDS ``import_from_excel`` implementations (Chinese
column headers, space-insensitive header matching, empty rows skipped) and
the FormuNex batch-import report shape (``{created, skipped, errors,
warnings}``). Excel reports use the *spreadsheet* row number (1-based,
header = row 1) on errors, so a reported row can be found in the file
even when empty rows were skipped during parsing.

- Formulation rows are mapped to ``formula_mapping.import_formula`` records,
  so the existing dedup (same company + product + source_ref -> skipped)
  and component validation (names, 0-100 pct, total 100 +/- 0.5) apply
  unchanged. The 成分列表 cell holds a JSON array, as in MSDS.
- Company rows dedup by exact name (existing name -> skipped, never
  overwritten); a missing 24h emergency phone is a row error, because a
  company without one cannot appear on SDS §1. Import never flips
  ``is_default``.
- ``dry_run=True`` validates everything inside a savepoint and rolls it
  back, mirroring ``import_formulas(dry_run=True)``.
"""

from __future__ import annotations

import io
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models_plm import CompanyRow
from .company import create_company
from .formula_mapping import FormulaImportError, import_formula

# (template header, record field). English aliases are accepted on read;
# templates use the Chinese headers.
FORMULA_HEADERS: list[tuple[str, str]] = [
    ("产品代码", "product_code"),
    ("产品名称", "product_name_zh"),
    ("产品英文名称", "product_name_en"),
    ("公司名称", "company_name"),
    ("公司应急电话", "company_emergency_phone"),
    ("版本号", "version"),
    ("来源编号", "source_ref"),
    ("成分列表", "components_json"),
    ("备注", "note"),
]
FORMULA_ALIASES = {
    "code": "product_code", "name": "product_name_zh", "name_en": "product_name_en",
    "company": "company_name", "company_emergency_phone": "company_emergency_phone",
    "version": "version", "source_ref": "source_ref", "components": "components_json",
    "ingredients": "components_json", "note": "note",
}
COMPANY_HEADERS: list[tuple[str, str]] = [
    ("公司名称", "name"),
    ("英文名称", "name_en"),
    ("地址", "address"),
    ("电话", "phone"),
    ("Email", "email"),
    ("24h应急电话", "emergency_phone"),
    ("统一社会信用代码", "unified_credit_code"),
]
COMPANY_ALIASES = {
    "name": "name", "name_en": "name_en", "address": "address", "phone": "phone",
    "email": "email", "emergency_phone": "emergency_phone",
    "unified_credit_code": "unified_credit_code",
}


def _norm(header: Any) -> str:
    return str(header or "").strip().replace(" ", "").lower()


def _field_map(headers: list[tuple[str, str]], aliases: dict[str, str]) -> dict[str, str]:
    m = {_norm(h): f for h, f in headers}
    m.update(aliases)
    return m


def map_rows(rows: list[dict[str, Any]], headers: list[tuple[str, str]],
             aliases: dict[str, str]) -> list[tuple[int, dict[str, Any]]]:
    """Map parsed xlsx rows (raw header -> value) to (spreadsheet row, record).

    Unknown columns are ignored; a row with no mapped value at all is
    dropped (the parser already skips fully-empty rows, this covers rows
    that only carry unknown columns). Row numbers are 1-based with the
    header as row 1, so errors point at the file.
    """
    fmap = _field_map(headers, aliases)
    out: list[tuple[int, dict[str, Any]]] = []
    for i, row in enumerate(rows):
        rec: dict[str, Any] = {}
        for header, value in row.items():
            field = fmap.get(_norm(header))
            if field is None or value is None or field in rec:
                continue  # first matching column wins
            if isinstance(value, str):
                value = value.strip()
                if value == "":
                    continue
            rec[field] = value
        if rec:
            out.append((i + 2, rec))
    return out


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _formula_record(rec: dict[str, Any], row_no: int) -> dict[str, Any]:
    """Map one record to an import_formula record; raise ValueError on bad cells."""
    components_raw = rec.get("components_json")
    components: Any = []
    if isinstance(components_raw, str):
        try:
            components = json.loads(components_raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {row_no} 行成分列表不是合法 JSON：{exc}") from None
    elif isinstance(components_raw, list):
        components = components_raw
    elif components_raw is not None:
        raise ValueError(f"第 {row_no} 行成分列表必须为 JSON 数组")
    version = rec.get("version")
    if version is not None:
        try:
            version = int(float(version)) if isinstance(version, str) else int(version)
        except (TypeError, ValueError):
            raise ValueError(f"第 {row_no} 行版本号不是整数：{version!r}") from None
    company_name = _as_text(rec.get("company_name"))
    company = None
    if company_name:
        company = {"name": company_name,
                   "emergency_phone": _as_text(rec.get("company_emergency_phone"))}
    return {"product_code": _as_text(rec.get("product_code")),
            "product_name_zh": _as_text(rec.get("product_name_zh")),
            "product_name_en": _as_text(rec.get("product_name_en")),
            "company": company, "version": version,
            "source_ref": _as_text(rec.get("source_ref")),
            "components": components, "note": _as_text(rec.get("note"))}


def import_formula_rows(session: Session, mapped: list[tuple[int, dict[str, Any]]], *,
                        created_by: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Import mapped formula rows; errors carry spreadsheet row numbers."""
    report: dict[str, Any] = {"created": [], "skipped": [], "errors": [], "warnings": []}
    savepoint = session.begin_nested() if dry_run else None
    try:
        for row_no, rec in mapped:
            try:
                record = _formula_record(rec, row_no)
                out = import_formula(session, record, created_by=created_by)
            except (FormulaImportError, ValueError) as exc:
                report["errors"].append({"row": row_no, "product_code": _as_text(rec.get("product_code")),
                                         "error": str(exc)})
                continue
            report["warnings"].extend(out.get("warnings") or [])
            report["created" if out["action"] == "created" else "skipped"].append(out)
    finally:
        if savepoint is not None:
            savepoint.rollback()
    report["dry_run"] = dry_run
    return report


def import_company_rows(session: Session, mapped: list[tuple[int, dict[str, Any]]], *,
                        dry_run: bool = False) -> dict[str, Any]:
    """Import mapped company rows; dedup by exact name, never overwrite."""
    report: dict[str, Any] = {"created": [], "skipped": [], "errors": [], "warnings": []}
    savepoint = session.begin_nested() if dry_run else None
    try:
        for row_no, rec in mapped:
            name = _as_text(rec.get("name"))
            if not name:
                report["errors"].append({"row": row_no, "name": "", "error": "公司名称不能为空"})
                continue
            existing = session.execute(
                select(CompanyRow).where(CompanyRow.name == name)).scalar_one_or_none()
            if existing is not None:
                report["skipped"].append({"name": name, "reason": "同名公司已存在"})
                continue
            data = {"name": name, "name_en": _as_text(rec.get("name_en")),
                    "address": _as_text(rec.get("address")), "phone": _as_text(rec.get("phone")),
                    "email": _as_text(rec.get("email")),
                    "emergency_phone": _as_text(rec.get("emergency_phone")),
                    "unified_credit_code": _as_text(rec.get("unified_credit_code"))}
            try:
                row = create_company(session, data)
            except ValueError as exc:
                report["errors"].append({"row": row_no, "name": name, "error": str(exc)})
                continue
            report["created"].append({"name": name, "company_id": row.id})
    finally:
        if savepoint is not None:
            savepoint.rollback()
    report["dry_run"] = dry_run
    return report


def build_template(headers: list[tuple[str, str]]) -> bytes:
    """Header-only .xlsx template (materials precedent: no example rows,
    so a template can never be imported by accident)."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([h for h, _ in headers])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
