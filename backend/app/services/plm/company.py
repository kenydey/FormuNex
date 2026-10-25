"""Company configuration (整合方案 §5): the legal entity behind documents.

SDS §1 supplier identification, TDS supplier and label supplier all read
the *default* company at generation time. A generated document keeps the
company it was built with — its content is immutable, so a later company
change never rewrites an issued document.

Companies are never deleted (issued documents must always resolve to a real
record); supersede by creating a new default. When no company row exists,
callers fall back to the legacy ``FORMUNEX_PLM_COMPANY_*`` env settings.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db.models_plm import CompanyRow
from . import storage
from .ghs.regulations import validate_regulations

MAX_LOGO_BYTES = 2 * 1024 * 1024
_LOGO_SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": ("png", "image/png"),
    b"\xff\xd8\xff": ("jpg", "image/jpeg"),
    b"GIF8": ("gif", "image/gif"),
}


def _logo_ext(data: bytes) -> tuple[str, str] | None:
    for sig, (ext, media) in _LOGO_SIGNATURES.items():
        if data.startswith(sig):
            return ext, media
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    stripped = data.strip()
    if stripped.startswith(b"<?xml") or stripped.startswith(b"<svg"):
        if b"<svg" in stripped[:4096]:
            return "svg", "image/svg+xml"
    return None


def validate_logo(data: bytes) -> tuple[str, str]:
    """Sniff the logo bytes (extension is never trusted); returns (ext, media_type)."""
    if not data:
        raise ValueError("Logo 文件为空")
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError(f"Logo 文件超过 {MAX_LOGO_BYTES // 1024 // 1024}MB 上限")
    detected = _logo_ext(data)
    if detected is None:
        raise ValueError("Logo 必须是 PNG / JPEG / GIF / WebP / SVG 图片")
    return detected


def company_dict(row: CompanyRow) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "name_en": row.name_en, "address": row.address,
            "address_en": row.address_en, "phone": row.phone, "fax": row.fax, "email": row.email,
            "emergency_phone": row.emergency_phone, "unified_credit_code": row.unified_credit_code,
            "logo_key": row.logo_key, "has_logo": bool(row.logo_key), "is_default": row.is_default,
            # G2: [] = no default (generation must select explicitly); never None.
            "applicable_regulations": list(row.applicable_regulations or []),
            "created_at": row.created_at, "updated_at": row.updated_at}


def _env_company() -> dict[str, Any]:
    """Legacy fallback: the FORMUNEX_PLM_COMPANY_* env settings."""
    s = get_settings()
    return {"name": s.plm_company_name, "address": s.plm_company_address, "phone": s.plm_company_phone,
            "email": s.plm_company_email, "emergency_phone": s.plm_emergency_phone,
            "name_en": "", "address_en": "", "fax": "", "unified_credit_code": "", "logo_key": None}


def default_company(session: Session) -> dict[str, Any] | None:
    """The default company row as a plain dict, or None when no company is configured."""
    row = session.execute(select(CompanyRow).where(CompanyRow.is_default.is_(True))
                          .order_by(CompanyRow.updated_at.desc()).limit(1)).scalar_one_or_none()
    return company_dict(row) if row is not None else None


def resolve_company(session: Session, override: dict | None) -> dict[str, Any]:
    """Company for a document: explicit override wins, else the default company row, else env settings."""
    base = default_company(session) or _env_company()
    base.update({k: v for k, v in (override or {}).items() if v not in (None, "")})
    return base


def create_company(session: Session, data: dict[str, Any], *, created_by: str | None = None) -> CompanyRow:
    name = str(data.get("name") or "").strip()
    emergency = str(data.get("emergency_phone") or "").strip()
    if not name:
        raise ValueError("公司名称不能为空")
    if not emergency:
        raise ValueError("24h 应急电话不能为空（SDS 强制非空）")
    try:
        regulations = validate_regulations(data.get("applicable_regulations") or [])
    except ValueError as exc:
        raise ValueError(str(exc)) from None
    row = CompanyRow(id=str(uuid.uuid4()), name=name, name_en=str(data.get("name_en") or ""),
                     address=str(data.get("address") or ""), address_en=str(data.get("address_en") or ""),
                     phone=str(data.get("phone") or ""), fax=str(data.get("fax") or ""),
                     email=str(data.get("email") or ""), emergency_phone=emergency,
                     unified_credit_code=str(data.get("unified_credit_code") or ""), created_by=created_by,
                     applicable_regulations=regulations)
    session.add(row)
    session.flush()
    if data.get("is_default") or default_company(session) is None:
        set_default(session, row.id)
    return row


def update_company(session: Session, company_id: str, data: dict[str, Any]) -> CompanyRow:
    row = session.get(CompanyRow, company_id)
    if row is None:
        raise ValueError("公司不存在")
    for field in ("name", "name_en", "address", "address_en", "phone", "fax", "email",
                  "emergency_phone", "unified_credit_code"):
        if field in data and data[field] is not None:
            setattr(row, field, str(data[field]))
    if "applicable_regulations" in data and data["applicable_regulations"] is not None:
        try:
            row.applicable_regulations = validate_regulations(data["applicable_regulations"])
        except ValueError as exc:
            raise ValueError(str(exc)) from None
    if not row.name.strip():
        raise ValueError("公司名称不能为空")
    if not row.emergency_phone.strip():
        raise ValueError("24h 应急电话不能为空（SDS 强制非空）")
    if data.get("is_default"):
        set_default(session, row.id)
    session.flush()
    return row


def set_default(session: Session, company_id: str) -> CompanyRow:
    row = session.get(CompanyRow, company_id)
    if row is None:
        raise ValueError("公司不存在")
    for old in session.execute(select(CompanyRow).where(CompanyRow.is_default.is_(True))).scalars():
        old.is_default = False
    row.is_default = True
    session.flush()
    return row


def set_logo(session: Session, company_id: str, data: bytes) -> CompanyRow:
    row = session.get(CompanyRow, company_id)
    if row is None:
        raise ValueError("公司不存在")
    ext, _media = validate_logo(data)
    row.logo_key = storage.put(f"companies/{company_id}/logo.{ext}", data)
    session.flush()
    return row
