"""Batch 5 (chunking) regression tests.

Each test pins one batch-5 fix at the layer where it lives. Section 6
control behaviours (empty-line/bold captions, same/skip-level headings,
repeated-section offsets, single-line $$/\\[ self-closing, pipe-table
atomicity) are covered by the pre-existing chunking suites, which must
stay green alongside this file.
"""

from __future__ import annotations

from app.services import chunking
from app.services.chunking import (
    _is_atomic,
    _is_table_caption,
    _math_line_closes,
    _math_selfclosed,
    _parse_heading,
    _split_blocks,
    _split_sections,
    chunk_markdown,
    chunk_plain_text_with_offsets,
    mark_example_boundaries,
)


def test_p2_b54_vision_placeholder_variants():
    assert chunking._is_vision_placeholder("> _（视觉解析失败）_") is True
    assert chunking._is_vision_placeholder("> (视觉解析失败)") is True
    assert chunking._is_vision_placeholder("> _（视觉解析已跳过）_") is True
    assert chunking._is_vision_placeholder("> _（视觉解析失败）_\n实质内容") is False


def test_p2_b43_heading_allows_up_to_three_leading_spaces():
    assert _parse_heading("   ## Title") == (2, "Title")
    assert _parse_heading("    ## Title") is None
    assert _parse_heading("## Title") == (2, "Title")


def test_p2_b44_atx_closing_hashes_only():
    assert _parse_heading("## C#") == (2, "C#")
    assert _parse_heading("## F#") == (2, "F#")
    assert _parse_heading("## Title ##") == (2, "Title")
    assert _parse_heading("## Title# #") == (2, "Title#")


def test_p1_b23_single_line_begin_end_is_selfclosed():
    assert _math_selfclosed("\\begin{aligned}x\\end{aligned}") is True
    assert _math_selfclosed("\\begin{aligned}x\\end{equation}") is False
    assert _math_selfclosed("$$x$$") is True
    assert _math_selfclosed("$$x") is False


def test_p1_b24_math_line_closes():
    assert _math_line_closes("x $$", "$$") is True
    assert _math_line_closes("$$5$$", "$$") is False
    assert _math_line_closes("$$", "$$") is True
    assert _math_line_closes("x \\]", "\\]") is True
    assert _math_line_closes("\\[ a \\]", "\\]") is False
    assert _math_line_closes("x \\end{aligned}", "\\end{") is True
    assert _math_line_closes("\\begin{aligned} x \\end{aligned}", "\\end{") is False


def test_p1_b26_fence_line_inside_math_is_content():
    md = "$$\na\n```\nb\n$$\n\n# Real\ncontent"
    paths = [p for p, _ in _split_sections(md)]
    assert "Real" in paths


def test_p1_b24_line_end_closer_reenables_headings():
    md = "$$\na\nx $$\n# After\ncontent"
    paths = [p for p, _ in _split_sections(md)]
    assert "After" in paths


def test_p2_b53_unclosed_math_guard_restores_headings():
    body = "\n".join(f"line{i}" for i in range(60))
    md = "$$\n" + body + "\n# After\ncontent"
    paths = [p for p, _ in _split_sections(md)]
    assert "After" in paths


def test_p1_b22_cap_trip_suppresses_mode_reentry():
    lines = ["```"] + [f"line{i}" for i in range(60)] + ["```", "# tail"]
    blocks = _split_blocks("\n".join(lines))
    assert blocks[0].startswith("```")
    assert all(not b.startswith("```") for b in blocks[1:])
    assert any("# tail" in b for b in blocks[1:])


def test_p2_b46_page_marker_inside_fence_and_math():
    fence = _split_blocks("```\ncode1\n<!-- page:2 -->\ncode2\n```")
    assert "<!-- page:2 -->" in fence
    math = _split_blocks("$$\na\n<!-- page:2 -->\nb\n$$")
    assert "<!-- page:2 -->" in math


