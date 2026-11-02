"""Stage 5 / SR-092: vote pages and body pages raster from the same source.

Pins the chain the confirmation rests on: the same pdf_local.page_as_png
renderer, at the same DPI (rapidocr_dpi default 120 == the Tesseract body
path's hardcoded 120). If any link changes DPI or renderer, this fails
and the same-source claim must be re-established before parallelizing.
"""

from __future__ import annotations

import fitz  # type: ignore
import pytest

from app.config import get_settings
from app.services import pdf_local, rapidocr_local, tesseract_local


def _make_pdf(n_pages: int) -> bytes:
    doc = fitz.open()
    for i in range(1, n_pages + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i} body text " * 20)
    out = doc.tobytes()
    doc.close()
    return out


def _record_raster(monkeypatch):
    dpis: list[int] = []

    def _fake(content, page_no, dpi=150, **kwargs):
        dpis.append(dpi)
        return b"fake-png"

    monkeypatch.setattr(pdf_local, "page_as_png", _fake)
    return dpis


def test_tesseract_body_raster_matches_vote_dpi(monkeypatch):
    dpis = _record_raster(monkeypatch)
    monkeypatch.setattr(tesseract_local, "tesseract_available", lambda: True)
    monkeypatch.setattr(tesseract_local, "ocr_png", lambda png, lang="eng": "some english text")
    out = tesseract_local.ocr_pdf(_make_pdf(2), lang="eng")
    assert out is not None
    assert dpis and all(d == 120 for d in dpis)


def test_rapidocr_body_raster_uses_rapidocr_dpi(monkeypatch):
    dpis = _record_raster(monkeypatch)
    monkeypatch.setattr(rapidocr_local, "rapidocr_available", lambda: (True, ""))
    monkeypatch.setattr(rapidocr_local, "ocr_png_scored", lambda png: ("some text", 0.99))
    out = rapidocr_local.ocr_pdf(_make_pdf(2))
    assert out is not None
    assert dpis and all(d == get_settings().rapidocr_dpi for d in dpis)


def test_default_rapidocr_dpi_is_the_tesseract_dpi():
    assert get_settings().rapidocr_dpi == 120
