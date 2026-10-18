"""Render structured document content (SDS / TDS / COA) to PDF with ReportLab.

One layout for every document type: title block, numbered sections with
label/value field tables and data tables, a running header (product, version)
and "第 x 页 共 y 页" footer (two-pass page count).

``pdfa=True`` post-processes the output to PDF/A-2b (see ``pdfa.py``).
"""
from __future__ import annotations

import io
from typing import Any
from xml.sax.saxutils import escape

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
except ImportError:
    # reportlab 是 documents extra：见 docx.py 同款 guard。
    colors = None  # type: ignore[assignment]
    A4 = None  # type: ignore[assignment]
    ParagraphStyle = None  # type: ignore[assignment]
    mm = None  # type: ignore[assignment]
    rl_canvas = None  # type: ignore[assignment]
    Paragraph = None  # type: ignore[assignment]
    SimpleDocTemplate = None  # type: ignore[assignment]
    Spacer = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]
    TableStyle = None  # type: ignore[assignment]

from . import MissingRenderDependency

from .fonts import resolve_font


def _text(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, list):
        return "<br/>".join(_text(v) for v in value)
    if isinstance(value, dict):
        if "code" in value and "text" in value:
            return escape(f"{value['code']} {value['text']}")
        return escape("；".join(f"{k}: {v}" for k, v in value.items()))
    return escape(str(value))


def _pictogram_row(codes: list[str], side: float | None = None):
    """GHS pictograms as vector drawings in one row (SDS section 2)."""
    # 默认值不在签名里算：mm 是 reportlab 的，缺依赖时模块仍要能 import。
    if side is None:
        side = 18 * mm
    from .label_pdf import _pictogram

    cells = []
    for code in codes:
        d = _pictogram(code)
        if d is None:
            cells.append(code)
            continue
        from copy import deepcopy

        d = deepcopy(d)
        k = side / max(d.width, d.height)
        d.scale(k, k)
        d.width, d.height = d.width * k, d.height * k
        cells.append(d)
    t = Table([cells], colWidths=[side + 2 * mm] * len(cells), hAlign="LEFT")
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return t


def _numbered_canvas(header_left: str, header_right: str, lang: str, font: str):
    class NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages: list[dict] = []

        def showPage(self):  # noqa: N802 - ReportLab API
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self, total: int) -> None:
            w, h = A4
            self.setFont(font, 8)
            self.setFillColor(colors.grey)
            self.drawString(18 * mm, h - 12 * mm, header_left[:80])
            self.drawRightString(w - 18 * mm, h - 12 * mm, header_right[:80])
            self.line(18 * mm, h - 13.5 * mm, w - 18 * mm, h - 13.5 * mm)
            page = (f"Page {self._pageNumber} of {total}" if lang == "en-US"
                    else f"第 {self._pageNumber} 页 共 {total} 页")
            self.drawCentredString(w / 2, 10 * mm, page)

    return NumberedCanvas


def render_pdf(content: dict, *, pdfa: bool = False) -> tuple[bytes, dict]:
    """Return (pdf bytes, render info: font, embedded)."""
    if Paragraph is None:
        raise MissingRenderDependency("reportlab")
    font = resolve_font()
    lang = content.get("lang", "zh-CN")
    base = ParagraphStyle("base", fontName=font.name, fontSize=9, leading=12.5, wordWrap="CJK")
    small = ParagraphStyle("small", parent=base, fontSize=8, leading=11)
    title = ParagraphStyle("title", parent=base, fontSize=16, leading=22, alignment=1, spaceAfter=4)
    sub = ParagraphStyle("sub", parent=base, fontSize=9, alignment=1, textColor=colors.grey)
    h2 = ParagraphStyle("h2", parent=base, fontSize=11, leading=15, spaceBefore=8, spaceAfter=4,
                        textColor=colors.white, backColor=colors.HexColor("#2F4F6F"), borderPadding=(2, 4, 2, 4))

    header = content.get("header") or {}
    story: list = [Paragraph(escape(content.get("title", "")), title)]
    sub_bits = [header.get("product_name"), header.get("product_code"),
                (f"Version {header['version']}" if lang == "en-US" else f"版本 {header['version']}") if header.get("version") else None,
                header.get("revision_date"), header.get("standard")]
    story.append(Paragraph(escape(" · ".join(str(b) for b in sub_bits if b)), sub))
    story.append(Spacer(1, 4 * mm))
    width = A4[0] - 36 * mm

    for s in content.get("sections", []):
        story.append(Paragraph(escape(f"{s['no']}  {s['title']}"), h2))
        rows = [[Paragraph(escape(f.get("label") or ""), small),
                 _pictogram_row(f["value"]) if f.get("key") == "pictograms" and f.get("value") else
                 Paragraph(_text(f.get("value")), base)]
                for f in s.get("fields", [])]
        if rows:
            tbl = Table(rows, colWidths=[width * 0.3, width * 0.7])
            tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                     ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                                     ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#333333"))]))
            story.append(tbl)
        for tb in s.get("tables", []):
            cols = tb.get("columns") or []
            data = [[Paragraph(f"<b>{escape(str(c))}</b>", small) for c in cols]]
            data += [[Paragraph(_text(v), small) for v in row] for row in tb.get("rows", [])]
            if len(data) == 1:
                data.append([Paragraph("—", small) for _ in cols])
            t = Table(data, colWidths=[width / max(1, len(cols))] * len(cols), repeatRows=1)
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF4")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.append(Spacer(1, 2 * mm))
            story.append(t)
        for p in s.get("paragraphs", []):
            story.append(Spacer(1, 1.5 * mm))
            story.append(Paragraph(escape(p), small))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm,
                            bottomMargin=16 * mm, title=content.get("title", ""), author=header.get("product_name") or "")
    left = " · ".join(str(b) for b in (header.get("product_name"), content.get("title")) if b)
    right = " · ".join(str(b) for b in (header.get("product_code"), header.get("batch_no") or header.get("version")) if b)
    doc.build(story, canvasmaker=_numbered_canvas(left, right, lang, font.name))
    pdf_bytes = buf.getvalue()
    info: dict = {"font": font.name, "font_embedded": font.embedded, "font_path": font.path}
    if pdfa:
        if not font.embedded:
            from .pdfa import PdfaError

            raise PdfaError("PDF/A-2b 要求全部字体嵌入：未找到可嵌入的 TrueType 字体（plm_doc_font_path）")
        from .pdfa import apply_pdfa_2b

        pdf_bytes, pdfa_info = apply_pdfa_2b(pdf_bytes, title=content.get("title", ""),
                                              author=header.get("product_name") or "")
        info.update(pdfa_info)
    return pdf_bytes, info
