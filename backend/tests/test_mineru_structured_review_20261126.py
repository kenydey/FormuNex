"""Review 2026-11-26, wave 1 — parsing P1-2 / P2-4 (structured side).

P1-2: `parse_structured` must account for every expected page — a page
MinerU returned zero blocks for gets a visible placeholder block, a
warning and a user-facing note, and is recorded on
`MinerUStructured.missing_pages`.
P2-4: `parse_structured` must mirror hybrid's all-zero `page_idx`
detection — blocks carrying no page info on a multi-page document
render in document order with `page_no=None` (no false p.1 stamp),
with a warning and a note.
"""

from __future__ import annotations

import pytest

from app.services import mineru_cloud, mineru_structured
from app.services.mineru_structured import StructuredBlock, render_markdown
from app.services.parse_notices import collect


def _block(kind: str, **kw) -> mineru_cloud.MinerUBlock:
    return mineru_cloud.MinerUBlock(type=kind, **kw)


def _doc(*blocks: mineru_cloud.MinerUBlock) -> mineru_cloud.MinerUDocument:
    return mineru_cloud.MinerUDocument(blocks=list(blocks))


def _empty_pdf(pages: int) -> bytes:
    fitz = pytest.importorskip("fitz", reason="PyMuPDF is in the parse_pro extra")

    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()  # no text layer at all
    out = doc.tobytes()
    doc.close()
    return out


@pytest.fixture()
def cloud_doc(monkeypatch: pytest.MonkeyPatch):
    """Point parse_structured at a canned MinerU document."""

    def _install(doc):
        monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))
        monkeypatch.setattr(
            mineru_structured, "_doc_is_clean", lambda content: False
        )
        monkeypatch.setattr(
            mineru_cloud, "parse_bytes", lambda content, **kw: doc
        )

    return _install


# ── P1-2: whole-document structured page coverage ────────────────────────


def test_missing_page_gets_placeholder_block_and_note(
    monkeypatch: pytest.MonkeyPatch, cloud_doc
) -> None:
    """P1-2: MinerU returns blocks for p1 and p3 of a 3-page document —
    p2 must appear as a placeholder block, not vanish."""
    cloud_doc(_doc(
        _block("text", page_idx=0, text="第一页内容"),
        _block("text", page_idx=2, text="第三页内容"),
    ))
    with collect() as notices:
        out = mineru_structured.parse_structured(_empty_pdf(3))
    assert out is not None
    assert out.missing_pages == [2]
    assert "第一页内容" in out.markdown and "第三页内容" in out.markdown
    assert "第 2 页 MinerU 未返回内容" in out.markdown
    assert out.markdown.count("<!-- page:") == 3, "every expected page gets a marker"
    assert any("第 2 页" in n and "MinerU" in n for n in notices), notices
    placeholder = [b for b in out.blocks if "未返回内容" in b.text]
    assert len(placeholder) == 1 and placeholder[0].page_no == 2


def test_fully_covered_document_has_no_missing_pages(
    monkeypatch: pytest.MonkeyPatch, cloud_doc
) -> None:
    """P1-2 regression guard: full coverage must not gain placeholders,
    missing_pages entries or spurious notes."""
    cloud_doc(_doc(
        _block("text", page_idx=0, text="第一页内容"),
        _block("text", page_idx=1, text="第二页内容"),
        _block("text", page_idx=2, text="第三页内容"),
    ))
    with collect() as notices:
        out = mineru_structured.parse_structured(_empty_pdf(3))
    assert out is not None
    assert out.missing_pages == []
    assert "未返回内容" not in out.markdown
    assert not any("未返回" in n for n in notices), notices


# ── P2-4: all-zero page_idx ──────────────────────────────────────────────


def test_all_zero_page_idx_renders_in_order_without_false_p1(
    monkeypatch: pytest.MonkeyPatch, cloud_doc, caplog: pytest.LogCaptureFixture
) -> None:
    """P2-4: every block claims page 0 on a 3-page document — page
    attribution is untrustworthy, so blocks render in document order
    with page_no None and no false p.1 marker."""
    import logging

    cloud_doc(_doc(
        _block("text", text="第一块内容"),
        _block("text", text="第二块内容"),
    ))
    with collect() as notices, caplog.at_level(
        logging.WARNING, logger=mineru_structured.logger.name
    ):
        out = mineru_structured.parse_structured(_empty_pdf(3))
    assert out is not None
    assert all(b.page_no is None for b in out.blocks)
    assert "第一块内容" in out.markdown and "第二块内容" in out.markdown
    assert out.markdown.index("第一块内容") < out.markdown.index("第二块内容")
    assert "<!-- page:1 -->" not in out.markdown, "no false p.1 stamp"
    assert "<!-- page:" not in out.markdown
    assert any("无页码信息" in n for n in notices), notices
    assert any("no page indices" in r.getMessage() for r in caplog.records)


def test_single_page_all_zero_keeps_p1(
    monkeypatch: pytest.MonkeyPatch, cloud_doc
) -> None:
    """P2-4 guardrail (mirrors hybrid): a genuine page-1-only result
    shares the all-zero signal harmlessly — a 1-page document keeps
    its p.1 attribution and gets no warning/note."""
    cloud_doc(_doc(_block("text", text="唯一页内容")))
    with collect() as notices:
        out = mineru_structured.parse_structured(_empty_pdf(1))
    assert out is not None
    assert [b.page_no for b in out.blocks] == [1]
    assert "<!-- page:1 -->" in out.markdown
    assert not any("无页码信息" in n for n in notices), notices


def test_render_markdown_skips_marker_for_page_no_none() -> None:
    """P2-4 unit pin: a page_no=None block emits no page marker, while
    a following block with a real page number still emits its own."""
    md = render_markdown([
        StructuredBlock(page_no=None, kind="text", text="无页码内容"),
        StructuredBlock(page_no=2, kind="text", text="第二页内容"),
    ])
    assert "无页码内容" in md and "第二页内容" in md
    assert "<!-- page:1 -->" not in md
    assert "<!-- page:2 -->" in md