def test_p2_b50_pipeless_table_is_one_block():
    blocks = _split_blocks("指标 | 值\n---|---\n盐雾 | 1000h\n\n正文")
    assert blocks[0] == "指标 | 值\n---|---\n盐雾 | 1000h"
    assert blocks[1] == "正文"
    assert _is_atomic(blocks[0]) is True
    assert chunking._classify_block_type(blocks[0]) == "table"


def test_p2_b51_atomic_table_must_lead_the_block():
    assert _is_atomic("<table><tr><td>a</td></tr></table>") is True
    assert _is_atomic("前言文字\n<table><tr><td>a</td></tr></table>") is False


def test_p2_b52_example_heads_inside_fence_and_math_untouched():
    fenced = mark_example_boundaries("```\nExample 1\n```\n\nExample 2\n")
    assert "## Example 1" not in fenced
    assert "## Example 2" in fenced
    mathed = mark_example_boundaries("$$\nExample 1\n$$\n\nExample 2\n")
    assert "## Example 1" not in mathed
    assert "## Example 2" in mathed


def test_p2_b47_caption_forms():
    assert _is_table_caption("表格 1 性能对比") is True
    assert _is_table_caption("table 2 results") is True
    assert _is_table_caption("Tab 3 x") is True
    assert _is_table_caption("Tab. S1 附录") is True
    assert _is_table_caption("表 A 汇总") is True
    assert _is_table_caption("表一 说明") is True
    assert _is_table_caption("如表1所示，粘度符合要求") is False
    # B47 length half: the cap stays 300 — the pinned 403-char prose
    # paragraph must not become a caption (see test_chunking_table_caption).
    assert _is_table_caption("表 1 " + "长说明" * 90) is True
    assert _is_table_caption("表1 " + "说明文字" * 100) is False


def test_p1_b25_caption_survives_page_marker_gap():
    md = (
        "<!-- page:1 -->\n\n表 1 性能对比\n\n<!-- page:2 -->\n\n"
        "| 指标 | 值 |\n|---|---|\n| 盐雾 | 1000h |"
    )
    chunks = chunk_markdown(md)
    atoms = [c for c in chunks if "盐雾" in c.text]
    assert len(atoms) == 1
    atom = atoms[0]
    assert atom.text.startswith("表 1 性能对比")
    assert atom.page_no == 1
    assert atom.page_end == 2
    assert atom.offset_start == md.index("表 1")
    assert md[atom.offset_start:atom.offset_end].find("盐雾") != -1


def test_p2_b45_block_marker_does_not_leak_across_sections():
    md = "# S1\n\n<!-- block:table -->\n\n# S2\n\nhello"
    chunks = chunk_markdown(md)
    s2 = [c for c in chunks if c.text == "hello"]
    assert len(s2) == 1
    assert s2[0].block_type == "text"


def test_p2_b42_begin_doc_takes_structured_path():
    # Routing discriminator: the atomic branch emits an oversized math
    # block whole (atomic blocks are never hard-split), so the structured
    # path yields ONE chunk for a 2030-char doc at max_chars=100; the
    # plain path would split it into several.
    md = "\\begin{aligned}\n" + "x " * 1000 + "\n\\end{aligned}"
    chunks = chunk_markdown(md, max_chars=100)
    assert len(chunks) == 1
    assert chunks[0].text == md
    assert chunks[0].block_type == "formula"


def test_p2_b49_offsets_exact_for_repeated_text():
    text = "word " * 200
    pairs = chunk_plain_text_with_offsets(text, max_chars=50, overlap=10)
    assert len(pairs) > 2
    # Old find-based offsets resolved the second chunk to 5 (first repeat
    # after the cursor); the true stride is 50 - 10 = 40.
    assert pairs[1][1] == 40
    for c, off in pairs:
        assert text[off:off + len(c)] == c or off == pairs[0][1]


def test_p2_b48_recursive_split_carries_previous_tail():
    text = "AAA\n\n" + "B\n" * 300
    pairs = chunk_plain_text_with_offsets(text, max_chars=100, overlap=20)
    assert pairs[0][0] == "AAA"
    # The first recursive chunk starts with the previous chunk's tail.
    assert pairs[1][0].startswith("AAA")
    assert pairs[1][1] == 0
