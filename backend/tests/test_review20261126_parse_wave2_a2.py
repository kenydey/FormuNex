"""Wave 2 (P2) A2 — parsing: B1 full.md fallback + gate exemption,
B1 pdf_local word-structure floor, B2 batch resume + shard-budget default
+ usage lock, B3 format_availability + shard-cache parser check.

Review: FormuNex-新一轮全面审查与评分报告 §3 (P1-3, P2-1, P2-2, P2-3,
P2-5, P2-6). Every fix here is probed against a fake — no MinerU key, no
network, no production library.
"""
from __future__ import annotations

import hashlib
import sys
import threading
import types
from dataclasses import dataclass, field

import pytest

fitz = pytest.importorskip("fitz")

from app.config import get_settings
from app.services import hybrid_parse, kb_retrieval_gate, mineru_cloud, mineru_structured, parsing, pdf_local
from app.services.kb_retrieval_gate import gate_ingest_rows, is_garbage_chunk_text
from app.services.mineru_cloud import MinerUBlock, MinerUDocument
from app.services.mineru_structured import full_md_fallback


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_pdf(pages: list[str]) -> bytes:
    """One fitz page per string (empty string = blank page)."""
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    out = doc.tobytes()
    doc.close()
    return out


_LONG_FULL_MD = (
    "# 全文\n\n"
    + "本方法适用于铝合金表面处理，通过多道工序实现耐腐蚀涂层。\n" * 12
    + "结论：该工艺在中性盐雾试验中达到 720 h 无红锈。\n"
)


def _doc(blocks, markdown=_LONG_FULL_MD):
    return MinerUDocument(blocks=list(blocks), markdown=markdown)


def _block(text, page_idx=0):
    return MinerUBlock(type="text", page_idx=page_idx, text=text)


# ── B1 (a): full_md_fallback unit ────────────────────────────────────────────


def test_full_md_fallback_empty_rendering_uses_full_md():
    assert full_md_fallback("", _LONG_FULL_MD) == _LONG_FULL_MD.strip()
    assert full_md_fallback("<!-- page:1 -->\n\n<!-- block:text -->", _LONG_FULL_MD) == _LONG_FULL_MD.strip()


def test_full_md_fallback_sparse_rendering_uses_full_md():
    assert full_md_fallback("短句。", _LONG_FULL_MD) == _LONG_FULL_MD.strip()


def test_full_md_fallback_rich_rendering_keeps_blocks():
    assert full_md_fallback(_LONG_FULL_MD, _LONG_FULL_MD) is None
    assert full_md_fallback("x" * 400, _LONG_FULL_MD) is None


def test_full_md_fallback_empty_full_md_never_fires():
    assert full_md_fallback("", "") is None
    assert full_md_fallback("短句。", "") is None


# ── B1 (a): structured tier ──────────────────────────────────────────────────


@pytest.fixture()
def _structured_stubs(monkeypatch):
    """parse_structured with a stubbed cloud call and local page probe."""
    monkeypatch.setattr(mineru_structured, "_doc_is_clean", lambda content: False)
    monkeypatch.setattr(
        mineru_structured, "_page_text_lengths", lambda content: [120, 130]
    )
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))
    return mineru_cloud


def test_structured_sparse_blocks_fall_back_to_full_md(_structured_stubs, monkeypatch):
    monkeypatch.setattr(
        mineru_cloud, "parse_bytes",
        lambda *a, **k: _doc([_block("短句。", 0), _block("另一句。", 1)]),
    )
    out = mineru_structured.parse_structured(b"%PDF fake")
    assert out is not None
    assert out.used_full_md_fallback is True
    assert out.markdown == _LONG_FULL_MD.strip()
    assert out.full_markdown == _LONG_FULL_MD.strip()
    assert len(out.blocks) == 2  # the blocks themselves are kept
    assert out.missing_pages == []


def test_structured_rich_blocks_keep_rendering(_structured_stubs, monkeypatch):
    rich = "本方法适用于铝合金表面处理，通过多道工序实现耐腐蚀涂层。" * 10
    monkeypatch.setattr(
        mineru_cloud, "parse_bytes",
        lambda *a, **k: _doc([_block(rich, 0), _block(rich, 1)]),
    )
    out = mineru_structured.parse_structured(b"%PDF fake")
    assert out is not None
    assert out.used_full_md_fallback is False
    assert rich[:40] in out.markdown
    assert out.full_markdown == _LONG_FULL_MD.strip()


