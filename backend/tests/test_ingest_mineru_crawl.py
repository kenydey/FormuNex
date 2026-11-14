"""Batch 4 (F): MinerU crawl as the escalation for JS-rendered pages.

``ingest_url`` converts HTML locally first; only when the local conversion
finds nothing (a JS-rendered page) does it escalate to a MinerU crawl.
These tests pin that trigger — a page the local path already reads must
never spend quota, and a page it cannot read must get the crawled text.
"""
from __future__ import annotations

import pytest

import app.services.ingestion as ing
from app.services.ingestion import ingest_url


@pytest.fixture(autouse=True)
def _sanitize_no_proxy(monkeypatch):
    """Sandbox no_proxy holds a bracketed IPv6 ([::1]); httpx.Client
    construction explodes on it (see AGENTS.md)."""
    import os

    for key in ("no_proxy", "NO_PROXY"):
        raw = os.environ.get(key, "")
        if raw:
            monkeypatch.setenv(
                key, ",".join(p for p in raw.split(",") if "[" not in p)
            )


class _FakeStreamResp:
    def __init__(self, body: bytes, content_type: str = "text/html; charset=utf-8"):
        self._body = body
        self.headers = {"content-type": content_type}
        self.is_redirect = False

    def raise_for_status(self):
        pass

    def iter_bytes(self, chunk_size: int = 65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def close(self):
        pass


def _mock_fetch(monkeypatch, body: bytes):
    import httpx

    monkeypatch.setattr(ing, "is_safe_url", lambda url: True)

    def fake_send(self, request, **kwargs):
        return _FakeStreamResp(body)

    monkeypatch.setattr(httpx.Client, "send", fake_send)


def _evidence_text(outcome) -> str:
    return "\n".join(e.snippet for e in outcome.evidence)


def test_empty_html_escalates_to_crawl(monkeypatch):
    """A page the local conversion reads as empty is crawled, and the
    crawled text is what gets ingested."""
    _mock_fetch(
        monkeypatch,
        b"<html><head><script>var x = 1;</script></head><body></body></html>",
    )
    calls = []
    monkeypatch.setattr(
        ing, "_crawl_page_text", lambda url: calls.append(url) or "# Crawled\n\nreal content"
    )
    outcome = ingest_url("https://example.com/js-page", persist=False)
    assert calls == ["https://example.com/js-page"]
    assert "real content" in _evidence_text(outcome)
    assert outcome.extraction_status != "skipped"


def test_nonempty_html_does_not_crawl(monkeypatch):
    """A page the local path already reads never touches MinerU — no quota
    spent on a solved problem."""
    _mock_fetch(
        monkeypatch,
        "<html><body><h1>Server page</h1><p>Readable text here.</p></body></html>".encode(),
    )

    def _must_not_run(url: str) -> str:
        raise AssertionError("crawl must not run for a readable page")

    monkeypatch.setattr(ing, "_crawl_page_text", _must_not_run)
    outcome = ingest_url("https://example.com/server-page", persist=False)
    assert "Readable text here" in _evidence_text(outcome)


def test_crawl_failure_keeps_the_skipped_outcome(monkeypatch):
    """Crawl returning nothing leaves the empty text empty, and the existing
    'cannot extract text' outcome stands — no exception, no partial ingest."""
    _mock_fetch(
        monkeypatch,
        b"<html><head><script>var x = 1;</script></head><body></body></html>",
    )
    monkeypatch.setattr(ing, "_crawl_page_text", lambda url: "")
    outcome = ingest_url("https://example.com/js-page", persist=False)
    assert outcome.extraction_status == "skipped"


def test_crawl_page_text_returns_the_crawled_markdown(monkeypatch):
    """The fail-open helper: a document yields its markdown."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.services.mineru_cloud.crawl_url",
        lambda url, **kw: SimpleNamespace(markdown="# Crawled\n\nbody"),
    )
    assert ing._crawl_page_text("https://example.com/x") == "# Crawled\n\nbody"


def test_crawl_page_text_is_fail_open(monkeypatch):
    """None and exceptions both degrade to '' — the caller keeps the local
    result instead of failing the ingest."""
    monkeypatch.setattr("app.services.mineru_cloud.crawl_url", lambda url, **kw: None)
    assert ing._crawl_page_text("https://example.com/x") == ""

    def _boom(url, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr("app.services.mineru_cloud.crawl_url", _boom)
    assert ing._crawl_page_text("https://example.com/x") == ""
