"""2026-11-26 审查第二波 · 资料检索 A1（P2 批 3 + 批 4）定向单测。

批 3: SerpAPI/Tavily/S2 服务端分页、EPO offset、fetch 池扩容、过滤下推
（补跑回填）、审计单写、non_oa 计数、CNIPA 豁免、配额写路径复核。
批 4: 缓存 key 加设置指纹、私访改公开 API、标识 12 位 hex、去重归一化、
executor 复用 + 缓存 LRU。
全离线: monkeypatch 替换 HTTP 层 / stream / store，不碰网络与开发库。
"""
from __future__ import annotations

import re
import time
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services import content_filter, federated_search, kb_ingest, literature
from app.services import search_providers as sp
from app.services.content_filter import FilterReport
from app.services.federated_search import FederatedSearchEngine


@pytest.fixture(autouse=True)
def _clear_search_cache():
    with literature._SEARCH_CACHE_LOCK:
        literature._SEARCH_CACHE.clear()
    yield
    with literature._SEARCH_CACHE_LOCK:
        literature._SEARCH_CACHE.clear()
    get_settings.cache_clear()


def _ev(identifier, title, relevance=0.9, source="OpenAlex", snippet=None, **kw):
    return Evidence(
        source=source,
        identifier=identifier,
        title=title,
        snippet=snippet if snippet is not None else f"epoxy coating corrosion {title}",
        relevance=relevance,
        **kw,
    )


# ── P2-1 SerpAPI 服务端分页 ─────────────────────────────────────────────────


def test_serpapi_sends_start_and_pages_in_20s(monkeypatch):
    """start=offset 服务端分页；单次 num<=20，limit>20 时步进 20 补取。"""
    calls: list[tuple[int, int]] = []

    def fake_search(engine, q, key, limit, offset, extra_params=None):
        calls.append((limit, offset))
        return {
            "organic_results": [
                {
                    "result_id": f"r{offset + i}",
                    "link": f"https://x/{offset + i}",
                    "title": f"T{offset + i}",
                    "snippet": "s",
                }
                for i in range(limit)
            ]
        }

    monkeypatch.setattr(sp, "_serpapi_search", fake_search)
    monkeypatch.setenv("FORMUNEX_SERPAPI_API_KEY", "sk-test")
    get_settings.cache_clear()

    hits = sp.search_serpapi_scholar("epoxy coating", limit=5, offset=25)
    assert [h.identifier for h in hits] == [f"r{i}" for i in range(25, 30)]
    assert calls == [(5, 25)], "一页能装下时只发一次请求，start=offset"
    assert hits[0].relevance == 0.5, "第 25 行 relevance 应为 1-25*0.02"
    assert hits[0].relevance > hits[-1].relevance

    calls.clear()
    hits = sp.search_serpapi_scholar("epoxy coating", limit=45, offset=0)
    assert len(hits) == 45
    assert calls == [(20, 0), (20, 20), (5, 40)], "limit>20 时服务端步进补取，不跳行"
    assert hits[0].identifier == "r0" and hits[-1].identifier == "r44"


def test_serpapi_web_uses_server_paging(monkeypatch):
    """SerpAPI web 同样走服务端分页（不再客户端切片）。"""
    seen: list[tuple[int, int]] = []

    def fake_search(engine, q, key, limit, offset, extra_params=None):
        seen.append((limit, offset))
        return {
            "organic_results": [
                {"link": f"https://blog.example/{offset + i}", "title": "t", "snippet": "s"}
                for i in range(limit)
            ]
        }

    monkeypatch.setattr(sp, "_serpapi_search", fake_search)
    monkeypatch.setenv("FORMUNEX_SERPAPI_API_KEY", "sk-test")
    get_settings.cache_clear()
    hits = sp.search_serpapi_web("waterborne epoxy", limit=3, offset=20)
    assert len(hits) == 3
    assert seen == [(3, 20)]
    assert hits[0].identifier == "https://blog.example/20"


