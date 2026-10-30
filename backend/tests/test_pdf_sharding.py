"""Batch 1: PDF sharding (分片解析、断点续跑、完整性报告、开关、预算、置信度门)."""

from __future__ import annotations

import fitz  # type: ignore
import pytest

from app.config import get_settings
from app.services import parsing
from app.services import rapidocr_local
from app.services.parse_notices import collect as collect_notices
from app.services.parse_shards import (
    content_sha256,
    delete_for_content,
    get_shard,
    put_shard,
)


def _make_pdf(n_pages: int) -> bytes:
    doc = fitz.open()
    for i in range(1, n_pages + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i} body text " * 20)
    out = doc.tobytes()
    doc.close()
    return out


@pytest.fixture()
def _tables():
    """Create every model table (incl. parse_shards) in the session tmp DB."""
    from app.db.database import create_all_metadata, default_engine

    create_all_metadata(default_engine())
    yield


def _shard_settings(monkeypatch, **overrides):
    settings = get_settings()
    monkeypatch.setattr(settings, "pdf_shard_enabled", True)
    monkeypatch.setattr(settings, "pdf_shard_pages", 10)
    monkeypatch.setattr(settings, "pdf_shard_resume", True)
    monkeypatch.setattr(settings, "pdf_shard_stale_days", 7)
    monkeypatch.setattr(settings, "mineru_shard_budget", 0)
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value)
    return settings


def _fake_tier(monkeypatch, mapping):
    """Replace the tier order with fakes keyed by shard page count.

    mapping: {page_count: text-or-None}; a missing count returns None.
    Records every call in the returned list.
    """
    calls: list[int] = []

    def _fake(content, ext):
        n = fitz.open(stream=content, filetype="pdf").page_count
        calls.append(n)
        return mapping.get(n)

    monkeypatch.setattr(parsing, "_pdf_tier_order", lambda prefer: [("markitdown", _fake)])
    return calls


def test_split_pdf_pages_ranges():
    content = _make_pdf(25)
    assert fitz.open(stream=parsing._split_pdf_pages(content, 1, 10), filetype="pdf").page_count == 10
    assert fitz.open(stream=parsing._split_pdf_pages(content, 11, 20), filetype="pdf").page_count == 10
    assert fitz.open(stream=parsing._split_pdf_pages(content, 21, 25), filetype="pdf").page_count == 5
    assert parsing._split_pdf_pages(b"not a pdf", 1, 2) is None


def test_renumber_page_markers():
    text = "a <!-- page:1 --> b <!-- page:3 -->"
    assert parsing._renumber_page_markers(text, 10) == "a <!-- page:11 --> b <!-- page:13 -->"
    assert parsing._renumber_page_markers(text, 0) == text


def test_inject_approximate_markers_with_offset():
    content = _make_pdf(12)
    text = "".join(f"line {i} word word word word\n" for i in range(400))
    out = parsing._inject_approximate_page_markers(content, text, start_page=11)
    assert out.startswith("<!-- page:11 -->")
    assert "<!-- page:22 -->" in out
    assert "<!-- page:1 -->" not in out


def test_sharded_parse_merges_with_global_pages(monkeypatch, _tables):
    _shard_settings(monkeypatch)
    calls = _fake_tier(
        monkeypatch,
        {10: "SHARD-A <!-- page:1 --> body", 2: "SHARD-B <!-- page:1 --> body"},
    )
    result = parsing.parse_document(_make_pdf(12), "pdf")
    assert result.parser == "sharded"
    assert "SHARD-A" in result.markdown and "SHARD-B" in result.markdown
    # First shard keeps page 1; second shard renumbered 1 -> 11.
    assert result.markdown.count("<!-- page:1 -->") == 1
    assert "<!-- page:11 -->" in result.markdown
    assert result.table_stats["sharded"] is True
    assert result.table_stats["pages"] == 12
    assert result.table_stats["parsed_pages"] == 12
    assert result.table_stats["failed_shards"] == []
    assert calls == [10, 2]


def test_small_doc_does_not_shard(monkeypatch):
    _shard_settings(monkeypatch)
    _fake_tier(monkeypatch, {5: "SMALL <!-- page:1 --> body"})
    result = parsing.parse_document(_make_pdf(5), "pdf")
    assert result.parser == "markitdown"


def test_shard_switch_off_keeps_old_path(monkeypatch):
    settings = _shard_settings(monkeypatch)
    monkeypatch.setattr(settings, "pdf_shard_enabled", False)
    _fake_tier(monkeypatch, {12: "WHOLE <!-- page:1 --> body"})
    result = parsing.parse_document(_make_pdf(12), "pdf")
    assert result.parser == "markitdown"


