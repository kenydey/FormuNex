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
from functools import lru_cache
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


@lru_cache(maxsize=1)
def resolve_font() -> FontChoice:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    configured = os.environ.get("FORMUNEX_PLM_DOC_FONT_PATH")
    if not configured:
        try:
            from ....config import get_settings

            configured = get_settings().plm_doc_font_path or None
        except Exception:  # noqa: BLE001
            configured = None
    candidates = ([(configured, 0)] if configured else []) + _CANDIDATES
    for path, index in candidates:
        if path and Path(path).is_file() and _try_register(path, index):
            return FontChoice("FNX-CJK", "FNX-CJK", True, path)
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    log.warning("no embeddable CJK TrueType font found; using non-embedded STSong-Light")
    return FontChoice("STSong-Light", "STSong-Light", False, None)