# ── P2-1 Tavily 上限明示 ────────────────────────────────────────────────────


def test_tavily_deep_page_returns_empty_without_http(monkeypatch, caplog):
    """Tavily 无服务端分页：offset>=20 的深页明示不可达，不发请求。"""
    posted: list = []

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None):
            posted.append(json)
            return FakeResp()

    monkeypatch.setattr("httpx.Client", lambda **kw: FakeClient())
    monkeypatch.setenv("FORMUNEX_TAVILY_API_KEY", "tvly-test")
    get_settings.cache_clear()
    with caplog.at_level("WARNING"):
        hits = sp.search_tavily("防腐涂料", limit=5, offset=25)
    assert hits == []
    assert posted == [], "深页不应发请求"
    assert "no server-side pagination" in caplog.text


def test_tavily_first_pages_still_slice(monkeypatch):
    """offset<20 时保持原有 max_results + 客户端切片行为。"""

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"url": f"https://example.com/{i}", "title": f"hit {i}", "content": "body"}
                    for i in range(15)
                ]
            }

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None):
            assert json["max_results"] == 15, "limit+offset<=20 时一次取足"
            return FakeResp()

    monkeypatch.setattr("httpx.Client", lambda **kw: FakeClient())
    monkeypatch.setenv("FORMUNEX_TAVILY_API_KEY", "tvly-test")
    get_settings.cache_clear()
    hits = sp.search_tavily("防腐涂料", limit=5, offset=10)
    assert [h.identifier for h in hits] == [f"https://example.com/{i}" for i in range(10, 15)]


# ── P2-2 EPO relevance 随 offset 递减 ───────────────────────────────────────


def test_epo_relevance_decreases_with_offset(monkeypatch):
    """offset=25 的 EPO 行 relevance 从 0.5 起递减，不再回到 1.0。"""
    from app.services import epo_ops, provider_health

    provider_health.reset_provider_health()

    class FakePage:
        def __init__(self, hits, total):
            self.hits = hits
            self.total = total

    all_hits = [
        SimpleNamespace(
            publication_number=f"EP{i:07d}A1",
            title=f"Patent {i} epoxy coating",
            abstract=f"epoxy coating abstract {i}",
            applicant="ACME",
            pub_date="2020-01-01",
            pub_year=2020,
        )
        for i in range(30)
    ]

    def fake_search(cql, key=None, secret=None, begin=1, end=25, client=None):
        return FakePage(all_hits[begin - 1 : end], 30)

    monkeypatch.setattr(epo_ops, "search", fake_search)
    monkeypatch.setattr(epo_ops, "build_cql", lambda *a, **k: "TI=x")
    monkeypatch.setattr(epo_ops, "query_terms", lambda *a, **k: ["x"])
    settings = get_settings()
    monkeypatch.setattr(settings, "epo_consumer_key", "provider-key", raising=False)
    monkeypatch.setattr(settings, "epo_consumer_secret", "provider-secret-9876543210", raising=False)

    hits0 = sp.search_epo_patents("epoxy coating", limit=5, offset=0)
    assert len(hits0) == 5
    assert hits0[0].relevance == 1.0

    hits25 = sp.search_epo_patents("epoxy coating", limit=5, offset=25)
    assert len(hits25) == 5
    assert hits25[0].identifier == "EP0000025A1"
    assert hits25[0].relevance == 0.5, "offset=25 首行 relevance 应为 1-25*0.02"
    assert [h.relevance for h in hits25] == sorted(
        [h.relevance for h in hits25], reverse=True
    )
    assert hits25[0].relevance < hits0[0].relevance


# ── P2-3 Semantic Scholar 服务端分页 + 过滤后补取 ──────────────────────────