def test_sharding_bypasses_2000_page_guard(monkeypatch, _tables):
    settings = _shard_settings(monkeypatch)
    monkeypatch.setattr(settings, "parse_max_pdf_pages", 20)
    _fake_tier(
        monkeypatch,
        {10: "P <!-- page:1 -->", 5: "P <!-- page:1 -->"},
    )
    result = parsing.parse_document(_make_pdf(25), "pdf")
    assert result.parser == "sharded"
    assert result.table_stats["pages"] == 25
    # And with sharding off, the same document is refused as before.
    monkeypatch.setattr(settings, "pdf_shard_enabled", False)
    refused = parsing.parse_document(_make_pdf(25), "pdf")
    assert refused.parser == "none"
    assert refused.failure_category == "oversized"


def test_failed_shard_reports_partial(monkeypatch, _tables):
    _shard_settings(monkeypatch)
    _fake_tier(monkeypatch, {10: "GOOD <!-- page:1 --> body"})
    with collect_notices() as notes:
        result = parsing.parse_document(_make_pdf(12), "pdf")
    assert result.parser == "sharded"
    assert "GOOD" in result.markdown
    assert result.table_stats["parsed_pages"] == 10
    assert result.table_stats["failed_shards"] == ["11-12"]
    assert any("11-12" in n for n in notes)


def test_resume_skips_cached_shard_and_cleans_up(monkeypatch, _tables):
    _shard_settings(monkeypatch)
    content = _make_pdf(12)
    sha = content_sha256(content)
    assert put_shard(sha, 0, 1, 10, "markitdown", "CACHED <!-- page:1 --> body")
    calls = _fake_tier(monkeypatch, {2: "FRESH <!-- page:1 --> body"})
    result = parsing.parse_document(content, "pdf")
    assert "CACHED" in result.markdown and "FRESH" in result.markdown
    assert "<!-- page:11 -->" in result.markdown
    # Only the uncached shard was parsed, and a clean merge deletes the rows.
    assert calls == [2]
    assert get_shard(sha, 0) is None
    delete_for_content(sha)


def test_mineru_budget_caps_shard_calls(monkeypatch, _tables):
    _shard_settings(monkeypatch, mineru_shard_budget=1)
    calls: list[str] = []

    def _hybrid(content, ext):
        return None

    def _mineru(content, ext):
        calls.append("mineru")
        return "MINERU <!-- page:1 --> body"

    monkeypatch.setattr(
        parsing,
        "_pdf_tier_order",
        lambda prefer: [("hybrid", _hybrid), ("mineru", _mineru)],
    )
    result = parsing.parse_document(_make_pdf(25), "pdf")
    assert calls == ["mineru"]
    assert result.parser == "sharded"
    assert result.table_stats["parsed_pages"] == 10
    assert result.table_stats["failed_shards"] == ["11-20", "21-25"]


def _make_blank_pdf(n_pages: int) -> bytes:
    doc = fitz.open()
    for _ in range(n_pages):
        doc.new_page()
    out = doc.tobytes()
    doc.close()
    return out


def test_oversized_scanned_shards_use_mineru_per_shard(monkeypatch, _tables):
    """Batch 1 supersession: the §8 mineru skip no longer applies to oversized
    scanned docs — each shard is within the cap, so mineru runs per shard."""
    _shard_settings(monkeypatch)
    monkeypatch.setattr(parsing, "_parse_hybrid", lambda c: None)
    attempted = []

    def _minerU(content, *args):  # noqa: N802
        attempted.append("mineru")
        return None

    monkeypatch.setattr(parsing, "_parse_mineru", _minerU)
    monkeypatch.setattr(parsing, "_parse_rapidocr", lambda c: "ocr text")
    result = parsing.parse_document(_make_blank_pdf(12), "pdf")
    assert result.parser == "sharded"
    assert attempted == ["mineru", "mineru"]
    assert "ocr text" in result.markdown


def test_shard_store_fail_open(monkeypatch):
    monkeypatch.setattr(
        "app.services.parse_shards._engine",
        lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    assert get_shard("deadbeef", 0) is None
    assert put_shard("deadbeef", 0, 1, 10, "x", "y") is False


def test_rapidocr_whole_doc_confidence_gate(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "rapidocr_min_confidence", 0.75)
    monkeypatch.setattr(rapidocr_local, "rapidocr_available", lambda: (True, ""))
    monkeypatch.setattr("app.services.pdf_local.page_count", lambda c: 2)
    monkeypatch.setattr("app.services.pdf_local.page_as_png", lambda c, n, dpi=None: b"png")
    monkeypatch.setattr(
        rapidocr_local, "ocr_png_scored", lambda png: ("some text here", 0.5)
    )
    assert rapidocr_local.ocr_pdf(b"fake") is None
    monkeypatch.setattr(
        rapidocr_local, "ocr_png_scored", lambda png: ("some text here", 0.9)
    )
    assert rapidocr_local.ocr_pdf(b"fake") is not None
    # Threshold 0 keeps the old behaviour (never gate on quality).
    monkeypatch.setattr(settings, "rapidocr_min_confidence", 0)
    monkeypatch.setattr(
        rapidocr_local, "ocr_png_scored", lambda png: ("some text here", 0.1)
    )
    assert rapidocr_local.ocr_pdf(b"fake") is not None
