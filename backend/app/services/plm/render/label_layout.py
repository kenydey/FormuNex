"""Label layouts from the label designer (方案 §3.1 / §5.5.5, F4): positioned elements on a GB 15258 label.

A layout is JSON::

    {"size_mm": [100, 150], "border": true,
     "elements": [{"id": "e1", "type": "pictograms", "x": 4, "y": 20, "w": 92, "h": 24,
                   "font_size": 8, "align": "center", "bold": false, "text": ""}]}

``x`` / ``y`` are millimetres from the top-left corner. Element types bind to
the label content (product name, pictograms, statements …); ``text`` elements
print their own text, where ``{{name}}`` is replaced by a batch variable
(batch number, production date, net content …). Every element is drawn inside
its box; text that does not fit is reported as overflow rather than clipped
silently.
"""
from __future__ import annotations

import io
import re
from typing import Any
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
from .label_pdf import _field, _pictogram

ELEMENT_TYPES = (
    "product_name", "ingredients", "pictograms", "signal_word", "hazard_statements", "precautionary_statements",
    "supplier", "emergency_phone", "sds_reference", "qr", "text",
)
_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class LayoutError(ValueError):
    pass


def validate_layout(layout: dict[str, Any]) -> dict[str, Any]:
    size = layout.get("size_mm") or [100, 150]
    try:
        w, h = float(size[0]), float(size[1])
    except (TypeError, ValueError, IndexError):
        raise LayoutError("size_mm 必须是 [宽, 高]（毫米）") from None
    if not (20 <= w <= 600 and 20 <= h <= 600):
        raise LayoutError("标签尺寸应在 20～600 mm 之间")
    elements = []
    for i, e in enumerate(layout.get("elements") or []):
        t = e.get("type")
        if t not in ELEMENT_TYPES:
            raise LayoutError(f"第 {i + 1} 个元素类型无效：{t}")
        try:
            x, y, ew, eh = (float(e[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            raise LayoutError(f"第 {i + 1} 个元素缺少位置或尺寸") from None
        if ew <= 0 or eh <= 0 or x < 0 or y < 0 or x + ew > w + 0.01 or y + eh > h + 0.01:
            raise LayoutError(f"第 {i + 1} 个元素（{t}）超出标签范围")
        elements.append({"id": str(e.get("id") or f"e{i + 1}"), "type": t, "x": x, "y": y, "w": ew, "h": eh,
                         "font_size": float(e.get("font_size") or 7), "align": e.get("align") or "left",
                         "bold": bool(e.get("bold")), "text": str(e.get("text") or "")})
    required = {"product_name", "pictograms", "signal_word", "hazard_statements", "precautionary_statements",
                "supplier", "emergency_phone"}
    missing = sorted(required - {e["type"] for e in elements})
    return {"size_mm": [w, h], "border": bool(layout.get("border", True)), "elements": elements,
            "missing_elements": missing}


def _text_for(content: dict, e: dict, variables: dict[str, Any]) -> str:
    t = e["type"]
    if t == "text":
        return _VAR.sub(lambda m: str(variables.get(m.group(1), m.group(0))), e["text"])
    if t == "ingredients":
        return "、".join(_field(content, "ingredients") or [])
    if t in ("hazard_statements", "precautionary_statements"):
        return "\n".join(f"{s['code']} {s['text']}" for s in _field(content, t) or [])
    if t == "emergency_phone":
        phone = _field(content, "emergency_phone")
        return f"{'Emergency telephone' if content.get('lang') == 'en-US' else '应急咨询电话'}：{phone}" if phone else ""
    return str(_field(content, t) or "")


def render_layout_pdf(content: dict, layout: dict[str, Any], variables: dict[str, Any] | None = None
                      ) -> tuple[bytes, dict]:
    if rl_canvas is None:
        raise MissingRenderDependency("reportlab")
    layout = validate_layout(layout)
    variables = variables or {}
    font = resolve_font()
    w, h = layout["size_mm"][0] * mm, layout["size_mm"][1] * mm
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(w, h))
    c.setTitle(content.get("title", ""))
    if layout["border"]:
        c.rect(1.5 * mm, 1.5 * mm, w - 3 * mm, h - 3 * mm)
    overflow: list[str] = []
    for e in layout["elements"]:
        x, top, ew, eh = e["x"] * mm, h - e["y"] * mm, e["w"] * mm, e["h"] * mm
        if e["type"] == "pictograms":
            codes = _field(content, "pictograms") or []
            if codes:
                side = min(eh, ew / len(codes) - 1 * mm)
                cx = x + (ew - len(codes) * (side + 1 * mm)) / 2 if e["align"] == "center" else x
                for code in codes:
                    d = _pictogram(code)
                    if d is None:
                        continue
                    k = side / max(d.width, d.height)
                    d.scale(k, k)
                    d.width, d.height = d.width * k, d.height * k
                    renderPDF.draw(d, c, cx, top - side)
                    cx += side + 1 * mm
            continue
        if e["type"] == "qr":
            payload = (content.get("meta") or {}).get("qr_payload") or variables.get("qr")
            if payload:
                import segno

                matrix = segno.make(str(payload), error="m").matrix
                n = len(matrix)
                side = min(ew, eh)
                cell = side / n
                c.setFillColor(colors.black)
                for r, row in enumerate(matrix):
                    for col, bit in enumerate(row):
                        if bit:
                            c.rect(x + col * cell, top - side + (n - 1 - r) * cell, cell, cell, stroke=0, fill=1)
            continue
        text = _text_for(content, e, variables)
        if not text:
            continue
        size = e["font_size"] * (1.4 if e["type"] == "signal_word" and e["font_size"] < 9 else 1)
        style = ParagraphStyle(e["id"], fontName=font.bold_name if e["bold"] or e["type"] == "signal_word" else font.name,
                               fontSize=size, leading=size * 1.25, wordWrap="CJK",
                               alignment={"left": 0, "center": 1, "right": 2}.get(e["align"], 0))
        story = [Paragraph("<br/>".join(escape(line) for line in text.split("\n")), style)]
        Frame(x, top - eh, ew, eh, showBoundary=0, leftPadding=0, rightPadding=0, topPadding=0,
              bottomPadding=0).addFromList(story, c)
        if story:
            overflow.append(e["id"])
    c.showPage()
    c.save()
    return buf.getvalue(), {"font": font.name, "font_embedded": font.embedded, "overflow": bool(overflow),
                            "overflow_elements": overflow, "missing_elements": layout["missing_elements"]}
