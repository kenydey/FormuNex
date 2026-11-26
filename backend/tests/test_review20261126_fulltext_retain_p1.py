"""2026-11-26 审查第一波 · 资料检索 P1-4 定向单测。

P1-4: enrich_search_results 全文取不到时保留原摘要行(置 has_fulltext=False),
而非从结果里删除; FulltextReport 增加 dropped_abstracts 计数;
deep_research/engine.retrieve 增加调用方兜底 —— enrich 丢行时恢复原摘要行。
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services import fulltext_fetcher as ff
from app.services.deep_research import DeepResearchEngine
from app.services.pdf_downloader import FetchedFulltext


LONG_TEXT = "\n\n".join(
    f"Section {i}. Epoxy-amine coating full text paragraph with formulation details, "
    "zinc phosphate loadings, cure schedules and salt spray results measured on steel."
    for i in range(40)
)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _enable(monkeypatch, max_docs: int = 8):
    monkeypatch.setenv("FORMUNEX_FULLTEXT_ENRICH", "true")
    monkeypatch.setenv("FORMUNEX_FULLTEXT_MAX_DOCS", str(max_docs))
    get_settings.cache_clear()


def _ev(identifier: str, source: str = "USPTO", relevance: float = 0.9) -> Evidence:
    return Evidence(
        source=source, identifier=identifier, title=f"Doc {identifier}",
        snippet="abstract only", relevance=relevance,
    )


def test_enrich_all_failed_keeps_input_rows(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(ff, "_fetch_patent_text", lambda ev, t: None)
    rows = [_ev("US1234567"), _ev("US2345678")]
    out, report = ff.enrich_search_results(rows, persist=False)
    assert [e.identifier for e in out] == ["US1234567", "US2345678"]
    assert all(e.has_fulltext is False for e in out)
    assert report.attempted == 2
    assert report.succeeded == 0
    assert report.dropped_abstracts == 2
    assert report.as_dict()["dropped_abstracts"] == 2


def test_enrich_mixed_success_keeps_failed_row_in_place(monkeypatch):
    _enable(monkeypatch)

    def fake_fetch(ev, t):
        if ev.identifier == "US1110001":
            return FetchedFulltext(text=LONG_TEXT)
        return None

    monkeypatch.setattr(ff, "_fetch_patent_text", fake_fetch)
    rows = [_ev("US1110001"), _ev("US2220002")]
    out, report = ff.enrich_search_results(rows, persist=False)
    assert out[0].identifier == "US1110001#p0"  # 成功行被 chunk 行替换
    failed = [e for e in out if e.identifier == "US2220002"]
    assert len(failed) == 1 and failed[0].has_fulltext is False
    assert report.succeeded == 1 and report.dropped_abstracts == 1


def test_deep_research_retrieve_restores_rows_dropped_by_enrich(monkeypatch):
    """调用方兜底: enrich 返回丢行时, retrieve 恢复原摘要行。"""
    from app.services import literature

    monkeypatch.setenv("FORMUNEX_FULLTEXT_ENRICH", "true")
    get_settings.cache_clear()
    kept = _ev("10.1016/j.porgcoat.2020.105678", source="OpenAlex")
    dropped = _ev("10.1016/j.porgcoat.2020.105679", source="OpenAlex")
    monkeypatch.setattr(
        literature, "iter_search",
        lambda *a, **k: ([kept, dropped], {}),
    )
    monkeypatch.setattr(
        "app.services.fulltext_fetcher.enrich_search_results",
        lambda evidence, **k: ([kept], ff.FulltextReport(attempted=2, succeeded=1)),
    )
    with DeepResearchEngine() as engine:
        evidence, _expanded = engine.retrieve("epoxy coating corrosion")
    got = {e.identifier for e in evidence}
    assert "10.1016/j.porgcoat.2020.105678" in got
    assert "10.1016/j.porgcoat.2020.105679" in got
    restored = [e for e in evidence if e.identifier == "10.1016/j.porgcoat.2020.105679"]
    assert restored[0].has_fulltext is False
