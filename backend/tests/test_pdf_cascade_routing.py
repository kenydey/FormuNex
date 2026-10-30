"""PDF cascade v2: routing + quality gate + 5-tier default order.

Covers the cascade-optimization execution (report §8):
- auto order drops the dormant docling/marker tiers;
- explicit pin still reaches the opt-in tiers with unchanged pin semantics;
- fully scanned docs skip markitdown (provably useless without OCR) and,
  over the MinerU page cap, skip the whole-doc mineru tier (cap bypass closed);
- a hybrid win that fails the conservative quality gate is challenged by the
  whole-doc mineru tier, and a rejected challenger keeps the hybrid result.
"""

import pytest

from app.config import get_settings
from app.services import parsing


def _names(order):
    return [n for n, _ in order]


def _pdf_bytes(pages_text):
    import fitz

    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    buf = doc.tobytes()
    doc.close()
    return buf


def test_auto_order_is_five_tiers_without_dormant():
    assert _names(parsing._pdf_tier_order("auto")) == [
        "hybrid",
        "mineru",
        "rapidocr",
        "markitdown",
        "pypdf",
    ]


def test_pin_docling_still_reaches_optin_tier():
    assert _names(parsing._pdf_tier_order("docling")) == [
        "docling",
        "marker",
        "mineru",
        "rapidocr",
        "markitdown",
        "pypdf",
    ]


def test_pin_unknown_falls_back_to_auto_order():
    assert _names(parsing._pdf_tier_order("nope")) == [
        "hybrid",
        "mineru",
        "rapidocr",
        "markitdown",
        "pypdf",
    ]


def test_pin_mineru_unusable_falls_back_to_auto_order(monkeypatch):
    monkeypatch.setattr(parsing, "_mineru_usable", lambda: False)
    assert _names(parsing._pdf_tier_order("mineru")) == [
        "hybrid",
        "mineru",
        "rapidocr",
        "markitdown",
        "pypdf",
    ]


def test_docling_marker_not_claimed_by_parse_pro():
    from app.services.dependencies import CATALOG

    by_pip = {d.pip_name: d for d in CATALOG}
    assert by_pip["docling"].extra == "parse_optin"
    assert by_pip["marker-pdf"].extra == "parse_optin"


def test_page_text_stats_counts_blank_pages():
    assert parsing._pdf_page_text_stats(_pdf_bytes(["", ""])) == (2, 2)
    assert parsing._pdf_page_text_stats(_pdf_bytes(["hello world " * 20])) == (1, 0)


def test_hybrid_quality_gate_density_and_table_loss():
    assert parsing._hybrid_quality_failed("hi", 3, 50) is True
    assert parsing._hybrid_quality_failed("x" * 500, 1, 50) is False
    assert parsing._hybrid_quality_failed("见表 1 的结果如下。\n\n一些文字，没有管道表格。", 1, 50) is True
    assert (
        parsing._hybrid_quality_failed(
            "见表 1 的结果如下。\n\n| 指标 | 值 |\n|---|---|\n| 盐雾 | 1000h |\n" + "x" * 200, 1, 50
        )
        is False
    )


def _gate_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "pdf_routing_enabled", True)
    monkeypatch.setattr(settings, "pdf_quality_gate_enabled", True)
    monkeypatch.setattr(settings, "pdf_quality_gate_min_chars_per_page", 50)


def _attempt_recorder(monkeypatch, target, result):
    attempted = []

    def _wrapper(content, *args):
        attempted.append(target)
        return result

    monkeypatch.setattr(parsing, target, _wrapper)
    return attempted


def test_quality_gate_escalates_to_mineru_on_table_loss(monkeypatch):
    _gate_settings(monkeypatch)
    monkeypatch.setattr(parsing, "_mineru_usable", lambda: True)
    monkeypatch.setattr(
        parsing,
        "_parse_hybrid",
        lambda c: "见表 1 的结果如下。\n\n一些文字描述，没有管道表格。",
    )
    monkeypatch.setattr(
        parsing,
        "_parse_mineru",
        lambda c: parsing.ParseResult(
            "| 指标 | 值 |\n|---|---|\n| 盐雾 | 1000h |", "mineru"
        ),
    )
    result = parsing.parse_document(_pdf_bytes(["hello world " * 20]), "pdf")
    assert result.parser == "mineru"
    assert "盐雾" in result.markdown


