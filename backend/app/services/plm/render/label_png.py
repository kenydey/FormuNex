"""Render a GB 15258 label PDF to PNG (for web preview / image export).

The label's stored artifact is the PDF; PNG is a derived view produced on
demand by rasterising the first page with PyMuPDF (``fitz``). The physical
label size is preserved: at ``dpi`` the pixel dimensions are
``size_mm / 25.4 * dpi``.
"""
from __future__ import annotations


class LabelPngError(RuntimeError):
    """PyMuPDF is unavailable, or the PDF cannot be rasterised."""


def render_label_png(pdf_bytes: bytes, *, dpi: int = 300) -> bytes:
    if not (72 <= dpi <= 600):
        raise LabelPngError(f"DPI 须在 72–600 之间，得到 {dpi}")
    if not pdf_bytes:
        raise LabelPngError("PDF 为空，无法转 PNG")
    try:
        import fitz  # PyMuPDF (AGPL-3.0); pinned in pyproject, installed with the parse_pro extra
    except ImportError as exc:
        raise LabelPngError("未安装 PyMuPDF（pip install PyMuPDF），无法渲染标签 PNG") from exc
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise LabelPngError(f"标签 PDF 无法打开：{exc}") from exc
    with doc:
        if doc.page_count < 1:
            raise LabelPngError("标签 PDF 没有页面")
        pix = doc[0].get_pixmap(dpi=dpi)
        return pix.tobytes("png")
