"""One formulation (a leaderboard card) → PDF with ReportLab and the project's CJK font."""
from __future__ import annotations

import io
from datetime import datetime, timezone
from xml.sax.saxutils import escape

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
except ImportError:
    # reportlab 是 documents extra：最小安装仍要能 import，
    # 只有真正导出 PDF 时才报缺依赖（API 层转 503）。
    colors = None  # type: ignore[assignment]
    A4 = None  # type: ignore[assignment]
    ParagraphStyle = None  # type: ignore[assignment]
    mm = None  # type: ignore[assignment]
    Paragraph = None  # type: ignore[assignment]
    SimpleDocTemplate = None  # type: ignore[assignment]
    Spacer = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]
    TableStyle = None  # type: ignore[assignment]

from .plm.render import MissingRenderDependency

from ..domain.schemas import Formulation
from .plm.render.fonts import resolve_font

_LABELS = {
    "zh-CN": {
        "domain": "产品领域", "source": "来源", "score": "综合评分", "total": "合计", "generated": "生成时间",
        "ingredients": "组分", "name": "名称", "role": "角色", "cas": "CAS", "wt": "wt%",
        "predicted": "预测指标", "metric": "指标", "value": "预测值", "std": "±σ",
        "rationale": "设计依据", "warnings": "警告", "none": "—",
    },
    "en-US": {
        "domain": "Domain", "source": "Source", "score": "Score", "total": "Total", "generated": "Generated",
        "ingredients": "Ingredients", "name": "Name", "role": "Role", "cas": "CAS", "wt": "wt%",
        "predicted": "Predicted metrics", "metric": "Metric", "value": "Predicted", "std": "±σ",
        "rationale": "Rationale", "warnings": "Warnings", "none": "—",
    },
}


def _fmt(v: float | None, digits: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def render_formulation_pdf(f: Formulation, *, lang: str = "zh-CN", title: str | None = None) -> tuple[bytes, dict]:
    if Paragraph is None:
        raise MissingRenderDependency("reportlab")
    """Return (pdf bytes, render info: font, embedded)."""
    lab = _LABELS.get(lang, _LABELS["zh-CN"])
    font = resolve_font()
    base = ParagraphStyle("b", fontName=font.name, fontSize=9.5, leading=13)
    small = ParagraphStyle("s", parent=base, fontSize=8.5, leading=11)
    h1 = ParagraphStyle("h1", parent=base, fontName=font.bold_name, fontSize=16, leading=20, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=base, fontName=font.bold_name, fontSize=11.5, leading=15, spaceBefore=8, spaceAfter=4)

    def p(text: str, style: ParagraphStyle = base) -> Paragraph:
        return Paragraph(escape(text or lab["none"]).replace("\n", "<br/>"), style)

    grid = TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font.name),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f5")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ])
    domain = getattr(f.domain, "value", f.domain)
    story: list = [p(title or f.name, h1)]
    meta = [
        [p(lab["domain"], small), p(str(domain), small), p(lab["source"], small), p(f.source or lab["none"], small)],
        [p(lab["score"], small), p(_fmt(f.score, 3), small), p(lab["generated"], small),
         p(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), small)],
    ]
    mt = Table(meta, colWidths=[24 * mm, 61 * mm, 24 * mm, 61 * mm], hAlign="LEFT")
    mt.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [mt, Spacer(1, 4 * mm)]

    story.append(p(lab["ingredients"], h2))
    rows = [[p(lab["name"], small), p(lab["role"], small), p(lab["cas"], small), p(lab["wt"], small)]]
    for ing in f.ingredients:
        name = ing.name + (f"（{ing.zh_name}）" if ing.zh_name and ing.zh_name != ing.name else "")
        rows.append([p(name, small), p(ing.role, small), p(ing.cas_no or lab["none"], small), p(_fmt(ing.weight_pct), small)])
    rows.append([p(lab["total"], small), p("", small), p("", small), p(_fmt(f.total_pct()), small)])
    it = Table(rows, colWidths=[70 * mm, 36 * mm, 36 * mm, 28 * mm], hAlign="LEFT", repeatRows=1)
    it.setStyle(grid)
    story.append(it)

    if f.predicted:
        story.append(p(lab["predicted"], h2))
        prow = [[p(lab["metric"], small), p(lab["value"], small), p(lab["std"], small)]]
        for k, v in f.predicted.items():
            std = f.predicted_std.get(k)
            tier = f.prediction_tiers.get(k)
            prow.append([p(k + (f" [{tier}]" if tier else ""), small), p(_fmt(v), small), p(_fmt(std) if std is not None else "—", small)])
        pt = Table(prow, colWidths=[80 * mm, 45 * mm, 45 * mm], hAlign="LEFT", repeatRows=1)
        pt.setStyle(grid)
        story.append(pt)

    if f.rationale:
        story += [p(lab["rationale"], h2), p(f.rationale)]
    if f.warnings:
        story.append(p(lab["warnings"], h2))
        story += [p(f"• {w}", small) for w in f.warnings]

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=title or f.name)
    doc.build(story)
    return buf.getvalue(), {"font": font.name, "embedded": font.embedded}