def test_structured_no_blocks_uses_full_md(_structured_stubs, monkeypatch):
    monkeypatch.setattr(mineru_cloud, "parse_bytes", lambda *a, **k: _doc([]))
    out = mineru_structured.parse_structured(b"%PDF fake")
    assert out is not None
    assert out.used_full_md_fallback is True
    assert out.markdown == _LONG_FULL_MD.strip()
    assert out.blocks == []


def test_structured_no_blocks_no_full_md_returns_none(_structured_stubs, monkeypatch):
    monkeypatch.setattr(
        mineru_cloud, "parse_bytes", lambda *a, **k: _doc([], markdown="")
    )
    assert mineru_structured.parse_structured(b"%PDF fake") is None


def test_structured_fallback_keeps_missing_page_placeholder(
    _structured_stubs, monkeypatch
):
    # Blocks on pages 1 and 3 of a 3-page doc: page 2 is missing (P1-2) and
    # the rendering is sparse, so the fallback fires — but the p.2
    # placeholder must survive the swap, not vanish with the rendering.
    # (Two blocks with distinct page_idx: a single page-0 block would take
    # the P2-4 all-zero branch and skip the coverage check entirely.)
    monkeypatch.setattr(
        mineru_structured, "_page_text_lengths", lambda content: [120, 130, 140]
    )
    monkeypatch.setattr(
        mineru_cloud, "parse_bytes",
        lambda *a, **k: _doc([_block("短句。", 0), _block("短句。", 2)]),
    )
    out = mineru_structured.parse_structured(b"%PDF fake")
    assert out is not None
    assert out.used_full_md_fallback is True
    assert out.missing_pages == [2]
    assert "第 2 页 MinerU 未返回内容" in out.markdown


# ── B1 (a): hybrid whole-scan path ───────────────────────────────────────────


def _stub_hybrid_doc(monkeypatch, document):
    monkeypatch.setattr(mineru_cloud, "parse_bytes", lambda *a, **k: document)
    monkeypatch.setattr(hybrid_parse, "_sample_document_language", lambda content: None)
    monkeypatch.setattr(mineru_cloud, "mineru_available", lambda: (True, ""))


def _scan_pages():
    return [
        pdf_local.LocalPage(page_no=1, markdown="", char_count=0),
        pdf_local.LocalPage(page_no=2, markdown="", char_count=0),
    ]


def test_hybrid_scanned_sparse_blocks_fall_back_to_full_md(monkeypatch):
    _stub_hybrid_doc(
        monkeypatch, _doc([_block("短句。", 0), _block("另一句。", 1)])
    )
    out = hybrid_parse._parse_scanned(b"%PDF fake", _scan_pages())
    assert out == _LONG_FULL_MD.strip()


def test_hybrid_scanned_no_blocks_uses_full_md(monkeypatch):
    _stub_hybrid_doc(monkeypatch, _doc([]))
    out = hybrid_parse._parse_scanned(b"%PDF fake", _scan_pages())
    assert out == _LONG_FULL_MD.strip()


def test_hybrid_scanned_no_blocks_no_full_md_returns_none(monkeypatch):
    _stub_hybrid_doc(monkeypatch, _doc([], markdown=""))
    assert hybrid_parse._parse_scanned(b"%PDF fake", _scan_pages()) is None


def test_hybrid_scanned_rich_blocks_keep_rendering(monkeypatch):
    rich = "本方法适用于铝合金表面处理，通过多道工序实现耐腐蚀涂层。" * 10
    _stub_hybrid_doc(monkeypatch, _doc([_block(rich, 0), _block(rich, 1)]))
    out = hybrid_parse._parse_scanned(b"%PDF fake", _scan_pages())
    assert out is not None
    assert rich[:40] in out
    assert out != _LONG_FULL_MD.strip()


# ── B1 (a): gate exemption for MinerU structured short chunks ───────────────


SHORT_SENTENCE = "本方法适用于铝合金表面处理。"


@pytest.fixture()
def _gate_on(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "content_filter_enabled", True, raising=False)
    monkeypatch.setattr(settings, "content_filter_min_snippet_chars", 40, raising=False)


def test_gate_drops_short_sentence_by_default_keeps_it_structured(_gate_on):
    assert is_garbage_chunk_text(SHORT_SENTENCE) is True
    assert is_garbage_chunk_text(SHORT_SENTENCE, mineru_structured=True) is False


def test_gate_structured_floor_still_rejects_fragments_and_soup(_gate_on):
    # Below the 8-char structured floor, and symbol soup above it: both stay garbage.
    assert is_garbage_chunk_text("适用于铝", mineru_structured=True) is True
    assert is_garbage_chunk_text("！！！…………", mineru_structured=True) is True
    assert is_garbage_chunk_text("a - - - - - - - - b", mineru_structured=True) is True


