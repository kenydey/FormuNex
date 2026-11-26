"""2026-11-26 审查第一波 · 资料检索 P1-1 / P1-2 定向单测 (literature.py)。

P1-1: 终态去重改用 _evidence_key (DOI 前缀剥离 + casefold), 同一 DOI
四种写法只留排序最高的一行。
P1-2: 分页终结条件 —— 仅"原始页为空"才停; new==0 时连续 2 轮全重复才停;
异常页不推进 cursor、不标记完成, 下轮同一 cursor 重试。
全离线: monkeypatch 替换 _build_streams 模拟外部检索 stream。
"""
from __future__ import annotations

import pytest

from app.domain.schemas import Evidence
from app.services import literature


@pytest.fixture(autouse=True)
def _clear_cache():
    with literature._SEARCH_CACHE_LOCK:
        literature._SEARCH_CACHE.clear()
    yield
    with literature._SEARCH_CACHE_LOCK:
        literature._SEARCH_CACHE.clear()


def _ev(identifier, title, relevance=0.9, source="OpenAlex"):
    return Evidence(
        source=source,
        identifier=identifier,
        title=title,
        snippet=f"epoxy coating corrosion {title}",
        relevance=relevance,
    )


# ── P1-1 ───────────────────────────────────────────────────────────────────


def test_evidence_key_unifies_four_doi_writings():
    keys = {
        literature._evidence_key(
            _ev(ident, "Epoxy coating corrosion protection")
        )
        for ident in (
            "10.1016/j.porgcoat.2019.105338",
            "DOI:10.1016/j.porgcoat.2019.105338",
            "https://doi.org/10.1016/j.porgcoat.2019.105338",
            "10.1016/J.PORGCOAT.2019.105338",
        )
    }
    assert keys == {"10.1016/j.porgcoat.2019.105338"}


def test_merge_filter_rank_dedupes_four_doi_writings_keeps_best_ranked():
    rows = [
        _ev("10.1016/j.porgcoat.2019.105338", "Epoxy coating corrosion protection", 0.9),
        _ev("DOI:10.1016/j.porgcoat.2019.105338", "Epoxy coating corrosion protection", 0.8),
        _ev("https://doi.org/10.1016/j.porgcoat.2019.105338", "Epoxy coating corrosion protection", 0.7),
        _ev("10.1016/J.PORGCOAT.2019.105338", "Epoxy coating corrosion protection", 0.6),
    ]
    ranked, _report = literature._merge_filter_rank(rows, "epoxy coating corrosion", 10)
    assert len(ranked) == 1
    assert ranked[0].identifier == "10.1016/j.porgcoat.2019.105338"
    assert ranked[0].relevance == 0.9


def test_merge_filter_rank_keeps_distinct_dois():
    rows = [
        _ev("10.1016/j.porgcoat.2019.105338", "Epoxy coating corrosion protection", 0.9),
        _ev("10.1016/j.porgcoat.2020.105339", "Epoxy coating salt spray study", 0.8),
    ]
    ranked, _report = literature._merge_filter_rank(rows, "epoxy coating corrosion", 10)
    assert len(ranked) == 2


# ── P1-2 ───────────────────────────────────────────────────────────────────


def _mock_streams(monkeypatch, streams):
    """streams: {name: [page0, page1, ...]}; fetch(off) 按 cursor 取页。

    每源的 fetch 收到 cursor(页内偏移); 本夹具用 cursor//page_size 定位页。
    """

    def fake_build_streams(*a, **k):
        out = []
        for name, pages in streams.items():
            calls: list[int] = []

            def make_fetch(pages=pages, calls=calls):
                def fetch(off):
                    calls.append(off)
                    idx = off // 2
                    if idx >= len(pages):
                        return []
                    page = pages[idx]
                    if isinstance(page, Exception):
                        raise page
                    return list(page)

                fetch.calls = calls
                return fetch

            fetch = make_fetch()
            out.append(
                {"name": name, "fetch": fetch, "cursor": 0, "paged": True,
                 "done": False, "dup_rounds": 0}
            )
        return out

    monkeypatch.setattr(literature, "_build_streams", fake_build_streams)


def _search_kw():
    return dict(
        source_types=["literature"], req=None, total_limit=10,
        per_source_cap=2, max_rounds=5,
    )


def test_dup_page_does_not_kill_source_deep_pages_reachable(monkeypatch):
    """B 源首轮全重复 → 不再被判枯竭, 其第 2 页全新结果仍被抓取。"""
    paper1 = _ev("10.1000/paper1", "epoxy coating paper one")
    paper2 = _ev("10.1000/paper2", "epoxy coating paper two")
    paper3 = _ev("10.1000/paper3", "epoxy coating paper three")
    paper4 = _ev("10.1000/paper4", "epoxy coating paper four")
    _mock_streams(monkeypatch, {
        "a": [[paper1, paper2], []],
        "b": [[paper1, paper2], [paper3, paper4], []],
    })
    final, _payload = literature.iter_search("epoxy coating", **_search_kw())
    got = {e.identifier for e in final}
    assert "10.1000/paper3" in got
    assert "10.1000/paper4" in got


def test_two_consecutive_dup_rounds_stop_source(monkeypatch):
    """连续 2 轮全重复才停: 第 3 轮仍抓, 第 4 轮(全新结果)不再抓。"""
    paper1 = _ev("10.1000/paper1", "epoxy coating paper one")
    paper2 = _ev("10.1000/paper2", "epoxy coating paper two")
    calls: list[int] = []

    def fetch(off):
        calls.append(off)
        idx = off // 2
        return [paper1] if idx in (0, 1, 2) else [paper2]

    monkeypatch.setattr(
        literature, "_build_streams",
        lambda *a, **k: [{"name": "mock", "fetch": fetch, "cursor": 0,
                          "paged": True, "done": False, "dup_rounds": 0}],
    )
    final, _payload = literature.iter_search("epoxy coating", **_search_kw())
    assert len(calls) == 3  # 第 1 轮新行 + 连续 2 轮全重复后停
    assert {e.identifier for e in final} == {"10.1000/paper1"}


def test_error_page_retries_same_cursor_without_finishing(monkeypatch):
    """异常页不推进 cursor、不标记完成: 首轮抛错后下轮同一 cursor 重试成功。"""
    paper1 = _ev("10.1000/paper1", "epoxy coating paper one")
    calls: list[int] = []

    def fetch(off):
        calls.append(off)
        if len(calls) == 1:
            raise RuntimeError("upstream flap")
        return [paper1] if off == 0 else []

    monkeypatch.setattr(
        literature, "_build_streams",
        lambda *a, **k: [{"name": "mock", "fetch": fetch, "cursor": 0,
                          "paged": True, "done": False, "dup_rounds": 0}],
    )
    final, _payload = literature.iter_search("epoxy coating", **_search_kw())
    assert calls[0] == 0 and calls[1] == 0  # 重试仍用同一 cursor
    assert calls[2] == 2  # 成功页后 cursor 才推进
    assert {e.identifier for e in final} == {"10.1000/paper1"}
