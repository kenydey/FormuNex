"""Stage 5 / SR-086: shard bookmarks survive the extras_json cache round-trip."""

from __future__ import annotations

import fitz  # type: ignore
import pytest

from app.config import get_settings
from app.services import parsing
from app.services.parse_shards import content_sha256, put_shard
from app.services.parsing import ParseResult


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
    from app.db.database import create_all_metadata, default_engine

    create_all_metadata(default_engine())
    yield


def _extras_with_bookmarks(bookmarks):
    res = ParseResult("body", "markitdown")
    res.bookmarks = bookmarks
    return parsing._serialize_extras(res)


def test_extras_roundtrip_restores_bookmarks_as_tuples():
    bookmarks = [("Chapter 1", 1, 3), ("Section 1.1", 2, 5)]
    tables, blocks, formulas, restored = [], [], [], []
    parsing._restore_extras(_extras_with_bookmarks(bookmarks), tables, blocks, formulas, restored)
    assert restored == bookmarks
    assert all(isinstance(b, tuple) for b in restored)


def test_restore_tolerates_old_cache_without_bookmarks_and_bad_entries():
    tables, blocks, formulas, restored = [], [], [], []
    parsing._restore_extras("{}", tables, blocks, formulas, restored)
    assert restored == []
    parsing._restore_extras(
        '{"bookmarks": [["Good", 1, 2], ["Bad"], [1, 2]]}',
        tables, blocks, formulas, restored,
    )
    assert restored == [("Good", 1, 2)]


def test_cached_shards_restore_bookmarks_when_flag_off(monkeypatch, _tables):
    settings = get_settings()
    monkeypatch.setattr(settings, "pdf_shard_enabled", True)
    monkeypatch.setattr(settings, "pdf_shard_pages", 10)
    monkeypatch.setattr(settings, "pdf_shard_resume", True)
    monkeypatch.setattr(settings, "pdf_shard_stale_days", 7)
    monkeypatch.setattr(settings, "mineru_shard_budget", 0)
    # The residual: with the whole-document re-read flag OFF, a resumed
    # run used to lose every shard bookmark (cached branch restored no
    # bookmarks at all). The tier is never consulted for cached shards.
    monkeypatch.setattr(settings, "pdf_bookmark_headings_enabled", False)
    monkeypatch.setattr(parsing, "_pdf_tier_order", lambda prefer: [("markitdown", lambda c, e: None)])

    content = _make_pdf(12)
    sha = content_sha256(content)
    assert put_shard(sha, 0, 1, 10, "markitdown", "SHARD-A body",
                     _extras_with_bookmarks([("Chapter 1", 1, 2)]))
    assert put_shard(sha, 1, 11, 12, "markitdown", "SHARD-B body",
                     _extras_with_bookmarks([("Chapter 2", 1, 11)]))
    result = parsing.parse_document(content, "pdf")
    assert result.parser == "sharded"
    assert result.bookmarks == [("Chapter 1", 1, 2), ("Chapter 2", 1, 11)]