def test_gate_ingest_rows_structured_keeps_short_sentences(_gate_on):
    rows = [{"text": SHORT_SENTENCE, "block_type": "text"}]
    kept, _ = gate_ingest_rows(rows, source_id=None)
    assert kept == []
    kept, reason = gate_ingest_rows(rows, source_id=None, mineru_structured=True)
    assert len(kept) == 1 and reason is None


def test_gate_explicit_min_chars_wins_over_structured_floor(_gate_on):
    assert is_garbage_chunk_text(SHORT_SENTENCE, min_chars=20, mineru_structured=True) is True
    assert is_garbage_chunk_text(SHORT_SENTENCE, min_chars=8, mineru_structured=True) is False


# ── B1 (b): pdf_local word-structure floor ───────────────────────────────────


def test_has_word_run():
    assert pdf_local.has_word_run("你好世界") is True
    assert pdf_local.has_word_run("ab") is True
    assert pdf_local.has_word_run("a1") is True
    assert pdf_local.has_word_run("5") is False
    assert pdf_local.has_word_run("…") is False
    assert pdf_local.has_word_run("") is False


def test_pdf_page_text_stats_uses_word_runs_not_20_chars():
    # Page 1 holds 11 real chars (was "blank" under the 20-char floor);
    # page 2 is blank; page 3 holds only a lone digit (still blank).
    # Latin text: fitz's default font cannot render CJK, so get_text()
    # would come back empty for Chinese and the probe would lie.
    content = _make_pdf(["Hello world", "", "7"])
    assert parsing._pdf_page_text_stats(content) == (3, 2)


def test_extract_pages_short_text_layer_falls_back(monkeypatch):
    """Layout returns empty markdown for a page whose text layer is one
    short real phrase: the B-1 fallback must restore it (was: >= 20 chars
    or nothing). pymupdf4llm is stubbed — the real library is absent here.
    """
    pymupdf = pytest.importorskip("pymupdf")
    fake = types.ModuleType("pymupdf4llm")
    fake._use_layout = True
    fake.use_layout = lambda wanted: None
    fake.to_markdown = lambda doc, **kwargs: [{"text": ""}]
    monkeypatch.setitem(sys.modules, "pymupdf4llm", fake)
    monkeypatch.setattr(pdf_local, "_layout_configured", False)
    monkeypatch.setattr(get_settings(), "pdf_layout_analysis", True, raising=False)
    # Latin text: fitz's default font cannot render CJK, so get_text()
    # would come back empty for Chinese and the probe would lie.
    content = _make_pdf(["Hello world"])
    pages = pdf_local.extract_pages(content)
    assert pages is not None and len(pages) == 1
    assert pages[0].markdown == "Hello world"
    # Control: a lone digit is not a word run — no fallback, still empty.
    content = _make_pdf(["7"])
    pages = pdf_local.extract_pages(content)
    assert pages is not None and pages[0].markdown == ""


# ── B2: batch pending resume ─────────────────────────────────────────────────


@dataclass
class _FakeImage:
    name: str = ""
    data: bytes = b""
    path: str = ""


@dataclass
class _FakeResult:
    state: str = "done"
    markdown: str | None = "# Parsed\n\nbody"
    content_list: list[dict] | None = None
    images: list[_FakeImage] = field(default_factory=list)
    error: str | None = None
    task_id: str = "t-1"


