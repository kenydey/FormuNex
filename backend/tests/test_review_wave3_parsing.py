"""Review 2026-11-26 wave 3 (parsing): P3-1, P3-2, P3-4.

P3-1: the sharded-parse tier-call assertion compared cross-thread
execution order; it now compares order-insensitively.
P3-2: the MinerU shard budget was check-then-act across threads and
could overshoot by up to workers-1 calls; the budget is now an
allowance reserved atomically, in shard order, before submit.
P3-4: the language-vote pages (first 3, RapidOCR) were rasterised
once for the vote and once again for the body OCR; each sampled page
is now rasterised once and shared by vote + body.
"""

from __future__ import annotations

import contextvars
import threading
import time

import fitz  # type: ignore
import pytest

from app.config import get_settings
from app.services import hybrid_parse, parsing, pdf_local, rapidocr_local, tesseract_local


def _make_pdf(n_pages: int, tag: str = "") -> bytes:
    doc = fitz.open()
    for i in range(1, n_pages + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i} {tag} body text " * 20)
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


# ── P3-1: shard-completion assertion is order-insensitive ────────────────


def test_sharded_tier_calls_compared_without_order(monkeypatch, _tables):
    """The tier is invoked from pool threads, so a slow first shard is
    legitimately called after the second one. The old ``calls == [10, 2]``
    asserted execution order; the merge order is pinned by the page
    markers, and the call multiset is pinned here."""
    import time as _time

    _shard_settings(monkeypatch, pdf_shard_workers=2)

    def _fake(content, ext):
        doc = fitz.open(stream=content, filetype="pdf")
        first = doc[0].get_text()
        doc.close()
        start = 1 if "page 1 " in first else 11
        if start == 1:
            _time.sleep(0.3)
        return f"SHARD-{start} <!-- page:1 --> body"

    calls: list[int] = []
    _real_fake = _fake

    def _recording(content, ext):
        # Record on completion, not on entry: the point is that shard 2
        # finishes (and is therefore recorded) before shard 1.
        out = _real_fake(content, ext)
        n = fitz.open(stream=content, filetype="pdf").page_count
        calls.append(n)
        return out

    monkeypatch.setattr(
        parsing, "_pdf_tier_order", lambda prefer: [("markitdown", _recording)]
    )
    result = parsing.parse_document(_make_pdf(12), "pdf")
    assert result.parser == "sharded"
    # Shard 1 slept, so shard 2 ran first: execution order is [2, 10].
    assert calls == [2, 10]
    assert sorted(calls) == [2, 10]
    # Merge order is still page order, whatever the execution order was.
    md = result.markdown
    assert md.index("SHARD-1 ") < md.index("SHARD-11 ")
    assert "<!-- page:11 -->" in md


# ── P3-2: MinerU shard budget is an atomic allowance ─────────────────────