def test_s2_server_side_offset_and_refill(monkeypatch):
    """S2 传 offset 服务端分页；首批全被 fieldsOfStudy 滤掉时补取下一页。"""
    calls: list[dict] = []

    class FakeResp:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            calls.append(dict(params or {}))
            offset = (params or {}).get("offset", 0)
            if offset == 0:
                papers = [
                    {"paperId": f"p{i}", "title": f"Astro {i}", "abstract": "a",
                     "fieldsOfStudy": ["Astrophysics"],
                     "externalIds": {"DOI": f"10.1234/astro{i}"}}
                    for i in range(15)
                ]
            else:
                papers = [
                    {"paperId": f"q{i}", "title": f"Chem {i}", "abstract": "epoxy coating",
                     "fieldsOfStudy": ["Chemistry"],
                     "externalIds": {"DOI": f"10.1234/chem{i}"}}
                    for i in range(15)
                ]
            return FakeResp({"data": papers})

    monkeypatch.setattr(literature, "make_client", lambda **kw: FakeClient())
    hits = literature.search_semantic_scholar("epoxy coating", limit=5, offset=0)
    assert len(calls) == 2, "首批全过滤后应补取下一页"
    assert calls[0]["offset"] == 0 and calls[1]["offset"] == 15
    assert len(hits) == 5
    assert all(h.identifier.startswith("10.1234/chem") for h in hits)
    assert hits[0].relevance == 1.0


# ── P2-4 fetch 池扩容 ───────────────────────────────────────────────────────


def test_fetch_executor_has_8_workers():
    assert literature._FETCH_EXECUTOR._max_workers == 8


# ── P2-5 缓存 key 加设置指纹 ────────────────────────────────────────────────


def test_cache_key_differs_when_settings_change():
    """换开关 / 换 key 配置 → key 不同；同配置 → key 稳定。"""
    from app.config import Settings

    k_base = literature._search_cache_key(
        "q", ["literature"], 10, 5, "d", None, settings=Settings()
    )
    k_rerank_off = literature._search_cache_key(
        "q", ["literature"], 10, 5, "d", None,
        settings=Settings(search_rerank_enabled=False),
    )
    assert k_base != k_rerank_off, "rerank 开关变化必须换 key"
    k_arm_off = literature._search_cache_key(
        "q", ["literature"], 10, 5, "d", None,
        settings=Settings(openalex_multi_arm=False),
    )
    assert k_base != k_arm_off, "multi_arm 开关变化必须换 key"
    k_same = literature._search_cache_key(
        "q", ["literature"], 10, 5, "d", None, settings=Settings()
    )
    assert k_base == k_same


def test_settings_fingerprint_ignores_key_values():
    """指纹只记 key 有无，不记密钥值：换 key 值但保持有无不变 → 指纹不变。"""
    ns_with = SimpleNamespace(
        serpapi_api_key="sk-1", tavily_api_key="", epo_consumer_key="",
        openalex_api_key="", openalex_enabled=True, openalex_multi_arm=True,
        openalex_arm_precise=True, openalex_arm_recall=True, openalex_arm_broad=True,
        kb_search_deny_enabled=True, search_rerank_enabled=True,
        search_mmr_enabled=False, content_filter_enabled=True, fulltext_enrich=True,
    )
    ns_with_other_key = SimpleNamespace(**{**vars(ns_with), "serpapi_api_key": "sk-2"})
    ns_without = SimpleNamespace(**{**vars(ns_with), "serpapi_api_key": ""})
    assert literature._settings_fingerprint(ns_with) == literature._settings_fingerprint(
        ns_with_other_key
    )
    assert literature._settings_fingerprint(ns_with) != literature._settings_fingerprint(
        ns_without
    )


# ── P3-6 缓存 LRU + 轮次 executor 复用 ─────────────────────────────────────


def test_cache_hit_refreshes_timestamp():
    """命中刷新时间戳：热点条目不再因 FIFO 被提前淘汰。"""
    literature._search_cache_put("lru-k", ["a"], {"x": 1})
    with literature._SEARCH_CACHE_LOCK:
        ts1 = literature._SEARCH_CACHE["lru-k"][0]
    time.sleep(0.02)
    assert literature._search_cache_get("lru-k", 600) is not None
    with literature._SEARCH_CACHE_LOCK:
        ts2 = literature._SEARCH_CACHE["lru-k"][0]
    assert ts2 > ts1