def test_quality_gate_pass_keeps_hybrid_and_skips_challenger(monkeypatch):
    _gate_settings(monkeypatch)
    monkeypatch.setattr(parsing, "_mineru_usable", lambda: True)
    monkeypatch.setattr(
        parsing,
        "_parse_hybrid",
        lambda c: "正文内容。\n\n| 指标 | 值 |\n|---|---|\n| 盐雾 | 1000h |\n" + "x" * 500,
    )
    attempted = _attempt_recorder(monkeypatch, "_parse_mineru", None)
    result = parsing.parse_document(_pdf_bytes(["hello world " * 20]), "pdf")
    assert result.parser == "hybrid"
    assert attempted == []


def test_quality_gate_skipped_when_mineru_unusable(monkeypatch):
    _gate_settings(monkeypatch)
    monkeypatch.setattr(parsing, "_mineru_usable", lambda: False)
    monkeypatch.setattr(
        parsing,
        "_parse_hybrid",
        lambda c: "见表 1 的结果如下。\n\n一些文字描述，没有管道表格。",
    )
    attempted = _attempt_recorder(monkeypatch, "_parse_mineru", None)
    result = parsing.parse_document(_pdf_bytes(["hello world " * 20]), "pdf")
    assert result.parser == "hybrid"
    assert attempted == []


def test_router_drops_markitdown_for_fully_scanned(monkeypatch):
    _gate_settings(monkeypatch)
    monkeypatch.setattr(parsing, "_parse_hybrid", lambda c: None)
    monkeypatch.setattr(parsing, "_parse_mineru", lambda c: None)
    monkeypatch.setattr(parsing, "_parse_rapidocr", lambda c: None)
    attempted = []

    def _markitdown(content, ext):
        attempted.append("markitdown")
        return "md text"

    monkeypatch.setattr(parsing, "_parse_markitdown", _markitdown)
    monkeypatch.setattr(parsing, "_parse_pypdf", lambda c: "pypdf text")
    # Without the router drop, markitdown would be reached after rapidocr
    # returns None; with it, the order jumps straight to pypdf.
    result = parsing.parse_document(_pdf_bytes(["", ""]), "pdf")
    assert result.parser == "pypdf"
    assert "markitdown" not in attempted


def test_router_skips_mineru_for_oversized_scanned(monkeypatch):
    _gate_settings(monkeypatch)
    monkeypatch.setattr(parsing, "_parse_hybrid", lambda c: None)
    attempted = []

    def _minerU(content, *args):  # noqa: N802
        attempted.append("mineru")
        return None

    monkeypatch.setattr(parsing, "_parse_mineru", _minerU)
    monkeypatch.setattr(parsing, "_parse_rapidocr", lambda c: "ocr text")
    # 12 blank pages > mineru_max_pages_per_doc (10): the whole-doc mineru
    # tier must not be attempted for this parse (cap bypass closed).
    result = parsing.parse_document(_pdf_bytes([""] * 12), "pdf")
    assert result.parser == "rapidocr"
    assert attempted == []


def test_routing_disabled_falls_back_to_static_order(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "pdf_routing_enabled", False)
    monkeypatch.setattr(settings, "pdf_quality_gate_enabled", False)
    monkeypatch.setattr(parsing, "_parse_hybrid", lambda c: None)
    monkeypatch.setattr(parsing, "_parse_mineru", lambda c: None)
    monkeypatch.setattr(parsing, "_parse_rapidocr", lambda c: None)
    attempted = []

    def _markitdown(content, ext):
        attempted.append("markitdown")
        return "md text"

    monkeypatch.setattr(parsing, "_parse_markitdown", _markitdown)
    result = parsing.parse_document(_pdf_bytes(["", ""]), "pdf")
    assert result.parser == "markitdown"
    assert attempted == ["markitdown"]