def _make_batch_sdk(*, with_submit_batch: bool = True):
    """Fake `mineru` module with submit_batch/get_batch resume primitives."""
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

    @dataclass
    class FileParam:
        pages: str = ""
        ocr: bool | None = None
        data_id: str = ""

    module.FileParam = FileParam
    module.calls = []
    module.submit_calls = []
    module.submit_batch_calls = []
    module.get_batch_calls = []
    module.submit_batch_id = "b-1"
    module.batch_size = 0
    module.batch_texts = ["PAGE-A", "PAGE-B"]
    module.get_batch_script = None  # list["processing" | "done" | Exception]

    def _done_results():
        return [
            _FakeResult(
                content_list=[
                    {"type": "text", "page_idx": 0, "text": text}
                ]
            )
            for text in module.batch_texts[: module.batch_size]
        ]

    class _Client:
        def __init__(self, token=None, base_url=None):
            module.calls.append({"token": token, "base_url": base_url})

        def submit(self, source, **kwargs):
            module.submit_calls.append(source)
            return module.submit_batch_id

        if with_submit_batch:

            def submit_batch(self, sources, **kwargs):
                module.submit_batch_calls.append(list(sources))
                module.last_batch_kwargs = kwargs
                module.batch_size = len(sources)
                return module.submit_batch_id

        def get_batch(self, batch_id):
            module.get_batch_calls.append(batch_id)
            if module.get_batch_script is not None:
                step = (
                    module.get_batch_script.pop(0)
                    if module.get_batch_script
                    else "done"
                )
                if isinstance(step, Exception):
                    raise step
                if step == "processing":
                    return [
                        _FakeResult(state="processing")
                        for _ in range(module.batch_size)
                    ]
                return _done_results()
            return _done_results()

        def extract_batch(self, sources, **kwargs):
            module.batch_calls = getattr(module, "batch_calls", [])
            module.batch_calls.append(list(sources))
            module.last_batch_kwargs = kwargs
            module.batch_size = len(sources)
            return iter(_done_results())

    module.MinerU = _Client
    return module


@pytest.fixture()
def _batch_sdk(monkeypatch, tmp_path):
    module = _make_batch_sdk()
    monkeypatch.setitem(sys.modules, "mineru", module)
    monkeypatch.setattr(mineru_cloud, "optional_import", lambda name: name == "mineru")
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "test-token", raising=False)
    monkeypatch.setattr(settings, "prune_mineru_cache", False, raising=False)
    monkeypatch.setattr(settings, "mineru_cache_dir", str(tmp_path / "cache"), raising=False)
    return module


def _pending_files():
    from pathlib import Path

    return list((Path(get_settings().mineru_cache_dir) / "pending").glob("*.json"))


def test_batch_timeout_leaves_pending_and_resume_picks_it_up(_batch_sdk):
    """First batch call times out (processing past a 0s budget) and saves the
    pending batch; the second call resumes it and submits nothing new."""
    sdk = _batch_sdk
    sdk.get_batch_script = ["processing"]
    pages = [b"%PDF page-a", b"%PDF page-b"]
    assert mineru_cloud.parse_pages_batch(pages, timeout=0.1) == [None, None]
    assert len(sdk.submit_batch_calls) == 1
    assert len(_pending_files()) == 1

    sdk.get_batch_script = ["done"]
    docs = mineru_cloud.parse_pages_batch(pages)
    assert len(docs) == 2 and all(d is not None for d in docs)
    assert docs[0].blocks[0].text == "PAGE-A"
    assert docs[1].blocks[0].text == "PAGE-B"
    assert len(sdk.submit_batch_calls) == 1  # resumed, not resubmitted
    assert sdk.get_batch_calls[-1] == "b-1"
    assert _pending_files() == []


def test_batch_expired_pending_resubmits(_batch_sdk):
    sdk = _batch_sdk
    sdk.get_batch_script = ["processing"]
    pages = [b"%PDF expire-a", b"%PDF expire-b"]
    assert mineru_cloud.parse_pages_batch(pages, timeout=0.1) == [None, None]
    assert len(sdk.submit_batch_calls) == 1

    sdk.get_batch_script = [sdk.TaskNotFoundError("gone"), "done"]
    docs = mineru_cloud.parse_pages_batch(pages)
    assert len(docs) == 2 and all(d is not None for d in docs)
    assert len(sdk.submit_batch_calls) == 2


def test_batch_without_submit_batch_falls_back_to_extract_batch(monkeypatch, tmp_path):
    """An SDK (or double) without submit_batch still parses via the old
    blocking extract_batch — resume is skipped, parsing is not broken."""
    module = _make_batch_sdk(with_submit_batch=False)
    monkeypatch.setitem(sys.modules, "mineru", module)
    monkeypatch.setattr(mineru_cloud, "optional_import", lambda name: name == "mineru")
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    monkeypatch.setattr(settings, "mineru_api_key", "test-token", raising=False)
    monkeypatch.setattr(settings, "prune_mineru_cache", False, raising=False)
    monkeypatch.setattr(settings, "mineru_cache_dir", str(tmp_path / "cache"), raising=False)
    docs = mineru_cloud.parse_pages_batch([b"%PDF legacy-a", b"%PDF legacy-b"])
    assert len(docs) == 2 and all(d is not None for d in docs)
    assert docs[0].blocks[0].text == "PAGE-A"
    assert len(module.batch_calls) == 1


# ── B2: usage counter lock + shard-budget default ────────────────────────────


