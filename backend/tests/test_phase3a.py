"""Phase 3A: 3-10 rule-set batch recompute + 3-11 PageIndex result cache.

3-9 (WS <150 ms) lives in ``test_plm_ws_perf.py``; the ``perf`` marker
(pyproject) makes ``pytest -m perf`` the full performance regression suite.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


# --------------------------------------------------------------------------- 3-10 fixtures


@pytest.fixture()
def recompute_db(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'phase3a.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion
    from app.db.models_plm import (
        ClassificationResultRow,
        ProductRevisionRow,
        ProductRow,
        RegulatoryDocumentRow,
    )
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow
    from app.services.plm.ghs import service as ghs_service

    with default_session_factory()() as session:
        xylene = SubstanceRow(id=str(uuid.uuid4()), cas_no="1330-20-7", name_zh="二甲苯",
                              name_en="xylene", aliases=[], hazardous_catalog_no=2289)
        session.add(xylene)
        session.flush()
        session.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=xylene.id,
                                               hazard_class="flammable_liquid", category="3",
                                               h_code="H226", source="cn_catalog"))
        product = ProductRow(id=str(uuid.uuid4()), code="FX-100", name_zh="FX-100 底漆",
                             name_en="FX-100 Primer")
        session.add(product)
        session.flush()
        revision = ProductRevisionRow(id=str(uuid.uuid4()), product_id=product.id, rev="A",
                                      formulation_version_id="fv-3a", state="draft")
        session.add(revision)
        session.add(FormulationVersion(
            id="fv-3a", lineage_id="lin-3a", version=1, domain="coating", name="底漆 v1",
            snapshot={"ingredients": [
                {"name": "xylene", "zh_name": "二甲苯", "cas_no": "1330-20-7", "weight_pct": 60},
                {"name": "water", "cas_no": "7732-18-5", "weight_pct": 40}]},
        ))
        session.flush()
        # Row 1: stale version, linked formulation + citing SDS document.
        stale1 = ClassificationResultRow(
            id=str(uuid.uuid4()), revision_id=revision.id, formulation_version_id="fv-3a",
            rule_set_version="gb30000-2013.v0", inputs_hash="old-hash-1",
            result={"h_codes": ["H999"], "p_codes": [], "signal_word": None,
                    "pictograms": [], "hazards": []}, trace=[])
        # Row 2: stale version, no formulation version → cannot recompute.
        stale2 = ClassificationResultRow(
            id=str(uuid.uuid4()), revision_id=revision.id, formulation_version_id=None,
            rule_set_version="gb30000-2013.v0", inputs_hash="old-hash-2",
            result={"h_codes": [], "p_codes": [], "signal_word": None,
                    "pictograms": [], "hazards": []}, trace=[])
        session.add_all([stale1, stale2])
        session.flush()
        doc = RegulatoryDocumentRow(
            id=str(uuid.uuid4()), revision_id=revision.id, doc_type="sds", lang="zh-CN",
            version_no=1, status="draft",
            content={"meta": {"classification_id": stale1.id,
                               "classification_properties": {"flash_point_c": 30,
                                                             "initial_boiling_point_c": 140}}})
        session.add(doc)
        # Row 3: stale version, but stored result already equals the fresh
        # classification → recompute must report "unchanged". It needs its own
        # citing document carrying the same properties, otherwise recompute
        # defaults properties to {} and the outcome legitimately differs.
        fresh = ghs_service.classify_items(
            session, [{"name": "二甲苯", "cas": "1330-20-7", "pct": 60},
                       {"name": "水", "cas": "7732-18-5", "pct": 40}],
            {"flash_point_c": 30, "initial_boiling_point_c": 140})
        stale3 = ClassificationResultRow(
            id=str(uuid.uuid4()), revision_id=revision.id, formulation_version_id="fv-3a",
            rule_set_version="gb30000-2013.v0", inputs_hash=fresh.inputs_hash,
            result=fresh.to_dict(), trace=[])
        session.add(stale3)
        session.flush()
        session.add(RegulatoryDocumentRow(
            id=str(uuid.uuid4()), revision_id=revision.id, doc_type="label", lang="zh-CN",
            version_no=1, status="draft",
            content={"meta": {"classification_id": stale3.id,
                               "classification_properties": {"flash_point_c": 30,
                                                             "initial_boiling_point_c": 140}}}))
        session.commit()
        ids = {"product_id": product.id, "revision_id": revision.id,
               "stale1": stale1.id, "stale2": stale2.id, "stale3": stale3.id}
    from app.main import app

    yield TestClient(app), ids
    get_settings.cache_clear()


def test_find_affected_groups_by_product(recompute_db):
    from app.db.database import default_session_factory
    from app.services.plm.release import ruleset_recompute as rr
    from app.services.plm.ghs.ruleset import DEFAULT_RULESET

    _api, ids = recompute_db
    with default_session_factory()() as session:
        affected = rr.find_affected_products(session, DEFAULT_RULESET)
    assert len(affected) == 1
    entry = affected[0]
    assert entry["product_id"] == ids["product_id"]
    assert len(entry["classifications"]) == 3
    by_id = {c["classification_id"]: c for c in entry["classifications"]}
    assert by_id[ids["stale1"]]["documents"][0]["doc_type"] == "sds"
    assert by_id[ids["stale2"]]["formulation_version_id"] is None


def test_recompute_dry_run_reports_changed_unchanged_manual(recompute_db):
    from app.db.database import default_session_factory
    from app.services.plm.release import ruleset_recompute as rr
    from app.services.plm.ghs.ruleset import DEFAULT_RULESET

    _api, ids = recompute_db
    with default_session_factory()() as session:
        out = rr.recompute_ruleset(session, DEFAULT_RULESET, apply=False)
    assert out["applied"] is False
    assert out["total_rows"] == 3 and out["changed"] == 1
    assert out["unchanged"] == 1 and out["needs_manual"] == 1
    by_id = {r["classification_id"]: r for r in out["results"]}
    assert by_id[ids["stale1"]]["status"] == "changed"
    assert by_id[ids["stale1"]]["properties_defaulted"] is False  # from citing SDS doc
    assert by_id[ids["stale3"]]["status"] == "unchanged"
    assert by_id[ids["stale2"]]["status"] == "needs_manual"
    # Dry run persists nothing.
    with default_session_factory()() as session:
        from app.db.models_plm import ClassificationResultRow
        assert session.query(ClassificationResultRow).count() == 3


def test_recompute_apply_persists_new_rows_old_rows_untouched(recompute_db):
    from app.db.database import default_session_factory
    from app.db.models_plm import ClassificationResultRow
    from app.services.plm.release import ruleset_recompute as rr
    from app.services.plm.ghs.ruleset import DEFAULT_RULESET

    _api, ids = recompute_db
    with default_session_factory()() as session:
        out = rr.recompute_ruleset(session, DEFAULT_RULESET, apply=True)
        session.commit()
    by_id = {r["classification_id"]: r for r in out["results"]}
    assert by_id[ids["stale1"]]["new_classification_id"]
    assert by_id[ids["stale3"]]["new_classification_id"]
    with default_session_factory()() as session:
        assert session.query(ClassificationResultRow).count() == 5  # 3 old + 2 new
        old = session.get(ClassificationResultRow, ids["stale1"])
        assert old.rule_set_version == "gb30000-2013.v0"  # old row never mutated
        new = session.get(ClassificationResultRow, by_id[ids["stale1"]]["new_classification_id"])
        assert new.rule_set_version == DEFAULT_RULESET


def test_recompute_unknown_version_rejected(recompute_db):
    from app.db.database import default_session_factory
    from app.services.plm.release import ruleset_recompute as rr

    _api, _ids = recompute_db
    with default_session_factory()() as session:
        with pytest.raises(FileNotFoundError):
            rr.recompute_ruleset(session, "no-such-ruleset", apply=False)


def test_recompute_endpoint_dry_run(recompute_db):
    from app.services.plm.ghs.ruleset import DEFAULT_RULESET

    api, _ids = recompute_db
    r = api.post(f"/api/plm/rulesets/{DEFAULT_RULESET}/recompute", params={"apply": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is False and body["affected_products"] == 1 and body["total_rows"] == 3
    r = api.post("/api/plm/rulesets/no-such-ruleset/recompute")
    assert r.status_code == 404


# --------------------------------------------------------------------------- 3-11 PageIndex cache


@pytest.fixture()
def pageindex_cache(monkeypatch):
    from app.services.pageindex import two_stage

    monkeypatch.setattr(two_stage, "corpus_fingerprint", lambda **kw: "fp-1")
    two_stage.invalidate_pageindex_cache()
    yield two_stage
    two_stage.invalidate_pageindex_cache()


def _fake_chunks():
    return [SimpleNamespace(id="c1", source_id="doc-a", text="实施例 1 组成"),
            SimpleNamespace(id="c2", source_id="doc-a", text="性能表")]


def test_pageindex_cache_hit_serves_without_retrieval(pageindex_cache, monkeypatch):
    from app.services import hybrid_search as hs_mod

    calls: list[str] = []

    def fake_hybrid(query, **kw):
        calls.append(query)
        return _fake_chunks()

    monkeypatch.setattr(hs_mod, "hybrid_search", fake_hybrid)
    first = pageindex_cache.two_stage_retrieve("实施例组成", use_llm=False, deep=False)
    second = pageindex_cache.two_stage_retrieve("实施例组成", use_llm=False, deep=False)
    assert calls == ["实施例组成"], "second identical query must hit the cache"
    assert second is first, "cache returns the same result object"
    assert second.to_dict()["query"] == "实施例组成"


def test_pageindex_cache_miss_on_different_query_and_params(pageindex_cache, monkeypatch):
    from app.services import hybrid_search as hs_mod

    calls: list[tuple] = []
    monkeypatch.setattr(hs_mod, "hybrid_search",
                        lambda q, **kw: (calls.append((q, kw.get("top_k"))), _fake_chunks())[1])
    pageindex_cache.two_stage_retrieve("实施例组成", use_llm=False, deep=False)
    pageindex_cache.two_stage_retrieve("另一查询", use_llm=False, deep=False)
    pageindex_cache.two_stage_retrieve("实施例组成", use_llm=False, deep=False, top_k=5)
    assert len(calls) == 3


def test_pageindex_cache_invalidation_on_fingerprint_change(pageindex_cache, monkeypatch):
    from app.services import hybrid_search as hs_mod

    calls: list[str] = []
    monkeypatch.setattr(hs_mod, "hybrid_search",
                        lambda q, **kw: (calls.append(q), _fake_chunks())[1])
    pageindex_cache.two_stage_retrieve("实施例组成", use_llm=False, deep=False)
    assert len(calls) == 1
    # A document changed → fingerprint moves → cache must miss.
    monkeypatch.setattr(pageindex_cache, "corpus_fingerprint", lambda **kw: "fp-2")
    pageindex_cache.two_stage_retrieve("实施例组成", use_llm=False, deep=False)
    assert len(calls) == 2
    # Explicit invalidation drops everything.
    pageindex_cache.invalidate_pageindex_cache()
    pageindex_cache.two_stage_retrieve("实施例组成", use_llm=False, deep=False)
    assert len(calls) == 3


def test_pageindex_cache_bypass_when_fingerprint_unavailable(pageindex_cache, monkeypatch):
    from app.services import hybrid_search as hs_mod

    calls: list[str] = []
    monkeypatch.setattr(hs_mod, "hybrid_search",
                        lambda q, **kw: (calls.append(q), _fake_chunks())[1])
    monkeypatch.setattr(pageindex_cache, "corpus_fingerprint", lambda **kw: None)
    pageindex_cache.two_stage_retrieve("实施例组成", use_llm=False, deep=False)
    pageindex_cache.two_stage_retrieve("实施例组成", use_llm=False, deep=False)
    assert len(calls) == 2, "no fingerprint → fail-open, never serve a possibly-stale hit"


def test_pageindex_cache_ttl_expiry(pageindex_cache, monkeypatch):
    import time as _time

    from app.services import hybrid_search as hs_mod

    calls: list[str] = []
    monkeypatch.setattr(hs_mod, "hybrid_search",
                        lambda q, **kw: (calls.append(q), _fake_chunks())[1])
    pageindex_cache.two_stage_retrieve("实施例组成", use_llm=False, deep=False)
    assert len(calls) == 1
    # Age the single entry past TTL.
    for key in list(pageindex_cache._PAGEINDEX_CACHE):
        at, result = pageindex_cache._PAGEINDEX_CACHE[key]
        pageindex_cache._PAGEINDEX_CACHE[key] = (at - pageindex_cache._PAGEINDEX_CACHE_TTL_S - 1, result)
    pageindex_cache.two_stage_retrieve("实施例组成", use_llm=False, deep=False)
    assert len(calls) == 2


def test_corpus_fingerprint_moves_on_chunk_and_tree_change(tmp_path, monkeypatch):
    """Fingerprint tracks the stores retrieval reads: chunk writes (generation)
    and tree writes (content hash) must both invalidate; a store swapped onto
    another engine must not collide with the previous store's fingerprint."""
    import app.db.chunk_store as chunk_store_mod
    import app.db.toc_tree_store as toc_store_mod
    from app.db.chunk_store import ChunkStore
    from app.db.models import Base
    from app.db.toc_tree_store import TocTreeStore
    from app.services.pageindex.two_stage import corpus_fingerprint
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    def make_stores(path):
        engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)
        return ChunkStore(factory), TocTreeStore(factory)

    chunks1, trees1 = make_stores(tmp_path / "fp1.db")
    monkeypatch.setattr(chunk_store_mod, "_store", chunks1)
    monkeypatch.setattr(toc_store_mod, "_store", trees1)
    fp1 = corpus_fingerprint()
    assert fp1
    # Chunk write bumps the chunk-store generation.
    chunks1.replace_for_source("doc-1", [{"text": "实施例 1", "page_no": 1}])
    fp2 = corpus_fingerprint()
    assert fp2 and fp2 != fp1, "chunk write must move the fingerprint"
    # Tree write changes the tree-content hash (update_tree touches no timestamp).
    trees1.put("doc-1", {"tree": {"nodes": [{"title": "实施例"}]}, "stats": {}}, builder_version="1")
    fp3 = corpus_fingerprint()
    assert fp3 and fp3 != fp2, "tree write must move the fingerprint"
    # Same content on another engine → different fingerprint (no id() collision).
    chunks2, trees2 = make_stores(tmp_path / "fp2.db")
    monkeypatch.setattr(chunk_store_mod, "_store", chunks2)
    monkeypatch.setattr(toc_store_mod, "_store", trees2)
    assert corpus_fingerprint() != fp1
