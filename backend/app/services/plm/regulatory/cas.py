"""CAS Registry Number validation, normalisation and repair.

A CAS RN is ``NNNNNNN-NN-R``: 2–7 digits, 2 digits, one check digit. The check
digit is the sum of every other digit weighted by its position counted from the
right (1, 2, 3, ...), modulo 10.

``repair`` undoes a specific, common corruption found in the MSDS-Generator
catalogue: Excel turned CAS numbers that look like dates into timestamps, e.g.
``79-10-7`` (acrylic acid) → ``2079-10-07 00:00:00`` and ``7758-05-6`` →
``7758-05-06``, and sometimes further into the date's serial number
(``72321`` = 2098-01-01 → ``98-01-1``, furfural). The checksum decides which
reading is the real number, so a repair is never a guess.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

_CAS_RE = re.compile(r"^(\d{2,7})-(\d{2})-(\d)$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T]\d{2}:\d{2}(?::\d{2})?)?$")


def check_digit(body: str) -> int:
    """Check digit for the digits of a CAS RN without its last digit."""
    digits = [int(c) for c in body if c.isdigit()]
    return sum(d * w for w, d in enumerate(reversed(digits), start=1)) % 10


def is_valid(cas: str | None) -> bool:
    if not cas:
        return False
    m = _CAS_RE.match(cas.strip())
    if not m:
        return False
    return check_digit(m.group(1) + m.group(2)) == int(m.group(3))


def normalize(raw: str | None) -> str | None:
    """Return a canonical CAS RN (no leading zeros, ASCII hyphens) or None."""
    if not raw:
        return None
    s = re.sub(r"\s+", "", str(raw))
    s = s.replace("－", "-").replace("—", "-").replace("–", "-")
    m = _CAS_RE.match(s)
    if not m:
        return None
    first = str(int(m.group(1)))
    if len(first) < 2:
        return None
    cas = f"{first}-{m.group(2)}-{m.group(3)}"
    return cas if is_valid(cas) else None


def repair(raw: str | None) -> tuple[str | None, str]:
    """Normalise *raw*, undoing Excel date corruption when needed.

    Returns ``(cas, how)`` where *how* is ``"ok"`` (already valid),
    ``"excel_date"`` / ``"excel_serial"`` (repaired), ``"invalid"`` or ``"empty"``.
    """
    if raw is None or not str(raw).strip():
        return None, "empty"
    cas = normalize(raw)
    if cas:
        return cas, "ok"
    text = str(raw).strip()
    if re.fullmatch(r"\d{4,7}", text) and int(text) > 3000:
        # Excel date serial (day 1 = 1900-01-01, with Excel's fictitious 1900-02-29).
        try:
            d = date(1899, 12, 30) + timedelta(days=int(text))
        except OverflowError:
            return None, "invalid"
        text = f"{d.year}-{d.month:02d}-{d.day:02d}"
    m = _DATE_RE.match(text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if day <= 9:
            candidates = [year]
            if 2000 <= year <= 2099:
                candidates.insert(0, year - 2000)  # two-digit first block read as 20xx
            if 1900 <= year <= 1999:
                candidates.insert(0, year - 1900)
            for first in candidates:
                if first < 10:
                    continue
                fixed = normalize(f"{first}-{month:02d}-{day}")
                if fixed:
                    return fixed, "excel_date" if text == str(raw).strip() else "excel_serial"
    return None, "invalid"


def split_many(raw: str | None) -> list[str]:
    """Split a cell holding several CAS numbers.

    Separators are ``;`` ``；`` ``,`` ``，`` ``、``. Line breaks are ambiguous: in
    the source spreadsheets they both wrap a single number (``95-\\n47-6``) and
    separate two. A piece is joined across its line breaks when that yields a
    CAS-shaped token, and split on them otherwise.
    """
    if not raw:
        return []
    out: list[str] = []
    for piece in re.split(r"[;；,，、]+", str(raw)):
        piece = piece.strip()
        if not piece:
            continue
        joined = re.sub(r"\s+", "", piece)
        if "\n" not in piece or _CAS_RE.match(joined) or not all(
            _CAS_RE.match(re.sub(r"\s+", "", x)) for x in piece.splitlines() if x.strip()
        ):
            out.append(joined if "\n" in piece else piece)
        else:
            out.extend(re.sub(r"\s+", "", x) for x in piece.splitlines() if x.strip())
    return out