def test_round_executor_is_reused_across_searches(monkeypatch):
    """两轮 iter_search 共用同一轮次 executor，跑完后 executor 仍可用。"""
    from app.services import literature as lit

    def fake_build_streams(*a, **k):
        def fetch(off):
            return [_ev("10.1234/abc", "Epoxy coating test")]

        return [
            {"name": "t", "fetch": fetch, "cursor": 0, "paged": False,
             "done": False, "dup_rounds": 0}
        ]

    monkeypatch.setattr(lit, "_build_streams", fake_build_streams)
    ex = lit._ROUND_EXECUTOR
    assert ex._max_workers == 8
    final1, _ = lit.iter_search(
        "epoxy coating", ["literature"], req=None, total_limit=10,
        per_source_cap=2, max_rounds=5,
    )
    assert len(final1) == 1
    final2, _ = lit.iter_search(
        "epoxy coating primer", ["literature"], req=None, total_limit=10,
        per_source_cap=2, max_rounds=5,
    )
    assert len(final2) == 1
    # executor 未被 shutdown：跑完两次检索后仍能提交新任务。
    fut = ex.submit(lambda: 42)
    assert fut.result(timeout=5) == 42


# ── P2-12 CNIPA 豁免 ────────────────────────────────────────────────────────


def test_cnipa_exempt_from_weblike():
    ev = _ev("https://patent.cnipa.gov.cn/x", "Epoxy coating patent", source="CNIPA (web)")
    assert literature._is_weblike(ev) is False
    assert literature._is_patent_or_literature(ev) is True
    # 对照：同内容走 Tavily 仍判 weblike。
    web = _ev("https://example.com/x", "Epoxy coating patent", source="Tavily")
    assert literature._is_weblike(web) is True


def test_cnipa_rows_survive_simhash_fold():
    """两篇摘要相近的 CNIPA 同族行不再被 SimHash 近重折叠。"""
    rows = [
        _ev("https://patent.cnipa.gov.cn/a", "Epoxy coating anticorrosion method",
            source="CNIPA (web)", snippet="epoxy coating corrosion protection method for steel substrate"),
        _ev("https://patent.cnipa.gov.cn/b", "Epoxy coating anticorrosion method",
            source="CNIPA (web)", snippet="epoxy coating corrosion protection method for steel substrate"),
    ]
    ranked, _report = literature._merge_filter_rank(rows, "epoxy coating", 10)
    assert len(ranked) == 2


# ── P2-9 non_oa 计数 ───────────────────────────────────────────────────────


def test_openalex_non_oa_counted_in_stats(monkeypatch):
    """provider 层统计非 OA 丢弃数：2 OA 保留、1 非 OA 计入 stats。"""
    payload = {
        "results": [
            {"id": "https://openalex.org/W1", "display_name": "OA study",
             "doi": "https://doi.org/10.1234/oa",
             "open_access": {"is_oa": True, "oa_status": "gold"}},
            {"id": "https://openalex.org/W2", "display_name": "Closed study",
             "doi": "https://doi.org/10.1234/closed",
             "open_access": {"is_oa": False}},
        ]
    }

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            return FakeResp()

    monkeypatch.setattr("httpx.Client", lambda **kw: FakeClient())
    stats: dict = {}
    hits = sp.search_openalex("zinc coating", limit=5, stats=stats)
    assert len(hits) == 1
    assert hits[0].identifier == "10.1234/oa"
    assert stats["non_oa_dropped"] == 1


def test_filter_report_provider_drop_does_not_inflate_dropped():
    report = FilterReport()
    report.record_provider_drop("non_oa", 3)
    assert report.dropped_by_reason["non_oa"] == 3
    assert report.dropped == 0, "provider 侧预过滤不计入 dropped（未到达过滤器）"
    report.record_provider_drop("non_oa", 0)
    assert report.dropped_by_reason["non_oa"] == 3


