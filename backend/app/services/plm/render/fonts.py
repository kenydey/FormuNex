"""CJK font for rendered documents.

ReportLab embeds TrueType outlines only (``.ttf`` / TrueType ``.ttc``); CFF-based
OpenType fonts (most Noto / Source Han ``.otf``) cannot be embedded. Resolution
order: ``FORMUNEX_PLM_DOC_FONT_PATH`` → known system TrueType CJK fonts →
ReportLab's built-in Adobe CID font ``STSong-Light``, which needs no file but is
*not embedded* (the reader substitutes a local font). The result says which, so
the release gate can refuse non-embedded fonts on controlled documents.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# (path, subfont index for .ttc)
_CANDIDATES: list[tuple[str, int]] = [
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
    ("/usr/share/fonts/truetype/arphic/uming.ttc", 0),
    ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", 0),
    ("C:/Windows/Fonts/msyh.ttc", 0),
    ("C:/Windows/Fonts/simsun.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", 0),
    ("/Library/Fonts/Arial Unicode.ttf", 0),
]


@dataclass(frozen=True)
class FontChoice:
    name: str
    bold_name: str
    embedded: bool
    path: str | None


def _try_register(path: str, index: int) -> bool:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    try:
        pdfmetrics.registerFont(TTFont("FNX-CJK", path, subfontIndex=index))
        return True
    except Exception as exc:  # noqa: BLE001 - CFF fonts, missing files, corrupt files
        log.debug("font %s unusable: %s", path, exc)
        return False


# W1-2: the old ``lru_cache`` keyed on nothing never saw a changed font
# configuration or a newly installed font. The cache below is validated
# against the configured path plus every candidate's existence/mtime on
# every call; a re-resolution (and re-registration) happens only when
# something actually changed. Registration itself is process-global in
# ReportLab, so a cache hit correctly skips re-registering.
_FONT_CACHE: dict[tuple, FontChoice] = {}


def _font_cache_clear() -> None:
    _FONT_CACHE.clear()


def _configured_font_path() -> str | None:
    configured = os.environ.get("FORMUNEX_PLM_DOC_FONT_PATH")
    if not configured:
        try:
            from ....config import get_settings

            configured = get_settings().plm_doc_font_path or None
        except Exception:  # noqa: BLE001
            configured = None
    return configured


def _font_cache_key(configured: str | None) -> tuple:
    key: list = [configured]
    for path, _index in ([(configured, 0)] if configured else []) + _CANDIDATES:
        if path:
            try:
                stat = Path(path).stat()
                key.append((path, stat.st_mtime_ns, stat.st_size))
            except OSError:
                key.append((path, None, None))
        else:
            key.append((None, None, None))
    return tuple(key)


def resolve_font() -> FontChoice:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    configured = _configured_font_path()
    key = _font_cache_key(configured)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    candidates = ([(configured, 0)] if configured else []) + _CANDIDATES
    for path, index in candidates:
        if path and Path(path).is_file() and _try_register(path, index):
            result = FontChoice("FNX-CJK", "FNX-CJK", True, path)
            _FONT_CACHE.clear()
            _FONT_CACHE[key] = result
            return result
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    log.warning("no embeddable CJK TrueType font found; using non-embedded STSong-Light")
    result = FontChoice("STSong-Light", "STSong-Light", False, None)
    _FONT_CACHE.clear()
    _FONT_CACHE[key] = result
    return result


# Compatibility: the old lru_cache exposed ``cache_clear``; keep it.
resolve_font.cache_clear = _font_cache_clear  # type: ignore[attr-defined]
