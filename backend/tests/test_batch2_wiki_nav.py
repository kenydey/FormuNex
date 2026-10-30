"""Batch 2: Wiki admission + PageIndex nav + table read path + rerank parity.

Covers the three eval scenario categories from the plan §4 (long-doc nav
QA, wiki-labeled evidence QA, OCR-table QA) plus the unit seams:
retrieval gate admission, hybrid wiki weight, Claims wiki cap, blend
FTS merge + project isolation, coverage stats + stale rebuild, probe
alpha alignment, rerank passthrough.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.models import SourceDocument
from app.db.session_utils import commit_session
from app.db.source_store import SourceStore
from app.db.toc_tree_store import TocTreeStore
from app.domain.schemas import Evidence


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    """Isolated stores on a temp SQLite DB (mirrors test_golden_eval)."""
    import app.db.chunk_store as chunk_store_mod
    import app.db.database as db_mod
    import app.db.source_store as source_store_mod
    import app.db.toc_tree_store as toc_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    toc = TocTreeStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(toc_store_mod, "_store", toc)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    return src, chk, toc, factory


def _ev(identifier: str, source: str, relevance: float, page=None) -> Evidence:
    return Evidence(
        source=source,
        identifier=identifier,
        title="t",
        snippet="s",
        relevance=relevance,
        page=page,
    )


# ── Wiki admission: gate + weight ──────────────────────────────────────────


def test_gate_admits_wiki_when_enabled(monkeypatch):
    from app.services.kb_retrieval_gate import drop_reason_for_chunk

    chunk = SimpleNamespace(
        source_id="wiki-sid",
        text="胺固化环氧体系的催化剂窗口与暴聚风险说明，内容足够长不会被判定为垃圾文本段落。",
        block_type=None,
    )
    # Default: admission on → no drop.
    assert drop_reason_for_chunk(chunk, wiki_ids={"wiki-sid"}) is None
    # Switch off → old wiki_track exclusion returns.
    monkeypatch.setenv("FORMUNEX_WIKI_RETRIEVAL_ENABLED", "false")
    get_settings.cache_clear()
    assert drop_reason_for_chunk(chunk, wiki_ids={"wiki-sid"}) == "wiki_track"


def test_apply_wiki_weight():
    from app.services.hybrid_search import apply_wiki_weight

    lit = SimpleNamespace(source_id="lit")
    wiki = SimpleNamespace(source_id="wiki-sid")
    combined = np.array([0.9, 0.8])
    out = apply_wiki_weight(combined, [lit, wiki], {"wiki-sid"}, 0.7)
    assert out[0] == pytest.approx(0.9)
    assert out[1] == pytest.approx(0.56)
    assert apply_wiki_weight(combined, [lit, wiki], {"wiki-sid"}, 1.0).tolist() == [0.9, 0.8]
    assert apply_wiki_weight(combined, [lit, wiki], set(), 0.7).tolist() == [0.9, 0.8]


# ── Claims: wiki label + cap ───────────────────────────────────────────────


def test_claims_wiki_only_capped_and_mixed_not():
    from app.pipeline.claim_checker import ClaimVerdict, VerifiedClaim
    from app.services.chat_claims import build_sourced_claims

    wiki_ev = _ev("kb:wiki-sid#c0", "wiki", 0.8)
    lit_ev = _ev("kb:lit#c0", "kb", 0.9)
    verified = [
        VerifiedClaim(
            text="wiki only claim",
            verdict=ClaimVerdict.supported,
            evidence_indices=[0],
        )
    ]
    claims = build_sourced_claims("q", "a", [wiki_ev], verified=verified)
    assert claims is not None and len(claims) == 1
    assert claims[0].confidence == pytest.approx(0.6)
    assert claims[0].chunk_ids == ["wiki-sid#c0"]

    verified_mixed = [
        VerifiedClaim(
            text="mixed claim",
            verdict=ClaimVerdict.supported,
            evidence_indices=[0, 1],
        )
    ]
    claims_mixed = build_sourced_claims(
        "q", "a", [wiki_ev, lit_ev], verified=verified_mixed
    )
    assert claims_mixed is not None and claims_mixed[0].confidence == pytest.approx(0.9)


def test_claims_evidence_keeps_wiki_and_mapping_identity():
    from app.services.citation_coords import claims_evidence, claims_evidence_with_mapping

    wiki_ev = _ev("wiki:materials/epoxy.md", "wiki", 0.8)
    raw_ev = _ev("kb:lit#c0", "kb", 0.9)
    kept = claims_evidence([wiki_ev, raw_ev])
    assert kept == [wiki_ev, raw_ev]
    filtered, mapping = claims_evidence_with_mapping([wiki_ev, raw_ev])
    assert filtered == [wiki_ev, raw_ev]
    assert mapping == {0: 0, 1: 1}


# ── Blend: FTS merge + project isolation ───────────────────────────────────


def test_blend_fts_merge_and_project_gate(monkeypatch):
    import app.services.wiki.retrieve as retrieve_mod

    base = [
        Evidence(
            source="wiki",
            identifier="wiki:materials/epoxy.md",
            title="[Wiki/material] 环氧",
            snippet="环氧固化",
            relevance=0.6,
        )
    ]
    monkeypatch.setattr(retrieve_mod, "search_wiki", lambda q, **kw: list(base))
    monkeypatch.setattr(
        retrieve_mod,
        "get_wiki_store",
        lambda: SimpleNamespace(session_factory=object()),
    )
    fts_rows = [
        {
            "path": "materials/epoxy.md",
            "title": "环氧",
            "kind": "material",
            "snippet": "环氧固化 FTS",
        },
        {"path": "systems/cure.md", "title": "固化", "kind": "system", "snippet": "固化工艺"},
    ]
    monkeypatch.setattr(
        "app.services.wiki.fts.search_fts", lambda factory, q, **kw: list(fts_rows)
    )
    merged = retrieve_mod._wiki_blend_hits("环氧固化", k=5)
    by_id = {e.identifier: e for e in merged}
    # FTS rank beats the keyword hit on the shared page; new page added.
    assert by_id["wiki:materials/epoxy.md"].relevance == pytest.approx(0.9)
    assert "wiki:systems/cure.md" in by_id
    assert len(merged) == 2
    # Project-scoped blends skip FTS entirely (no project dimension in FTS).
    assert retrieve_mod._fts_blend_hits("环氧固化", 5, "proj-A") == []


# ── PageIndex nav: matching + long-doc scenario ────────────────────────────


def test_nav_match_sections():
    from app.services.pageindex.nav_layer import match_sections

    tree = {
        "node_id": "root",
        "title": "长文档",
        "kind": "root",
        "children": [
            {
                "node_id": "n1",
                "title": "概述",
                "kind": "heading",
                "chunk_ids": ["c0"],
                "table_ids": [],
                "children": [],
            },
            {
                "node_id": "n2",
                "title": "磷化工艺参数",
                "kind": "heading",
                "chunk_ids": ["c1"],
                "table_ids": [],
                "children": [],
            },
        ],
    }
    matched = match_sections(tree, "磷化工艺参数")
    assert [n["node_id"] for n, _ in matched] == ["n2"]
    assert match_sections(tree, "不存在的主题") == []


def _seed_long_doc(stores):
    src, chk, toc, factory = stores
    sid = "long-doc-1"
    with commit_session(factory) as session:
        session.add(
            SourceDocument(
                id=sid,
                filename="long.pdf",
                title="长文档",
                source_kind="local",
                content_hash="long",
                full_text="",
                extraction_status="ok",
            )
        )
        rows = []
        for i in range(25):
            if i < 14:
                heading = "概述"
            elif i < 17:
                heading = "磷化工艺参数"
            else:
                heading = "附录"
            rows.append(
                {
                    "text": f"第{i + 1}页内容 磷化工艺参数" if 14 <= i < 17 else f"第{i + 1}页内容 概述附录",
                    "heading_path": heading,
                    "page_no": i + 1,
                }
            )
        chk.replace_for_source_in(session, sid, rows)
    return sid


def test_nav_long_doc_boost_fetch_and_short_doc_passthrough(stores):
    from app.services.pageindex.nav_layer import apply_nav
    from app.services.pageindex.two_stage import build_tree_for_source

    src, chk, toc, factory = stores
    sid = _seed_long_doc(stores)
    built = build_tree_for_source(sid)
    assert built is not None
    row = toc.get(sid)
    assert row is not None and (row.page_count or 0) >= 20

    chunks = chk.get_by_source(sid)
    by_ord = {c.ord: c for c in chunks}
    ev_outside = _ev(f"kb:{sid}#c0", "kb", 0.9, page=1)
    ev_inside = _ev(f"kb:{sid}#c14", "kb", 0.5, page=15)
    boosted, extras, tables, meta = apply_nav("磷化工艺参数", [ev_outside, ev_inside])
    assert meta["docs_navigated"] == 1
    assert meta["sections_matched"] >= 1
    assert meta["chunks_boosted"] >= 1
    boosted_by_id = {e.identifier: e for e in boosted}
    assert boosted_by_id[f"kb:{sid}#c14"].relevance == pytest.approx(0.65)
    # Section chunks 15/16 (ords 15, 16) missed top-k → fetched as extras.
    extra_ords = {c.ord for c in extras}
    assert {15, 16} <= extra_ords
    assert by_ord[14].id  # sanity: seeded rows carry ids

    # Short doc: nav is a no-op.
    short_sid = "short-doc-1"
    with commit_session(factory) as session:
        session.add(
            SourceDocument(
                id=short_sid,
                filename="short.pdf",
                title="短文档",
                source_kind="local",
                content_hash="short",
                full_text="",
                extraction_status="ok",
            )
        )
        chk.replace_for_source_in(
            session,
            short_sid,
            [{"text": "磷化工艺参数短文", "heading_path": "磷化工艺参数", "page_no": 1}],
        )
    build_tree_for_source(short_sid)
    ev_short = _ev(f"kb:{short_sid}#c0", "kb", 0.8, page=1)
    boosted_s, extras_s, _, meta_s = apply_nav("磷化工艺参数", [ev_short])
    assert boosted_s == [ev_short]
    assert extras_s == []
    assert meta_s["docs_navigated"] == 0


def test_coverage_stats_and_stale_rebuild(stores):
    from app.services.pageindex.nav_layer import ensure_tree_current, pageindex_coverage_stats
    from app.services.pageindex.tree_builder import BUILDER_VERSION

    src, chk, toc, factory = stores
    sid = _seed_long_doc(stores)
    toc.put(
        sid,
        {
            "tree": {"node_id": "root", "title": "t", "kind": "root", "children": []},
            "stats": {"page_count": 25, "node_count": 1, "max_depth": 0},
            "doc_kind": "generic",
        },
        builder_version="0",
    )
    stats = pageindex_coverage_stats()
    assert stats["trees_total"] >= 1
    assert stats["stale_version"] >= 1
    assert stats["eligible_with_tree"] >= 1
    row = ensure_tree_current(sid)
    assert row is not None and row.builder_version == BUILDER_VERSION
    stats2 = pageindex_coverage_stats()
    assert stats2["stale_version"] == 0


# ── Tables: OCR heuristic + read path ──────────────────────────────────────


def test_ocr_grid_reconstruction_and_search(tmp_path, monkeypatch):
    import app.services.table_contract as tc

    monkeypatch.setattr(tc, "_tables_path", lambda key: tmp_path / f"{key}.json")
    sid = "ocr-doc-1"
    chunks = [
        SimpleNamespace(
            text=(
                "项目          含量          单位\n"
                "磷酸锌        12.5          %\n"
                "环氧树脂      45.0          %\n"
                "固化剂        8.2           %\n"
                "普通段落文本，不成表格，随便写写就好没有对齐的列结构在这里出现。\n"
            ),
            page_no=2,
        )
    ]
    tables = tc.reconstruct_text_tables(sid, chunks)
    assert len(tables) == 1
    t = tables[0]
    assert t.provenance.get("confidence") == "low"
    assert t.headers == ["项目", "含量", "单位"]
    assert len(t.rows) == 3
    assert t.page_no == 2
    assert tc.save_tables(sid, tables) is not None
    hits = tc.search_source_tables(sid, "磷酸锌含量")
    assert len(hits) == 1 and hits[0].table_id == t.table_id
    assert tc.search_source_tables(sid, "不存在的关键词") == []
    assert tc.reconstruct_text_tables(sid, [SimpleNamespace(text="普通文本\n没有表格\n", page_no=1)]) == []


# ── Rerank parity + probe alpha ────────────────────────────────────────────


def test_rerank_evidence_passthrough_and_applied(monkeypatch):
    import app.services.kb_index as kb_index_mod
    from app.services.rag import RerankScoredItem

    evs = [_ev(f"kb:lit#c{i}", "kb", 0.9 - i * 0.1) for i in range(3)]

    def _not_applied(query, candidates, k):
        items = [
            RerankScoredItem(evidence=c, score=float(c.relevance), original_index=i)
            for i, c in enumerate(candidates[:k])
        ]
        return items, {"applied": False, "backend": "llm", "reason": "llm_rerank_failed"}

    monkeypatch.setattr("app.services.rag.rerank_scored", _not_applied)
    assert kb_index_mod.rerank_evidence("q", evs) == evs

    def _applied(query, candidates, k):
        order = list(reversed(candidates[:k]))
        items = [
            RerankScoredItem(evidence=c, score=float(i), original_index=i)
            for i, c in enumerate(order)
        ]
        return items, {"applied": True, "backend": "cross_encoder", "reason": "ok"}

    monkeypatch.setattr("app.services.rag.rerank_scored", _applied)
    out = kb_index_mod.rerank_evidence("q", evs)
    assert [e.identifier for e in out] == [e.identifier for e in reversed(evs)]


def test_probe_alpha_defaults_to_online(monkeypatch):
    from app.api.kb import QueryTestRequest
    from app.services import kb_query_test

    # Endpoint default: None → run_query_test falls back to kb_hybrid_alpha.
    assert QueryTestRequest(query="q").alpha is None
    monkeypatch.setattr(kb_query_test.kb_index, "kb_enabled", lambda: False)
    monkeypatch.setattr(kb_query_test.kb_index, "kb_stats", lambda: {})
    payload = kb_query_test.run_query_test(query="环氧", top_k=3)
    assert payload["params"]["alpha"] == pytest.approx(get_settings().kb_hybrid_alpha)
    assert payload["params"]["alpha"] == pytest.approx(0.85)