def test_iter_search_merges_non_oa_into_filter_payload(monkeypatch):
    """端到端：OpenAlex/ChemRxiv 流的非 OA 丢弃数进 filter_payload。"""
    from app.services import provider_health

    provider_health.reset_provider_health()
    settings = get_settings()
    monkeypatch.setattr(settings, "openalex_multi_arm", False, raising=False)

    def _inv(text):
        return {w: [i] for i, w in enumerate(text.split())}

    oa_payload = {
        "results": [
            {"id": "https://openalex.org/W1", "display_name": "OA epoxy study",
             "doi": "https://doi.org/10.1234/oa1", "title": "epoxy coating study",
             "abstract_inverted_index": _inv(
                 "Salt spray resistance of epoxy coating on zinc phosphate "
                 "treated steel substrate after 1000 hours exposure"),
             "open_access": {"is_oa": True, "oa_status": "gold"}},
            {"id": "https://openalex.org/W2", "display_name": "OA epoxy study 2",
             "doi": "https://doi.org/10.1234/oa2", "title": "epoxy coating study",
             "abstract_inverted_index": _inv(
                 "Waterborne epoxy primer adhesion on aluminium alloy with "
                 "silane pretreatment and corrosion protection performance"),
             "open_access": {"is_oa": True, "oa_status": "gold"}},
            {"id": "https://openalex.org/W3", "display_name": "Closed epoxy study",
             "doi": "https://doi.org/10.1234/closed", "title": "epoxy coating study",
             "abstract_inverted_index": _inv("Closed access epoxy coating study abstract text"),
             "open_access": {"is_oa": False}},
        ]
    }

    class FakeResp:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            if "openalex.org" in url:
                return FakeResp(oa_payload)
            return FakeResp({"data": []})

    monkeypatch.setattr(sp, "make_client", lambda **kw: FakeClient())
    monkeypatch.setattr(literature, "make_client", lambda **kw: FakeClient())
    final, payload = literature.iter_search(
        "epoxy coating", ["literature"], req=None, total_limit=10,
        per_source_cap=2, max_rounds=5,
    )
    assert len(final) == 2, "两 OA 行（OpenAlex 与 ChemRxiv 流返回同一批，去重后 2 行）"
    # 计数口径：第 1 轮两流凑满 limit=2 后即 break，页内第 3 行（非 OA）未被
    # 访问故不计；第 2 轮 offset=2 的两次调用各只见到该非 OA 行，各计 1。
    assert payload["non_oa_dropped"] == 2
    assert payload["dropped_by_reason"].get("non_oa", 0) == payload["non_oa_dropped"]


def test_content_filter_weblike_exempts_cnipa():
    """content_filter 侧的折叠门同样豁免 CNIPA（含 URL identifier 的行）。"""
    ev = _ev("https://patent.cnipa.gov.cn/a", "Epoxy coating patent", source="CNIPA (web)")
    assert content_filter._is_weblike(ev) is False
    kept, _report = content_filter.filter_evidence(
        [
            _ev("https://patent.cnipa.gov.cn/a", "Epoxy coating method",
                source="CNIPA (web)",
                snippet="epoxy coating corrosion protection method for steel substrate"),
            _ev("https://patent.cnipa.gov.cn/b", "Epoxy coating method",
                source="CNIPA (web)",
                snippet="epoxy coating corrosion protection method for steel substrate"),
        ],
        "epoxy coating",
    )
    assert len(kept) == 2, "同族 CNIPA 行共享摘要也不应被折叠"


# ── P2-7 Federated 过滤后补跑回填 ──────────────────────────────────────────


