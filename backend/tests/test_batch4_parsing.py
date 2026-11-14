"""Batch 4 (parsing/RAG/vision) regression tests.

Each test pins one batch-4 fix at the layer where it lives, so a future
refactor that reintroduces the old behaviour fails here first:

* parsing.py — P1-B1 challenger must pass the gate itself; P2-B1 the gate
  page count no longer depends on routing being enabled.
* hybrid_parse.py — P1-B4 escalation adoption, P1-B8 scanned density gate,
  P2-B7 list blocks are not headings, P2-B11 language votes, P2-B12 empty
  equations and page-index collapse.
* mineru_cloud.py — P1-B5 per-document isolation + field fallback, P1-B6
  options in the cache key, P2-B8 batch alignment, P2-B9 cache version/TTL
  and per-page image keys.
* mineru_structured.py — P1-B9 empty-text formula skip + count-only regex
  catches, P2-B10 furniture/figure/empty-block alignment.
* pdf_local.py — P1-B10 failed pages stay visible, P2-B6 short real text is
  not a scan.
* parse_shards.py — P1-B7 parser/range validation and empty-markdown refusal.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.services import (
    hybrid_parse,
    mineru_cloud,
    mineru_structured,
    parse_shards,
    parsing,
    pdf_local,
)
from app.services.chunking import block_marker, page_marker


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


def _gate_settings(monkeypatch, *, routing: bool):
    settings = get_settings()
    monkeypatch.setattr(settings, "pdf_routing_enabled", routing, raising=False)
    monkeypatch.setattr(settings, "pdf_quality_gate_enabled", True, raising=False)
    monkeypatch.setattr(settings, "pdf_quality_gate_min_chars_per_page", 50, raising=False)


# ── parsing.py: P1-B1 / P2-B1 ──────────────────────────────────────────────


def test_p1_b1_challenger_failing_gate_keeps_hybrid(monkeypatch):
    _gate_settings(monkeypatch, routing=True)
    monkeypatch.setattr(parsing, "_mineru_usable", lambda: True)
    hybrid_text = "见表 1 的结果如下。\n\n一些文字描述，没有管道表格。" + "x" * 200
    monkeypatch.setattr(parsing, "_parse_hybrid", lambda c: hybrid_text)
    monkeypatch.setattr(
        parsing, "_parse_mineru",
        lambda c: parsing.ParseResult("hi", "mineru"),
    )
    result = parsing.parse_document(_pdf_bytes(["hello world " * 20]), "pdf")
    assert result.parser == "hybrid"
    assert result.markdown == hybrid_text


def test_p1_b1_tabular_but_sparse_challenger_rejected(monkeypatch):
    # Density is part of the gate: a challenger with a table but only
    # ~30 chars on 1 page (30 < 50) must not replace the hybrid result.
    _gate_settings(monkeypatch, routing=True)
    monkeypatch.setattr(parsing, "_mineru_usable", lambda: True)
    hybrid_text = "见表 1 的结果如下。\n\n一些文字描述，没有管道表格。" + "x" * 200
    monkeypatch.setattr(parsing, "_parse_hybrid", lambda c: hybrid_text)
    monkeypatch.setattr(
        parsing, "_parse_mineru",
        lambda c: parsing.ParseResult(
            "| 指标 | 值 |\n|---|---|\n| 盐雾 | 1000h |", "mineru"
        ),
    )
    result = parsing.parse_document(_pdf_bytes(["hello world " * 20]), "pdf")
    assert result.parser == "hybrid"
    assert result.markdown == hybrid_text


def test_p2_b1_gate_density_runs_without_routing(monkeypatch):
    _gate_settings(monkeypatch, routing=False)
    monkeypatch.setattr(parsing, "_mineru_usable", lambda: True)
    monkeypatch.setattr(parsing, "_parse_hybrid", lambda c: "x" * 80)
    called = []
    monkeypatch.setattr(
        parsing, "_parse_mineru",
        lambda c: called.append("mineru") or parsing.ParseResult("y" * 500, "mineru"),
    )
    result = parsing.parse_document(_pdf_bytes(["hello world " * 20] * 2), "pdf")
    # 80 chars over 2 pages = 40/page < 50: the gate must fire even though
    # routing is off (route_pages used to stay None and skip density).
    assert called == ["mineru"]
    assert result.parser == "mineru"


def test_p2_b1_gate_pass_without_routing_skips_challenger(monkeypatch):
    _gate_settings(monkeypatch, routing=False)
    monkeypatch.setattr(parsing, "_mineru_usable", lambda: True)
    monkeypatch.setattr(parsing, "_parse_hybrid", lambda c: "x" * 120)
    called = []
    monkeypatch.setattr(
        parsing, "_parse_mineru",
        lambda c: called.append("mineru") or parsing.ParseResult("y" * 500, "mineru"),
    )
    result = parsing.parse_document(_pdf_bytes(["hello world " * 20] * 2), "pdf")
    # 120 chars over 2 pages = 60/page >= 50: gate passes, no challenger.
    assert called == []
    assert result.parser == "hybrid"


# ── hybrid_parse.py: P1-B4 ───────────────────────────────────────────────────


def test_p1_b4_adoption_gate():
    local = "本地正文内容，足够长的一段文字。" * 10
    placeholder = "> [图 · p.1]\n>\n> _（视觉解析未返回结果）_"
    assert hybrid_parse._adopt_upgraded(3, local, placeholder) == (local.strip(), False)
    sparse = "短"
    assert hybrid_parse._adopt_upgraded(3, local, sparse) == (local.strip(), False)
    table_local = "| 指标 | 值 |\n|---|---|\n| 盐雾 | 1000h |" + "x" * 200
    assert hybrid_parse._adopt_upgraded(3, table_local, "y" * 500) == (table_local.strip(), False)
    good = "升级后的完整正文，字符数充足。" * 20
    assert hybrid_parse._adopt_upgraded(3, local, good) == (good.strip(), True)
    assert hybrid_parse._adopt_upgraded(3, "", placeholder) == (placeholder.strip(), True)
    assert hybrid_parse._adopt_upgraded(3, local, "") == (local.strip(), False)


# ── hybrid_parse.py: P2-B7 / P2-B12 ─────────────────────────────────────────


def _block(kind: str, **kw) -> mineru_cloud.MinerUBlock:
    return mineru_cloud.MinerUBlock(type=kind, **kw)


def test_p2_b7_list_block_is_not_a_heading():
    out = hybrid_parse._render_blocks(
        [_block("list", text="1. 试剂A\n2. 试剂B")], page_label="p.1"
    )
    assert "# 1. 试剂A" not in out
    assert "试剂A" in out and "试剂B" in out


def test_p2_b7_multiline_text_block_is_not_a_heading():
    out = hybrid_parse._render_blocks(
        [_block("text", text="1. 引言\n这是正文第二行", text_level=0)], page_label="p.1"
    )
    assert not out.startswith("# 1.")
    out2 = hybrid_parse._render_blocks(
        [_block("text", text="1. 引言", text_level=0)], page_label="p.1"
    )
    assert out2.startswith("# 1. 引言")


def test_p2_b12_empty_equation_leaves_a_marker():
    out = hybrid_parse._render_blocks(
        [_block("equation", text="  ")], page_label="p.1"
    )
    assert "公式块内容为空" in out


def test_p2_b12_page_index_collapse_renders_in_order(monkeypatch):
    doc = mineru_cloud.MinerUDocument(
        blocks=[
            _block("text", page_idx=0, text="第一块"),
            _block("text", page_idx=0, text="第二块"),
        ]
    )
    monkeypatch.setattr(mineru_cloud, "parse_bytes", lambda *a, **k: doc)
    pages = [
        pdf_local.LocalPage(page_no=1, markdown="", char_count=0),
        pdf_local.LocalPage(page_no=2, markdown="", char_count=0),
    ]
    out = hybrid_parse._parse_scanned(b"%PDF", pages)
    assert out is not None
    assert "第一块" in out and "第二块" in out
    # Collapse path renders in document order without per-page markers.
    assert page_marker(1) not in out and page_marker(2) not in out


def test_p2_b12_distinct_pages_keep_markers(monkeypatch):
    doc = mineru_cloud.MinerUDocument(
        blocks=[
            _block("text", page_idx=0, text="第一页"),
            _block("text", page_idx=1, text="第二页"),
        ]
    )
    monkeypatch.setattr(mineru_cloud, "parse_bytes", lambda *a, **k: doc)
    pages = [
        pdf_local.LocalPage(page_no=1, markdown="", char_count=0),
        pdf_local.LocalPage(page_no=2, markdown="", char_count=0),
    ]
    out = hybrid_parse._parse_scanned(b"%PDF", pages)
    assert out is not None
    assert page_marker(1) in out and page_marker(2) in out


# ── hybrid_parse.py: P1-B8 / P2-B11 ─────────────────────────────────────────


def test_p1_b8_scanned_density_gate():
    assert hybrid_parse._scanned_density_ok("x" * 300, 100) is False
    assert hybrid_parse._scanned_density_ok("x" * 5000, 100) is True
    assert hybrid_parse._scanned_density_ok("x" * 10, None) is True
    assert hybrid_parse._scanned_density_ok("", 5) is False


def _scan_env(monkeypatch, *, votes, tesseract_text, rapid_text, n_pages):
    from app.services import rapidocr_local, tesseract_local

    monkeypatch.setattr(tesseract_local, "tesseract_available", lambda: True)
    monkeypatch.setattr(pdf_local, "page_count", lambda content: n_pages)
    monkeypatch.setattr(pdf_local, "page_as_png", lambda *a, **k: b"png")
    vote_iter = iter(votes)

    def _scored(png):
        try:
            return next(vote_iter), 0.9
        except StopIteration:
            return "", 0.0

    monkeypatch.setattr(rapidocr_local, "ocr_png_scored", _scored)
    monkeypatch.setattr(tesseract_local, "ocr_pdf", lambda *a, **k: tesseract_text)
    monkeypatch.setattr(rapidocr_local, "ocr_pdf", lambda *a, **k: rapid_text)
    monkeypatch.setattr(hybrid_parse, "_scanned_vision_fallback", lambda c: None)
    monkeypatch.setattr(pdf_local, "ocr_markdown", lambda *a, **k: None)


def test_p2_b11_single_english_vote_falls_back_to_rapidocr(monkeypatch):
    _scan_env(
        monkeypatch,
        votes=["This is an English page with plenty of words."],
        tesseract_text="eng output " * 50,
        rapid_text="rapid output " * 50,
        n_pages=3,
    )
    assert hybrid_parse._scanned_without_cloud(b"%PDF") == "rapid output " * 50


def test_p1_b8_sparse_tesseract_output_falls_back_to_rapidocr(monkeypatch):
    _scan_env(
        monkeypatch,
        votes=["English page one.", "English page two."],
        tesseract_text="ab",
        rapid_text="rapid output " * 50,
        n_pages=3,
    )
    assert hybrid_parse._scanned_without_cloud(b"%PDF") == "rapid output " * 50


def test_p1_b8_sparse_rapidocr_output_is_not_shipped(monkeypatch):
    from app.services import rapidocr_local, tesseract_local

    monkeypatch.setattr(tesseract_local, "tesseract_available", lambda: False)
    monkeypatch.setattr(pdf_local, "page_count", lambda content: 100)
    monkeypatch.setattr(rapidocr_local, "ocr_pdf", lambda *a, **k: "x" * 300)
    monkeypatch.setattr(hybrid_parse, "_scanned_vision_fallback", lambda c: None)
    monkeypatch.setattr(pdf_local, "ocr_markdown", lambda *a, **k: None)
    assert hybrid_parse._scanned_without_cloud(b"%PDF") is None


# ── mineru_cloud.py: P1-B6 / P2-B9 ───────────────────────────────────────────


def test_p1_b6_cache_key_covers_options():
    a = mineru_cloud._cache_key(b"doc", ext="pdf", ocr=False, formula=True, table=True)
    b = mineru_cloud._cache_key(b"doc", ext="pdf", ocr=False, formula=False, table=True)
    c = mineru_cloud._cache_key(b"doc", ext="pdf", ocr=False, formula=True, table=False)
    d = mineru_cloud._cache_key(b"doc", ext="pdf", ocr=True, formula=True, table=True)
    assert len({a, b, c, d}) == 4


def _cache_settings(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_cache_dir", str(tmp_path / "cache"), raising=False)
    monkeypatch.setattr(settings, "prune_mineru_cache", False, raising=False)


def _doc_with_images():
    return mineru_cloud.MinerUDocument(
        blocks=[
            mineru_cloud.MinerUBlock(type="image", page_idx=0, image_path="fig.png", image=b"page0"),
            mineru_cloud.MinerUBlock(type="image", page_idx=1, image_path="fig.png", image=b"page1"),
        ],
        markdown="doc",
    )


def test_p2_b9_per_page_image_keys_survive_roundtrip(monkeypatch, tmp_path):
    _cache_settings(monkeypatch, tmp_path)
    key = mineru_cloud._cache_key(b"doc", ext="pdf", ocr=False, formula=True, table=True)
    mineru_cloud._cache_store(key, _doc_with_images())
    loaded = mineru_cloud._cache_load(key)
    assert loaded is not None
    images = [b.image for b in loaded.blocks]
    assert images == [b"page0", b"page1"]


def test_p2_b9_version_mismatch_is_a_miss(monkeypatch, tmp_path):
    _cache_settings(monkeypatch, tmp_path)
    key = mineru_cloud._cache_key(b"doc", ext="pdf", ocr=False, formula=True, table=True)
    mineru_cloud._cache_store(key, _doc_with_images())
    path = Path(get_settings().mineru_cache_dir) / key / "doc.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["version"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert mineru_cloud._cache_load(key) is None


def test_p2_b9_expired_cache_is_a_miss(monkeypatch, tmp_path):
    _cache_settings(monkeypatch, tmp_path)
    key = mineru_cloud._cache_key(b"doc", ext="pdf", ocr=False, formula=True, table=True)
    mineru_cloud._cache_store(key, _doc_with_images())
    path = Path(get_settings().mineru_cache_dir) / key / "doc.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["written_at"] = 0
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert mineru_cloud._cache_load(key) is None


# ── mineru_cloud.py: P1-B5 ───────────────────────────────────────────────────


def test_p1_b5_malformed_page_fields_fall_back_to_zero(caplog):
    class _Result:
        content_list = [
            {"type": "text", "page_idx": "abc", "text_level": "x", "text": "hello"},
            {"type": "text", "page_idx": 2, "text": "world"},
        ]
        markdown = "doc"

    import logging

    with caplog.at_level(logging.WARNING, logger="app.services.mineru_cloud"):
        doc = mineru_cloud._normalise(_Result(), {})
    assert doc.blocks[0].page_idx == 0
    assert doc.blocks[0].text_level == 0
    assert doc.blocks[1].page_idx == 2
    assert "unparsable" in caplog.text


def _make_batch_sdk(batch_results):
    module = types.ModuleType("mineru")

    class MinerUError(Exception): ...
    class AuthError(MinerUError): ...
    class QuotaExceededError(MinerUError): ...
    class FileTooLargeError(MinerUError): ...
    class PageLimitError(MinerUError): ...
    class TimeoutError_(MinerUError): ...
    class TaskNotFoundError(MinerUError): ...

    module.MinerUError = MinerUError
    module.AuthError = AuthError
    module.QuotaExceededError = QuotaExceededError
    module.FileTooLargeError = FileTooLargeError
    module.PageLimitError = PageLimitError
    module.TimeoutError = TimeoutError_
    module.TaskNotFoundError = TaskNotFoundError

    # The real SDK (0.2.5) exports FileParam at the top level; the batch
    # path passes one data_id per page. A fake without it makes every batch
    # submission die with AttributeError before extract_batch is reached.
    from dataclasses import dataclass

    @dataclass
    class FileParam:
        pages: str = ""
        ocr: bool | None = None
        data_id: str = ""

    module.FileParam = FileParam

    class _Client:
        def __init__(self, token=None, base_url=None):
            pass

        def extract_batch(self, sources, **kwargs):
            return iter(batch_results)

    module.MinerU = _Client
    return module


def _batch_env(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "test-token", raising=False)
    monkeypatch.setattr(settings, "prune_mineru_cache", False, raising=False)
    monkeypatch.setattr(settings, "mineru_cache_dir", str(tmp_path / "cache"), raising=False)
    monkeypatch.setattr(mineru_cloud, "optional_import", lambda name: name == "mineru")


class _FakeDoc:
    def __init__(self, content_list):
        self.state = "done"
        self.content_list = content_list
        self.images = []
        self.markdown = "doc"


def test_p2_b8_batch_count_mismatch_discards_batch(monkeypatch, tmp_path):
    _batch_env(monkeypatch, tmp_path)
    monkeypatch.setitem(
        sys.modules, "mineru",
        _make_batch_sdk([_FakeDoc([{"type": "text", "page_idx": 0, "text": "only"}])]),
    )
    results = mineru_cloud.parse_pages_batch([b"%PDF a", b"%PDF b"])
    assert results == [None, None]


def test_p1_b5_malformed_batch_document_keeps_siblings(monkeypatch, tmp_path):
    _batch_env(monkeypatch, tmp_path)

    class _BoomDoc:
        state = "done"
        images = []

        @property
        def content_list(self):
            raise RuntimeError("boom")

        markdown = "doc"

    monkeypatch.setitem(
        sys.modules, "mineru",
        _make_batch_sdk([
            _FakeDoc([{"type": "text", "page_idx": 0, "text": "good"}]),
            _BoomDoc(),
        ]),
    )
    results = mineru_cloud.parse_pages_batch([b"%PDF a", b"%PDF b"])
    assert results[0] is not None
    assert results[0].blocks[0].text == "good"
    assert results[1] is None


# ── mineru_structured.py: P1-B9 / P2-B10 ─────────────────────────────────────


def _sblock(kind, **kw):
    return mineru_structured.StructuredBlock(page_no=kw.pop("page_no", 1), kind=kind, **kw)


def test_p2_b10_render_skips_empty_blocks_and_marks_figures():
    blocks = [
        _sblock("text", text="hello"),
        _sblock("text", text="   "),
        _sblock("figure", caption="反应流程图"),
    ]
    out = mineru_structured.render_markdown(blocks)
    assert out.count(block_marker("text")) == 1
    assert "未做视觉解析" in out
    assert "反应流程图" in out


def test_p2_b10_normalise_drops_page_furniture():
    doc = SimpleNamespace(blocks=[
        mineru_cloud.MinerUBlock(type="header", text="页眉"),
        mineru_cloud.MinerUBlock(type="text", text="正文"),
        mineru_cloud.MinerUBlock(type="footer", text="页脚"),
        mineru_cloud.MinerUBlock(type="page_number", text="1"),
    ])
    kinds = [b.kind for b in mineru_structured._normalise_blocks(doc)]
    assert kinds == ["text"]


def test_p1_b9_llm_fix_skips_empty_text_blocks(monkeypatch):
    from app.services import llm as _llm

    calls = []
    monkeypatch.setattr(
        _llm, "complete_json",
        lambda *a, **k: calls.append(1) or {"latex": "x^2"},
    )
    structured = mineru_structured.MinerUStructured(
        markdown="",
        blocks=[
            _sblock("formula", text="", latex=""),
            _sblock("formula", text="x squared", latex=""),
        ],
    )
    fixed = mineru_structured._llm_fix_formulas(
        structured, [b for b in structured.blocks if not b.latex.strip()]
    )
    assert fixed == 1
    assert len(calls) == 1


# ── pdf_local.py: P1-B10 / P2-B6 ─────────────────────────────────────────────


def test_p2_b6_short_real_text_is_not_a_scan():
    assert pdf_local.LocalPage(page_no=1, markdown="摘要：本品用于金属表面处理。", char_count=30).looks_scanned is False
    assert pdf_local.LocalPage(page_no=1, markdown="1", char_count=1).looks_scanned is True
    assert pdf_local.LocalPage(page_no=1, markdown="", char_count=0).looks_scanned is True
    assert pdf_local.LocalPage(page_no=1, markdown="x" * 10, char_count=10, n_tables=2).looks_scanned is False


def test_p1_b10_all_failed_pages_return_none_from_to_markdown(monkeypatch):
    failed = [pdf_local.LocalPage(page_no=1, markdown="> _（第 1 页本地解析失败，内容缺失）_", failed=True)]
    monkeypatch.setattr(pdf_local, "extract_pages", lambda content: failed)
    assert pdf_local.to_markdown(b"%PDF") is None


def test_p1_b10_failed_page_keeps_marker_in_mixed_doc(monkeypatch):
    pages = [
        pdf_local.LocalPage(page_no=1, markdown="正文", char_count=100),
        pdf_local.LocalPage(page_no=2, markdown="> _（第 2 页本地解析失败，内容缺失）_", failed=True),
    ]
    monkeypatch.setattr(pdf_local, "extract_pages", lambda content: pages)
    out = pdf_local.to_markdown(b"%PDF")
    assert out is not None and "第 2 页本地解析失败" in out


# ── parse_shards.py: P1-B7 ───────────────────────────────────────────────────


@pytest.fixture()
def _tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.db.database import Base, default_session_factory

    db_url = f"sqlite:///{tmp_path}/batch4_shards.db"
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    return db_url


def test_p1_b7_get_shard_validates_parser_and_range(_tables):
    assert parse_shards.put_shard("sha1", 0, 1, 10, "markitdown", "body", "{}")
    assert parse_shards.get_shard("sha1", 0, parser="markitdown", page_start=1, page_end=10) is not None
    assert parse_shards.get_shard("sha1", 0, parser="mineru") is None
    assert parse_shards.get_shard("sha1", 0, page_start=1, page_end=11) is None
    assert parse_shards.get_shard("sha1", 0, page_start=2, page_end=10) is None


def test_p1_b7_put_shard_refuses_empty_markdown(_tables):
    assert parse_shards.put_shard("sha1", 0, 1, 10, "markitdown", "   ", "{}") is False
    assert parse_shards.get_shard("sha1", 0) is None


# ── batch 4 (MinerU): .html/.htm uploads run MinerU-HTML first ─────────────


def _html_doc(markdown: str):
    from types import SimpleNamespace

    return SimpleNamespace(markdown=markdown)


def test_mineru_html_tier_wins_for_html_upload(monkeypatch: pytest.MonkeyPatch):
    """MinerU available → an .html upload is parsed by MinerU-HTML, and the
    tier name records it."""
    monkeypatch.setattr(parsing, "_mineru_usable", lambda: True)
    monkeypatch.setattr(
        mineru_cloud, "parse_bytes", lambda *a, **k: _html_doc("# From MinerU\n\nbody")
    )
    result = parsing.parse_document(b"<html><body><h1>Hi</h1></body></html>", "html")
    assert result.parser == "mineru_html"
    assert "From MinerU" in result.markdown


def test_mineru_html_tier_falls_back_when_unavailable(monkeypatch: pytest.MonkeyPatch):
    """MinerU off → the local text tier converts the HTML, unchanged."""
    monkeypatch.setattr(parsing, "_mineru_usable", lambda: False)
    result = parsing.parse_document(
        b"<html><body><h1>Hi</h1><p>local text</p></body></html>", "html"
    )
    assert result.parser == "text"
    assert "local text" in result.markdown


def test_mineru_html_tier_falls_back_when_mineru_fails(monkeypatch: pytest.MonkeyPatch):
    """MinerU returning nothing → the local text tier, not an empty parse."""
    monkeypatch.setattr(parsing, "_mineru_usable", lambda: True)
    monkeypatch.setattr(mineru_cloud, "parse_bytes", lambda *a, **k: None)
    result = parsing.parse_document(
        b"<html><body><h1>Hi</h1><p>local text</p></body></html>", "htm"
    )
    assert result.parser == "text"
    assert "local text" in result.markdown
