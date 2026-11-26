"""Review 2026-11-26, wave 1 — parsing P1-1 / P1-2 (hybrid side).

P1-1: `figure` blocks must reach the vision path like `image`/`chart`
instead of vanishing when their text is empty.
P1-2: `_parse_scanned` must account for every expected page — a page
MinerU returned zero blocks for gets a visible placeholder, a warning
and a user-facing note instead of disappearing silently.
"""

from __future__ import annotations

import pytest

from app.services import hybrid_parse, mineru_cloud, pdf_local
from app.services.parse_notices import collect


def _page(page_no: int) -> pdf_local.LocalPage:
    return pdf_local.LocalPage(
        page_no=page_no, markdown="", char_count=0, n_tables=0,
        n_images=0, image_area_ratio=0.0, has_markdown_table=False,
    )


def _block(kind: str, **kw) -> mineru_cloud.MinerUBlock:
    return mineru_cloud.MinerUBlock(type=kind, **kw)


@pytest.fixture()
def no_vision(monkeypatch: pytest.MonkeyPatch):
    from app.services import vision_extract

    monkeypatch.setattr(vision_extract, "vision_available", lambda: (False, "未配置"))


def _render(blocks, monkeypatch, *, vision_result=None):
    """Render blocks, recording every vision call (mirrors
    test_hybrid_parse._render)."""
    from app.services import vision_extract

    seen: list[bytes] = []

    def fake_extract(content, filename):
        seen.append(content)
        return vision_result, None if vision_result else "no output"

    monkeypatch.setattr(vision_extract, "vision_available", lambda: (True, ""))
    monkeypatch.setattr(vision_extract, "extract_image", fake_extract)
    return hybrid_parse._render_blocks(blocks, page_label="p.1"), seen


# ── P1-1: figure blocks ──────────────────────────────────────────────────


def test_figure_block_reaches_the_vision_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1-1: a figure block is visual content — it must be looked at,
    not dropped because its text is empty."""
    _, seen = _render(
        [_block("figure", image=b"FIGURE", caption="结构图")], monkeypatch
    )
    assert seen == [b"FIGURE"]


def test_figure_block_survives_when_vision_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, no_vision
) -> None:
    """P1-1 (report probe, verbatim): an empty-text figure block with an
    image used to render as '' — now it keeps a visible placeholder."""
    out = hybrid_parse._render_blocks(
        [_block("figure", text="", image=b"\x89PNG" + b"x" * 100, caption="结构图")],
        page_label="p.1",
    )
    assert out != ""
    assert "[图 · p.1]" in out
    assert "结构图" in out


def test_figure_vision_output_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1-1: a figure that vision reads returns the vision result,
    including the verified SMILES."""
    from app.services.vision_extract import VisionExtraction, VisionMolecule

    extraction = VisionExtraction(
        kind="structure",
        markdown="苯环结构式",
        molecules=[VisionMolecule(smiles="c1ccccc1", name="苯",
                                  confidence=0.9, verified=True)],
    )
    out, seen = _render(
        [_block("figure", image=b"STRUCT", caption="Figure 1")],
        monkeypatch, vision_result=extraction,
    )
    assert seen == [b"STRUCT"]
    assert "c1ccccc1" in out


def test_unknown_type_with_image_but_no_text_reaches_vision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-1 (robustness arm of the report fix): an unknown block type
    carrying an image but no text is a figure by content."""
    _, seen = _render(
        [_block("some_future_visual", image=b"VISUAL")], monkeypatch
    )
    assert seen == [b"VISUAL"]


def test_unknown_type_with_text_and_image_keeps_its_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-1 guardrail: the unknown-type visual routing must not swallow
    text — a text-bearing unknown type keeps the old prose path and
    never reaches vision."""
    out, seen = _render(
        [_block("some_future_type", text="still useful", image=b"VISUAL")],
        monkeypatch,
    )
    assert seen == []
    assert "still useful" in out


# ── P1-2: whole-document scan page coverage ──────────────────────────────


def _scanned_doc(monkeypatch, blocks):
    """Drive _parse_scanned with a mocked MinerU response."""
    pages = [_page(n) for n in (1, 2, 3)]
    monkeypatch.setattr(
        hybrid_parse, "_sample_document_language", lambda content: None
    )
    monkeypatch.setattr(
        mineru_cloud,
        "parse_bytes",
        lambda content, **kw: mineru_cloud.MinerUDocument(blocks=list(blocks)),
    )
    return pages


def test_missing_page_gets_a_placeholder_and_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2: MinerU returns blocks for p1 and p3 only — p2 must appear
    as a visible placeholder, not vanish."""
    pages = _scanned_doc(
        monkeypatch,
        [_block("text", page_idx=0, text="第一页内容"),
         _block("text", page_idx=2, text="第三页内容")],
    )
    with collect() as notices:
        out = hybrid_parse._parse_scanned(b"%PDF scan", pages)
    assert out is not None
    assert "第一页内容" in out and "第三页内容" in out
    assert "第 2 页 MinerU 未返回内容" in out
    assert out.count("<!-- page:") == 3, "every expected page gets a marker"
    assert out.index("第一页内容") < out.index("第 2 页 MinerU 未返回内容") < out.index("第三页内容")
    assert any("第 2 页" in n and "MinerU" in n for n in notices), notices


def test_fully_covered_scan_has_no_placeholder_or_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1-2 regression guard: full coverage must not gain placeholders
    or spurious notes."""
    pages = _scanned_doc(
        monkeypatch,
        [_block("text", page_idx=0, text="第一页内容"),
         _block("text", page_idx=1, text="第二页内容"),
         _block("text", page_idx=2, text="第三页内容")],
    )
    with collect() as notices:
        out = hybrid_parse._parse_scanned(b"%PDF scan", pages)
    assert out is not None
    assert "未返回内容" not in out
    assert not any("未返回" in n for n in notices), notices
    assert out.count("<!-- page:") == 3