def test_federated_refills_after_date_filter(monkeypatch):
    """日期过滤后不足 limit 时用加倍 limit 补跑，直到凑满。"""
    calls: list[int] = []

    def fake_iter_search(query, types, req=None, total_limit=300, per_source_cap=50,
                         progress_cb=None):
        calls.append(total_limit)
        if total_limit == 5:
            return ([
                _ev("10.1/old1", "Old epoxy study", pub_year=2010),
                _ev("10.1/old2", "Old epoxy study 2", pub_year=2012),
            ], {})
        return ([
            _ev(f"10.1/new{i}", f"New epoxy study {i}", pub_year=2024)
            for i in range(10)
        ], {})

    monkeypatch.setattr(federated_search.literature, "iter_search", fake_iter_search)
    settings = SimpleNamespace(
        federated_sources=["literature"], search_total_limit=300,
        search_limit_per_source=50,
    )
    engine = FederatedSearchEngine(settings)
    result = engine.search("epoxy coating", date_from=2020, total_limit=5)
    assert calls == [5, 10], "首轮被滤空后应以 2 倍 limit 补跑一次"
    assert len(result.evidence) == 5
    assert all((e.pub_year or 0) >= 2020 for e in result.evidence)


def test_federated_default_limit_matches_iter_search(monkeypatch):
    """默认 limit 与 iter_search 统一为 search_total_limit（旧 120 已废弃）。"""
    calls: list[int] = []

    def fake_iter_search(query, types, req=None, total_limit=300, per_source_cap=50,
                         progress_cb=None):
        calls.append(total_limit)
        return ([], {})

    monkeypatch.setattr(federated_search.literature, "iter_search", fake_iter_search)
    settings = SimpleNamespace(
        federated_sources=["literature"], search_total_limit=300,
        search_limit_per_source=50,
    )
    engine = FederatedSearchEngine(settings)
    engine.search("epoxy coating")
    assert calls == [300]


def test_federated_no_refill_without_filters(monkeypatch):
    """无过滤时不补跑（一次取数即返回）。"""
    calls: list[int] = []

    def fake_iter_search(query, types, req=None, total_limit=300, per_source_cap=50,
                         progress_cb=None):
        calls.append(total_limit)
        return ([_ev("10.1/a", "Epoxy study", pub_year=2024)], {})

    monkeypatch.setattr(federated_search.literature, "iter_search", fake_iter_search)
    settings = SimpleNamespace(
        federated_sources=["literature"], search_total_limit=300,
        search_limit_per_source=50,
    )
    engine = FederatedSearchEngine(settings)
    result = engine.search("epoxy coating", total_limit=5)
    assert calls == [5]
    assert len(result.evidence) == 1


# ── P2-8 审计单写 ──────────────────────────────────────────────────────────


def test_dispatch_passes_write_audit_false(monkeypatch):
    """分发侧 select_ingest_targets 不写审计；执行侧保留唯一写点。"""
    from app.worker.tasks import kb as kb_tasks

    captured: dict = {}

    def fake_select(evidence, **kw):
        captured.update(kw)
        return [(evidence[0], "literature")] if evidence else []

    monkeypatch.setattr(kb_ingest, "select_ingest_targets", fake_select)
    monkeypatch.setattr(kb_ingest, "ingest_enabled", lambda: True)
    monkeypatch.setattr(
        "app.api._idempotency.enqueue_outbox", lambda op, payload: "obx-1"
    )
    monkeypatch.setattr(
        "app.services.dispatch.dispatch_task_kernel",
        lambda *a, **k: "task-1",
    )
    monkeypatch.setattr(
        kb_tasks, "task_manager",
        SimpleNamespace(register_celery_task=lambda *a, **k: None),
    )
    ev = _ev("10.1234/abc", "Epoxy coating study")
    task_id = kb_tasks.dispatch_kb_ingest(
        [ev.model_dump()], project_id="p1", query="epoxy coating"
    )
    # eager 分支返回自生成的 task_id（dispatch_task_kernel 的返回值不被采用）；
    # 本测试的关键断言是分发侧传了 write_audit=False（见 captured）。
    assert task_id.startswith("kbingest-")
    assert captured.get("write_audit") is False, "分发侧必须传 write_audit=False"


