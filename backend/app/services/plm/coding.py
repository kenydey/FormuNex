"""G8 generic product coding scheme.

A company defines its coding rules in its configuration (see CompanyRow.coding_rules):
categories with a Chinese name, an English name and an identifier (digits or
letters), plus a total code length in digits. When a product is created with a
category, its code is generated as::

    <identifier><zero-padded serial>

where the serial occupies ``code_digits - len(identifier)`` digits. Example
(Cheng's, confirmed against the MSDS MYH structure where phosphating's first
digit is 4): identifier ``4``, code_digits 3 -> ``401``; code_digits 4 ->
``4001``. The serial is per identifier across all products of the default
company, so changing code_digits later only affects new products: existing
codes are frozen, and the next serial continues from the highest serial ever
used for that identifier (any length), never reusing a serial.

Decisions (Cheng 2026-10-25): the code belongs to the product, so revisions
share it; MYH codes are NOT auto-mapped (a product may carry a manual
``legacy_code`` for reference only); a code_digits change applies to new
products only.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models_plm import CompanyRow, ProductRow

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9]{1,8}$")
_CODE_RE_CACHE: dict[str, re.Pattern[str]] = {}

# A code belongs to at most one product; the DB unique constraint is the
# backstop for concurrent creation racing on the same serial.


def empty_rules() -> dict[str, Any]:
    """No coding rules configured: auto-coding is unavailable."""
    return {"categories": [], "code_digits": None}


def normalize_rules(data: Any) -> dict[str, Any]:
    """Stored rules as the API returns them; never None, always shaped."""
    if not isinstance(data, dict):
        return empty_rules()
    cats = data.get("categories")
    return {"categories": list(cats) if isinstance(cats, list) else [],
            "code_digits": data.get("code_digits")}


def validate_coding_rules(data: Any) -> dict[str, Any]:
    """Validate a company coding-rules payload; raise ValueError on any problem.

    ``{}`` (or None) means "no rules": auto-coding disabled, manual codes only.
    A half configuration (categories without digits, or digits without
    categories) is rejected — the two are set together.
    """
    if data is None or data == {}:
        return empty_rules()
    if not isinstance(data, dict):
        raise ValueError("编码规则必须为对象")
    raw_cats = data.get("categories")
    digits = data.get("code_digits")
    if not isinstance(raw_cats, list):
        raise ValueError("编码规则 categories 必须为数组")
    categories: list[dict[str, str]] = []
    seen: set[str] = set()
    for cat in raw_cats:
        if not isinstance(cat, dict):
            raise ValueError("Category 必须为对象")
        name_zh = str(cat.get("name_zh") or "").strip()
        identifier = str(cat.get("identifier") or "").strip()
        if not name_zh:
            raise ValueError("Category 中文名称不能为空")
        if not _IDENTIFIER_RE.match(identifier):
            raise ValueError(f"Category 识别码 {identifier!r} 必须为 1-8 位数字或字母")
        if identifier in seen:
            raise ValueError(f"Category 识别码 {identifier!r} 重复")
        seen.add(identifier)
        categories.append({"name_zh": name_zh, "name_en": str(cat.get("name_en") or ""),
                           "identifier": identifier})
    if not categories:
        if digits is not None:
            raise ValueError("未定义 Category 时数字编码位数无意义")
        return empty_rules()
    if not isinstance(digits, int) or isinstance(digits, bool):
        raise ValueError("数字编码位数必须为整数")
    if digits < 2 or digits > 16:
        raise ValueError("数字编码位数必须在 2-16 之间")
    longest = max(len(c["identifier"]) for c in categories)
    if digits <= longest:
        raise ValueError(f"数字编码位数 ({digits}) 必须大于最长识别码长度 ({longest})，否则流水号无位可用")
    return {"categories": categories, "code_digits": digits}


def default_company_rules(session: Session) -> dict[str, Any] | None:
    """The default company's coding rules, or None when no default company exists."""
    row = session.execute(select(CompanyRow).where(CompanyRow.is_default.is_(True))
                          .order_by(CompanyRow.updated_at.desc()).limit(1)).scalar_one_or_none()
    return normalize_rules(row.coding_rules) if row is not None else None


def next_code(session: Session, identifier: str, code_digits: int) -> str:
    """Next code for an identifier: highest serial ever used for it + 1.

    Only rows actually auto-coded with this identifier count (manual codes
    are ignored), and all lengths count, so a code_digits change never
    reuses a serial. Raises ValueError when the serial no longer fits.
    """
    pattern = _CODE_RE_CACHE.setdefault(identifier, re.compile(r"^" + re.escape(identifier) + r"(\d+)$"))
    serial_len = code_digits - len(identifier)
    rows = session.execute(select(ProductRow.code).where(
        ProductRow.category_identifier == identifier)).scalars().all()
    serial = 0
    for code in rows:
        m = pattern.match(code)
        if m:
            serial = max(serial, int(m.group(1)))
    nxt = serial + 1
    if len(str(nxt)) > serial_len:
        raise ValueError(f"识别码 {identifier!r} 的编码位数已用完（{code_digits} 位）")
    return identifier + str(nxt).zfill(serial_len)


def generate_product_code(session: Session, rules: dict[str, Any], identifier: str) -> str:
    """Generate a code for a category identifier under the given rules."""
    identifiers = {c["identifier"] for c in rules.get("categories", [])}
    if identifier not in identifiers:
        raise ValueError(f"Category 识别码 {identifier!r} 不在默认公司的编码规则中")
    return next_code(session, identifier, rules["code_digits"])
