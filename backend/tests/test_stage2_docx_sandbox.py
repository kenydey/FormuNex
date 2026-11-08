"""Stage 2 / SR-008: docxtpl renders in a sandboxed Jinja environment."""
from __future__ import annotations

import io

import pytest
from docx import Document

from app.services.plm.render.docx import render_docx


def _template_docx(tag: str) -> bytes:
    doc = Document()
    doc.add_paragraph(tag)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _rendered_text(out: bytes) -> list[str]:
    return [p.text for p in Document(io.BytesIO(out)).paragraphs]


def test_dunder_access_is_blocked():
    # docxtpl's default environment renders this as "str" (verified);
    # sandboxed it must fail instead of reaching Python internals.
    with pytest.raises(Exception, match="SecurityError|access"):
        render_docx({"x": "abc"}, template=_template_docx("{{ x.__class__.__name__ }}"))


def test_legit_tags_still_render():
    out = render_docx(
        {"header": {"product_name": "PRODUCT-X"}, "sections": []},
        template=_template_docx("{{ header.product_name }}"),
    )
    assert _rendered_text(out) == ["PRODUCT-X"]


def test_text_helper_still_callable():
    out = render_docx(
        {"header": {}, "sections": []},
        template=_template_docx("{{ text(None) }}"),
    )
    assert _rendered_text(out) == ["—"]