def test_execution_side_still_writes_audit(monkeypatch):
    """执行侧 select_ingest_targets 默认仍写审计（唯一写点未被误关）。"""
    wrote: list[dict] = []
    monkeypatch.setattr(
        kb_ingest, "write_ingest_audit",
        lambda **kw: wrote.append(kw),
    )
    ev = _ev("10.1234/abc", "Epoxy coating study")
    targets = kb_ingest.select_ingest_targets([ev], query="")
    assert len(targets) == 1
    assert len(wrote) == 1
    assert wrote[0]["action"] == "accept"


# ── P3-7 select 去重归一化 ─────────────────────────────────────────────────


def test_select_ingest_targets_dedupes_normalized_doi():
    """同一 DOI 两种写法（裸 DOI / DOI: 前缀）只占一个目标位。"""
    rows = [
        _ev("10.1234/abc", "Epoxy coating study"),
        _ev("DOI:10.1234/abc", "Epoxy coating study"),
    ]
    targets = kb_ingest.select_ingest_targets(rows, query="", write_audit=False)
    assert len(targets) == 1
    assert targets[0][0].identifier == "10.1234/abc"


# ── P2-10 配额写路径复核 ───────────────────────────────────────────────────


class _FakeSourceStore:
    def __init__(self, count=0, create_id="src-1"):
        self._count = count
        self._create_id = create_id
        self.created: list[dict] = []

    def count_for_project(self, project_id, acquisition=None):
        return self._count

    def find_by_hash(self, content_hash):
        return None

    def find_failed_by_origin(self, origin):
        return None

    def record_ingest_failure(self, **kw):
        return None

    def create(self, **kw):
        self.created.append(kw)
        return self._create_id


def _quota_settings(monkeypatch, quota):
    monkeypatch.setattr(
        "app.services.fulltext_fetcher.get_settings",
        lambda: SimpleNamespace(
            kb_project_source_quota=quota, patent_prefer_html=True
        ),
    )


def test_persist_fulltext_refuses_when_quota_full(monkeypatch):
    """写路径配额已满：不新建行，refusal 标明 project_quota_full。"""
    from app.services import fulltext_fetcher as ff

    store = _FakeSourceStore(count=300)
    monkeypatch.setattr(
        "app.db.source_store.get_source_store", lambda: store
    )
    monkeypatch.setattr(
        "app.services.kb_index.index_source", lambda *a, **k: 1
    )
    _quota_settings(monkeypatch, 300)
    ev = _ev("10.1234/abc", "Epoxy coating study")
    refusal: dict = {}
    assert ff._persist_fulltext("some full text", ev, "literature",
                                project_id="p1", refusal=refusal) is None
    assert refusal.get("reason") == "project_quota_full"
    assert store.created == [], "配额已满时不得新建行"


def test_persist_fulltext_creates_when_quota_available(monkeypatch):
    """配额有余量：正常新建行，refusal 为空。"""
    from app.services import fulltext_fetcher as ff

    store = _FakeSourceStore(count=10)
    monkeypatch.setattr(
        "app.db.source_store.get_source_store", lambda: store
    )
    monkeypatch.setattr(
        "app.services.kb_index.index_source", lambda *a, **k: 1
    )
    _quota_settings(monkeypatch, 300)
    ev = _ev("10.1234/abc", "Epoxy coating study")
    refusal: dict = {}
    assert ff._persist_fulltext("some full text", ev, "literature",
                                project_id="p1", refusal=refusal) == "src-1"
    assert refusal == {}
    assert len(store.created) == 1


