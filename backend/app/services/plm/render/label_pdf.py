"""Render a GB 15258 label to a PDF page of the label's physical size.

Pictograms are the GHS vector symbols (``resources/plm/pictograms/*.svg``),
drawn as vectors so print quality does not depend on resolution. Layout, top
to bottom: product identifier and hazardous ingredients, pictograms with the
signal word, hazard statements, precautionary statements, supplier block with
the 24 h emergency telephone and SDS reference, optional QR code.
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

try:
    from reportlab.graphics import renderPDF
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import Frame, Paragraph
except ImportError:
    # reportlab 是 documents extra：见 docx.py 同款 guard。
    renderPDF = None  # type: ignore[assignment]
    colors = None  # type: ignore[assignment]
    ParagraphStyle = None  # type: ignore[assignment]
    mm = None  # type: ignore[assignment]
    rl_canvas = None  # type: ignore[assignment]
    Frame = None  # type: ignore[assignment]
    Paragraph = None  # type: ignore[assignment]

from . import MissingRenderDependency

from .fonts import resolve_font

PICTOGRAM_DIR = Path(__file__).resolve().parents[3] / "resources" / "plm" / "pictograms"


@lru_cache(maxsize=16)
def _pictogram(code: str):
    from svglib.svglib import svg2rlg

    path = PICTOGRAM_DIR / f"{code}.svg"
    return svg2rlg(str(path)) if path.is_file() else None


def _field(content: dict, key: str):
    for s in content.get("sections", []):
        for f in s.get("fields", []):
            if f["key"] == key:
                return f["value"]
    return None


def render_label_pdf(content: dict) -> tuple[bytes, dict]:
    if rl_canvas is None:
        raise MissingRenderDependency("reportlab")
    font = resolve_font()
    size = (content.get("meta") or {}).get("size") or {}
    w, h = float(size.get("width_mm", 100)) * mm, float(size.get("height_mm", 150)) * mm
    pad = 4 * mm
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(w, h))
    c.setTitle(content.get("title", ""))
    c.setStrokeColor(colors.black)
    c.rect(1.5 * mm, 1.5 * mm, w - 3 * mm, h - 3 * mm)

    scale = min(w, h) / (100 * mm)  # type scales with the label
    base = ParagraphStyle("b", fontName=font.name, fontSize=6.5 * scale, leading=8.5 * scale, wordWrap="CJK")
    name_style = ParagraphStyle("n", parent=base, fontSize=11 * scale, leading=14 * scale, alignment=1)
    signal_style = ParagraphStyle("s", parent=base, fontSize=13 * scale, leading=16 * scale, alignment=1)

    y = h - pad
    # Product identifier.
    story = [Paragraph(escape(str(_field(content, "product_name") or "")), name_style)]
    ingredients = _field(content, "ingredients") or []
    if ingredients:
        story.append(Paragraph(escape("、".join(ingredients)), ParagraphStyle("i", parent=base, alignment=1)))
    top_h = 9 * mm * scale + (4 * mm * scale if ingredients else 0)
    Frame(pad, y - top_h, w - 2 * pad, top_h, showBoundary=0, leftPadding=0, rightPadding=0,
          topPadding=0, bottomPadding=0).addFromList(story, c)
    y -= top_h

    # Pictograms row.
    codes = _field(content, "pictograms") or []
    if codes:
        side = min(22 * mm * scale, (w - 2 * pad) / max(1, len(codes)) - 1 * mm)
        x = (w - len(codes) * (side + 1 * mm)) / 2
        for code in codes:
            d = _pictogram(code)
            if d is None:
                continue
            k = side / max(d.width, d.height)
            d.scale(k, k)
            d.width, d.height = d.width * k, d.height * k
            renderPDF.draw(d, c, x, y - side)
            x += side + 1 * mm
        y -= side + 2 * mm

    word = _field(content, "signal_word")
    if word:
        f = Frame(pad, y - 8 * mm * scale, w - 2 * pad, 8 * mm * scale, showBoundary=0, leftPadding=0,
                  rightPadding=0, topPadding=0, bottomPadding=0)
        f.addFromList([Paragraph(f"<b>{escape(word)}</b>", signal_style)], c)
        y -= 8 * mm * scale

    # Statements and supplier fill the rest.
    h_lines = [f"{s['code']} {s['text']}" for s in _field(content, "hazard_statements") or []]
    p_lines = [f"{s['code']} {s['text']}" for s in _field(content, "precautionary_statements") or []]
    story = [Paragraph(escape(line), base) for line in h_lines]
    if p_lines:
        story.append(Paragraph("<br/>" + "<br/>".join(escape(line) for line in p_lines), base))
    supplier = _field(content, "supplier")
    phone = _field(content, "emergency_phone")
    sds = _field(content, "sds_reference")
    footer = "<br/>".join(escape(str(v)) for v in (supplier, f"应急咨询电话：{phone}" if phone else None, sds) if v)
    story.append(Paragraph("<br/>" + footer, base))
    qr = (content.get("meta") or {}).get("qr_payload")
    qr_side = 16 * mm * scale if qr else 0
    body = Frame(pad, pad, w - 2 * pad - (qr_side + 2 * mm if qr else 0), y - pad, showBoundary=0,
                 leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    body.addFromList(story, c)
    overflow = bool(story)  # addFromList consumes what fits; leftovers mean the label is too small
    if qr:
        import segno

        matrix = segno.make(qr, error="m").matrix
        n = len(matrix)
        cell = qr_side / n
        qx, qy = w - pad - qr_side, pad
        c.setFillColor(colors.black)
        for r, row in enumerate(matrix):
            for col, bit in enumerate(row):
                if bit:
                    c.rect(qx + col * cell, qy + (n - 1 - r) * cell, cell, cell, stroke=0, fill=1)
    c.showPage()
    c.save()
    return buf.getvalue(), {"font": font.name, "font_embedded": font.embedded, "overflow": overflow}