def test_apply_mineru_budget_reserves_atomically_under_concurrency():
    """Eight threads racing for a budget of 3: exactly three keep the
    mineru tier and the counter ends at exactly 3 — no overshoot."""
    budget: tuple[int, list[int]] = (3, [0])
    token = parsing._MINERU_BUDGET.set(budget)
    results: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def _worker(ctx):
        def _run():
            barrier.wait()
            kept = parsing._apply_mineru_budget(
                [("hybrid", lambda c, e: None), ("mineru", lambda c, e: "x")]
            )
            with lock:
                results.append(any(name == "mineru" for name, _ in kept))

        ctx.run(_run)

    try:
        contexts = [contextvars.copy_context() for _ in range(8)]
        threads = [
            threading.Thread(target=_worker, args=(ctx,)) for ctx in contexts
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        parsing._MINERU_BUDGET.reset(token)
    assert results.count(True) == 3
    assert results.count(False) == 5
    assert budget[1][0] == 3


def test_sharded_default_budget_deterministic_across_runs(monkeypatch, _tables):
    """The wave-2 jitter test, repeated: a 120-page doc under the
    default budget of 10 must parse exactly 100 pages and fail exactly
    the last two shards, every run — never 120 pages, never a different
    pair of failed shards. Fresh content per run so the shard cache
    cannot leak a previous run's result into the next."""
    _shard_settings(monkeypatch, pdf_shard_workers=4, mineru_shard_budget=10)
    # NB: 10 is the mineru_shard_budget field default (wave-2 test asserts
    # it); passing it explicitly only undoes _shard_settings' budget=0.
    calls: list[str] = []

    def _mineru(content, ext):
        calls.append("mineru")
        return "MINERU <!-- page:1 --> body"

    monkeypatch.setattr(
        parsing, "_pdf_tier_order", lambda prefer: [("mineru", _mineru)]
    )
    for run in range(5):
        calls.clear()
        content = _make_pdf(120, tag=f"run{run}")
        result = parsing.parse_document(content, "pdf")
        assert result.parser == "sharded"
        assert result.table_stats["parsed_pages"] == 100
        assert result.table_stats["failed_shards"] == ["101-110", "111-120"]
        assert len(calls) == 10


def test_mineru_budget_not_exceeded_with_slow_tiers(monkeypatch, _tables):
    """A slow mineru tier widens the old check-then-act window to the
    whole call: without the atomic reserve both shards in a window
    would pass the check and both would call. Budget 2, 3 shards."""
    _shard_settings(monkeypatch, pdf_shard_workers=2, mineru_shard_budget=2)
    calls: list[str] = []

    def _mineru(content, ext):
        calls.append("mineru")
        time.sleep(0.2)
        return "MINERU <!-- page:1 --> body"

    monkeypatch.setattr(
        parsing, "_pdf_tier_order", lambda prefer: [("mineru", _mineru)]
    )
    result = parsing.parse_document(_make_pdf(25), "pdf")
    assert result.parser == "sharded"
    assert len(calls) == 2
    assert result.table_stats["parsed_pages"] == 20
    assert result.table_stats["failed_shards"] == ["21-25"]


# ── P3-4: vote pages and body OCR share one raster ───────────────────────


def _scan_mocks(monkeypatch, *, n_pages, vote_texts, body_texts=None):
    """Drive _scanned_without_cloud with a fake renderer.

    page_as_png returns distinct bytes per (page, dpi) and records every
    render; ocr_png_scored returns the vote texts for the first calls
    and `body_texts` afterwards. Returns the render log."""
    renders: list[tuple[int, int]] = []

    def _fake_png(content, page_no, dpi=150, **kwargs):
        renders.append((page_no, dpi))
        return f"png-{page_no}-{dpi}".encode("utf-8")

    monkeypatch.setattr(pdf_local, "page_count", lambda content: n_pages)
    monkeypatch.setattr(pdf_local, "page_as_png", _fake_png)
    monkeypatch.setattr(tesseract_local, "tesseract_available", lambda: True)
    monkeypatch.setattr(rapidocr_local, "rapidocr_available", lambda: (True, ""))
    vote_iter = iter(vote_texts)
    body_iter = iter(body_texts or [])
    scored_calls: list[bytes] = []

    def _scored(png):
        scored_calls.append(png)
        try:
            return next(vote_iter), 0.9
        except StopIteration:
            try:
                return next(body_iter), 0.9
            except StopIteration:
                return "", 0.0

    monkeypatch.setattr(rapidocr_local, "ocr_png_scored", _scored)
    monkeypatch.setattr(hybrid_parse, "_scanned_vision_fallback", lambda c: None)
    monkeypatch.setattr(pdf_local, "ocr_markdown", lambda *a, **k: None)
    return renders, scored_calls


def test_vote_rasters_reused_by_rapidocr_body(monkeypatch):
    """Chinese votes lose the Tesseract route: the RapidOCR body must
    take pages 1-3 from the vote results and render only pages 4-5.
    Before the fix the body re-rendered pages 1-3 (8 renders total)."""
    renders, scored = _scan_mocks(
        monkeypatch,
        n_pages=5,
        vote_texts=["中文投票内容第一页 " * 20] * 3,
        body_texts=["中文正文内容 " * 20] * 2,
    )
    tess_calls: list[bytes] = []
    monkeypatch.setattr(
        tesseract_local, "ocr_png", lambda png, lang="eng": tess_calls.append(png)
    )
    out = hybrid_parse._scanned_without_cloud(b"%PDF scan")
    assert out is not None
    assert "中文投票内容第一页" in out and "中文正文内容" in out
    # Each page rendered exactly once across vote + body.
    assert sorted(p for p, _ in renders) == [1, 2, 3, 4, 5]
    assert len(renders) == 5
    # Vote OCR ran 3x; body OCR ran only for the 2 pages the vote never saw.
    assert len(scored) == 5
    # No Tesseract body at all: the vote said Chinese.
    assert tess_calls == []


def test_vote_rasters_reused_by_tesseract_body(monkeypatch):
    """English votes win: the Tesseract body must be fed the vote PNGs
    for pages 1-3 and render only pages 4-5 — same bytes, same DPI."""
    renders, scored = _scan_mocks(
        monkeypatch,
        n_pages=5,
        vote_texts=["This is an English page with many words. "] * 3,
    )
    tess_pngs: list[bytes] = []
    monkeypatch.setattr(
        tesseract_local,
        "ocr_png",
        lambda png, lang="eng": tess_pngs.append(png)
        or "english body text " * 25,
    )
    out = hybrid_parse._scanned_without_cloud(b"%PDF scan")
    assert out is not None
    assert "english body text" in out
    # Each page rendered exactly once across vote + body.
    assert sorted(p for p, _ in renders) == [1, 2, 3, 4, 5]
    assert len(renders) == 5
    # Tesseract ran once per page, on the vote bytes for pages 1-3.
    assert tess_pngs == [b"png-1-120", b"png-2-120", b"png-3-120", b"png-4-120", b"png-5-120"]
    # The RapidOCR vote ran 3x and never again: no RapidOCR body ran.
    assert len(scored) == 3


def test_tesseract_body_rerenders_when_vote_dpi_differs(monkeypatch):
    """A vote rendered at rapidocr_dpi != 120 must NOT be handed to
    Tesseract at the wrong resolution: pages 1-3 are rendered fresh at
    120, exactly as tesseract_local.ocr_pdf did before."""
    settings = get_settings()
    monkeypatch.setattr(settings, "rapidocr_dpi", 150)
    renders, _scored = _scan_mocks(
        monkeypatch,
        n_pages=5,
        vote_texts=["This is an English page with many words. "] * 3,
    )
    tess_pngs: list[bytes] = []
    monkeypatch.setattr(
        tesseract_local,
        "ocr_png",
        lambda png, lang="eng": tess_pngs.append(png)
        or "english body text " * 25,
    )
    out = hybrid_parse._scanned_without_cloud(b"%PDF scan")
    assert out is not None and "english body text" in out
    # Vote: 3 renders at 150; body: 5 renders at 120 (pages 1-3 twice).
    assert sorted(renders) == sorted(
        [(1, 150), (2, 150), (3, 150)]
        + [(p, 120) for p in (1, 2, 3, 4, 5)]
    )
    assert all(png.endswith(b"-120") for png in tess_pngs)


def test_no_vote_means_no_duplicate_raster(monkeypatch):
    """Without Tesseract there is no vote pass, so the RapidOCR body
    still renders each page exactly once (baseline guard)."""
    monkeypatch.setattr(tesseract_local, "tesseract_available", lambda: False)
    renders, scored = _scan_mocks(
        monkeypatch,
        n_pages=5,
        vote_texts=[],
        body_texts=["中文正文内容 " * 20] * 5,
    )
    out = hybrid_parse._scanned_without_cloud(b"%PDF scan")
    assert out is not None and "中文正文内容" in out
    assert sorted(p for p, _ in renders) == [1, 2, 3, 4, 5]
    assert len(scored) == 5