def test_index_one_maps_quota_refusal_to_skipped(monkeypatch):
    """配额拒绝在 _index_one 记 skipped/project_quota_full，不进失败落盘。"""
    from app.services import fulltext_fetcher as ff

    def fake_persist(text, ev, kind, **kw):
        kw.get("refusal", {})["reason"] = "project_quota_full"
        return None

    monkeypatch.setattr(ff, "persist_fulltext", fake_persist)
    ev = _ev("10.1234/abc", "Epoxy coating study")
    doc: dict = {}
    emitted: list[dict] = []
    kb_ingest._index_one("text", ev, "literature", emitted.append, doc, project_id="p1")
    assert doc["status"] == "skipped"
    assert doc["skip_reason"] == "project_quota_full"


# ── P3-1 公开 API ──────────────────────────────────────────────────────────


def test_public_wrappers_exist_and_fetch_one_uses_them(monkeypatch):
    """公开包装存在；_fetch_one 经公开 API 取全文（私访已移除）。"""
    from app.services import fulltext_fetcher as ff
    from app.services.pdf_downloader import FetchedFulltext

    assert callable(ff.dispatch_fetch)
    assert callable(ff.last_acquisition)
    assert callable(ff.persist_fulltext)

    seen: dict = {}

    def fake_dispatch(kind, ev, timeout):
        seen["kind"] = kind
        return FetchedFulltext(text="hello world full text")

    monkeypatch.setattr(ff, "dispatch_fetch", fake_dispatch)
    monkeypatch.setattr(ff, "last_acquisition", lambda: "html")
    ev = _ev("10.1234/abc", "Epoxy coating study")
    doc: dict = {}
    text, pdf_bytes, tables = kb_ingest._fetch_one(
        ev, "literature", 20.0, lambda d: None, doc
    )
    assert text == "hello world full text"
    assert seen["kind"] == "literature"
    assert doc.get("acquisition") == "html"


# ── P3-3 标识 12 位 hex + 来源改名 ─────────────────────────────────────────


def test_split_lit_answer_uses_12_hex_and_litsearch():
    """标识 12 位 hex 且混入答案哈希；来源名为 LitSearch。"""
    a = literature.split_lit_answer("An answer with no citations.", query="epoxy")
    assert len(a) == 1
    assert a[0].source == "LitSearch"
    assert re.fullmatch(r"chemlit:[0-9a-f]{12}", a[0].identifier), a[0].identifier
    b = literature.split_lit_answer("An answer with no citations.", query="epoxy")
    assert b[0].identifier == a[0].identifier, "同查询同答案标识稳定"
    c = literature.split_lit_answer("A different answer.", query="epoxy")
    assert c[0].identifier != a[0].identifier, "同查询不同答案不再撞标识"


def test_chemcrow_hint_no_longer_mentions_paper_qa():
    hint = literature.get_source_availability()["chemcrow"]["hint"]
    assert "paper-qa" not in hint
    assert "2026-09" in hint


# ── P2-11 种子/在线按 is_seed_corpus 划分（协调员补项） ─────────────────────


def test_seed_online_split_uses_is_seed_corpus_not_identifier():
    """在线结果撞种子专利号时不得绕过在线过滤（P2-11）。

    在线行 identifier 与种子专利号 US9982145B2 相同、但 domain_match="none"
    且 relevance 更高：旧逻辑按裸 identifier 把它算作种子，_filter_seed_by_query
    命中查询词后保留、并在去重时挤掉真种子；新逻辑按 is_seed_corpus 划分，
    在线行走 _keep 即被 domain_match="none" 拒掉，只剩真种子。
    """
    seed = Evidence(
        source="USPTO",
        identifier="US9982145B2",
        title="Waterborne epoxy anticorrosive coating with zinc phosphate",
        snippet="epoxy resin primer",
        relevance=0.1,
        is_seed_corpus=True,
    )
    online = Evidence(
        source="uspto",
        identifier="US9982145B2",
        title="Epoxy resin coating additive",
        snippet="epoxy resin formulation",
        relevance=0.95,
        domain_match="none",
    )
    merged, _report = literature._merge_filter_rank(
        [seed, online], "epoxy resin", total_limit=10
    )
    assert [e.identifier for e in merged] == ["US9982145B2"]
    assert merged[0].is_seed_corpus is True