def test_record_usage_counts_concurrently_without_loss(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_cache_dir", str(tmp_path / "cache"), raising=False)
    n_threads, n_calls = 8, 25
    barrier = threading.Barrier(n_threads)

    def _worker():
        barrier.wait()
        for _ in range(n_calls):
            mineru_cloud._record_usage(pages=1, ok=True)

    threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    summary = mineru_cloud.daily_usage_summary()
    assert summary == {
        "pages": n_threads * n_calls,
        "calls": n_threads * n_calls,
        "failed": 0,
        "quota_errors": 0,
    }


def test_shard_budget_default_aligns_with_max_pages_per_doc():
    fields = type(get_settings()).model_fields
    assert fields["mineru_shard_budget"].default == 10
    assert fields["mineru_max_pages_per_doc"].default == 10


def test_sharded_default_budget_caps_mineru_calls(monkeypatch):
    """Default budget (10) caps MinerU tier calls across shards: a 120-page
    doc (12 shards) parses 10 shards and reports the last 2 as failed."""
    from app.db.database import create_all_metadata, default_engine

    create_all_metadata(default_engine())
    settings = get_settings()
    monkeypatch.setattr(settings, "pdf_shard_enabled", True, raising=False)
    monkeypatch.setattr(settings, "pdf_shard_pages", 10, raising=False)
    monkeypatch.setattr(settings, "pdf_shard_resume", True, raising=False)
    monkeypatch.setattr(settings, "pdf_shard_stale_days", 7, raising=False)
    # NB: mineru_shard_budget is deliberately NOT overridden — the default (10) applies.

    def _mineru(content, ext):
        return "MINERU <!-- page:1 --> body"

    monkeypatch.setattr(
        parsing, "_pdf_tier_order", lambda prefer: [("mineru", _mineru)]
    )
    content = _make_pdf([f"page {i} body text " * 20 for i in range(120)])
    result = parsing.parse_document(content, "pdf")
    assert result.parser == "sharded"
    assert result.table_stats["parsed_pages"] == 100
    assert result.table_stats["failed_shards"] == ["101-110", "111-120"]


# ── B3: format_availability + shard-cache parser ─────────────────────────────


def test_format_availability_recognises_mineru_only(monkeypatch):
    monkeypatch.setattr(
        parsing, "optional_import", lambda name: name == "mineru"
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_enabled", True, raising=False)
    assert parsing.format_availability()["pdf"] is True
    assert parsing.can_parse("pdf") is True
    monkeypatch.setattr(settings, "mineru_enabled", False, raising=False)
    assert parsing.format_availability()["pdf"] is False
    assert parsing.can_parse("pdf") is False


def test_shard_cache_passes_pinned_parser(monkeypatch):
    """A shard cached under another parser is a miss when a tier is pinned,
    and a hit under auto (where the winning tier is content-dependent)."""
    from app.db.database import create_all_metadata, default_engine
    from app.services.parse_shards import content_sha256, put_shard

    create_all_metadata(default_engine())
    settings = get_settings()
    monkeypatch.setattr(settings, "pdf_shard_enabled", True, raising=False)
    monkeypatch.setattr(settings, "pdf_shard_pages", 10, raising=False)
    monkeypatch.setattr(settings, "pdf_shard_resume", True, raising=False)
    monkeypatch.setattr(settings, "pdf_shard_stale_days", 7, raising=False)
    monkeypatch.setattr(settings, "mineru_shard_budget", 0, raising=False)
    calls: list[int] = []

    def _fake(content, ext):
        n = fitz.open(stream=content, filetype="pdf").page_count
        calls.append(n)
        return "FRESH <!-- page:1 --> body"

    monkeypatch.setattr(
        parsing, "_pdf_tier_order", lambda prefer: [("markitdown", _fake)]
    )
    content = _make_pdf([f"page {i} body text " * 20 for i in range(12)])
    sha = content_sha256(content)

    # Pinned tier "mineru" != cached parser "markitdown": miss, re-parse.
    assert put_shard(sha, 0, 1, 10, "markitdown", "CACHED <!-- page:1 --> body")
    result = parsing.parse_document(content, "pdf", prefer="mineru")
    assert "FRESH" in result.markdown and "CACHED" not in result.markdown
    assert calls == [10, 2]

    # Auto: no parser expectation, the cached row is a hit.
    assert put_shard(sha, 0, 1, 10, "markitdown", "CACHED <!-- page:1 --> body")
    calls.clear()
    result = parsing.parse_document(content, "pdf", prefer="auto")
    assert "CACHED" in result.markdown
    assert calls == [2]  # only the uncached second shard was parsed
