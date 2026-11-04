"""Three-library C1: positional header/footer strip (pure span rule).

ChemEAGLE's text-layer rule, decoupled from extraction: it runs on span
dicts (``{text, y0, y1}`` in points, origin top-left, as PyMuPDF reports)
so any caller that can feed ``page.get_text("dict")`` spans can use it.
It deliberately does NOT re-implement reading order or font rules: the
local parser already owns reading order, and font sizes never reach this
pipeline, so those two ChemEAGLE rules are not portable here.

A span inside the header zone (top 9% of the page) or the footer zone
(bottom 7%) is dropped when ANY of these hold:
- its normalised text repeats on at least 2 pages (running furniture);
- it is pure symbols (no alphanumeric characters);
- it carries a DOI link.
Anything else in the zones is kept: a unique header-zone title is content,
not furniture.
"""
from __future__ import annotations

import re
from collections import Counter

HEADER_TOP_FRACTION = 0.09
FOOTER_BOTTOM_FRACTION = 0.07
MIN_REPEAT_PAGES = 2

_DOI_RE = re.compile(r"10\.\d{4,}/[^\s]+|doi\.org/[^\s]+", re.IGNORECASE)


def _normalise(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _is_pure_symbols(text: str) -> bool:
    return bool(text and not any(ch.isalnum() for ch in text))


def strip_header_footer(
    pages: list[dict],
    *,
    min_repeat_pages: int = MIN_REPEAT_PAGES,
) -> list[dict]:
    """Pages (``{height, spans}``) -> same shape with furniture removed.

    Repeat counting is per page (a text repeated twice on ONE page does
    not count); a span counts as repeating when its normalised text
    appears on at least ``min_repeat_pages`` distinct pages.
    """
    page_texts: list[Counter] = []
    for page in pages or []:
        counter: Counter = Counter()
        for span in page.get("spans", []) or []:
            norm = _normalise(span.get("text", ""))
            if norm:
                counter[norm] = 1  # presence per page, not occurrences
        page_texts.append(counter)
    repeat_counts: Counter = Counter()
    for counter in page_texts:
        repeat_counts.update(counter)

    out: list[dict] = []
    for page, counter in zip(pages or [], page_texts):
        height = float(page.get("height") or 0)
        kept: list[dict] = []
        for span in page.get("spans", []) or []:
            text = span.get("text", "") or ""
            y0 = float(span.get("y0", 0))
            y1 = float(span.get("y1", 0))
            in_header = height > 0 and y1 <= HEADER_TOP_FRACTION * height
            in_footer = height > 0 and y0 >= (1 - FOOTER_BOTTOM_FRACTION) * height
            if not (in_header or in_footer):
                kept.append(span)
                continue
            norm = _normalise(text)
            if (
                (norm and repeat_counts.get(norm, 0) >= min_repeat_pages)
                or _is_pure_symbols(text)
                or _DOI_RE.search(text)
            ):
                continue
            kept.append(span)
        out.append({**page, "spans": kept})
    return out
