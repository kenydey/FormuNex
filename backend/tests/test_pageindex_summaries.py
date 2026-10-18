"""PageIndex ToC node summaries: LLM generation + status machine + trigger API.

Summaries are only generated for a document the user explicitly triggers
(POST /api/kb/sources/{id}/toc-tree/summarize) — never a full-library sweep.
"""
from __future__ import annotations

import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.toc_tree_store import TocTreeStore
from app.services import kb_index
from app.services.pageindex import summaries as summaries_mod
from app.services.pageindex.summaries import generate_summaries
from tests.pageindex_fixtures import patent_cn_basic


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.source_store as source_store_mod
    import app.db.toc_tree_store as toc_store_mod
    from app.db.source_store import SourceStore

    for key, value in {
        "FORMUNEX_API_AUTH_ENABLED": "false",
        "FORMUNEX_CROSSLINGUAL_EXPAND_ENABLED": "false",
        "FORMUNEX_QUERY_REWRITE_ON_EMPTY_ENABLED": "false",
        "FORMUNEX_KG_ENABLED": "false",
        "FORMUNEX_CELERY_EAGER": "true",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    sources = SourceStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", sources)
    monkeypatch.setattr(chunk_store_mod, "_store", ChunkStore(factory))
    trees = TocTreeStore(factory)
    monkeypatch.setattr(toc_store_mod, "_store", trees)

    p = patent_cn_basic()
    sid = sources.create(filename=f"{p.title}.pdf", title=p.title, source_kind="patent",
                         full_text=p.page_marked_text(),
                         content_hash=hashlib.sha256(p.page_marked_text().encode()).hexdigest())
    assert kb_index.index_source(sid, p.page_marked_text(), embed=False) > 0
    yield {"sid": sid, "trees": trees}
    get_settings.cache_clear()


def _summaries(trees, sid):
    tree = trees.get(sid).tree
    out = {}

    def walk(node):
        if node.get("kind") != "root":
            out[node["node_id"]] = node.get("summary")
        for c in node.get("children") or []:
            walk(c)

    walk(tree)
    return out


def test_generate_summaries_fills_nodes_and_marks_done(kb):
    sid, trees = kb["sid"], kb["trees"]
    assert trees.get(sid).summaries_status == "none"
    result = generate_summaries(sid, summarizer=lambda title, text: f"摘要：{title}")
    assert result["summaries_status"] == "done"
    assert result["newly"] == result["total"] > 0
    assert trees.get(sid).summaries_status == "done"
    got = _summaries(trees, sid)
    assert got and all(v and v.startswith("摘要：") for v in got.values())


def test_generate_summaries_is_idempotent(kb):
    sid, trees = kb["sid"], kb["trees"]
    generate_summaries(sid, summarizer=lambda title, text: "摘要")
    second = generate_summaries(sid, summarizer=lambda title, text: "不应覆盖")
    assert second["newly"] == 0 and second["summaries_status"] == "done"
    assert all(v == "摘要" for v in _summaries(trees, sid).values())


def test_generate_summaries_partial_when_llm_fails_for_some(kb):
    sid, trees = kb["sid"], kb["trees"]

    def flaky(title, text):
        return None if "实施例" in title else f"摘要：{title}"

    result = generate_summaries(sid, summarizer=flaky)
    assert result["summaries_status"] == "partial"
    assert 0 < result["summarised"] < result["total"]
    assert trees.get(sid).summaries_status == "partial"

    # Re-triggering retries just the missing nodes.
    result2 = generate_summaries(sid, summarizer=lambda title, text: f"摘要：{title}")
    assert result2["summaries_status"] == "done"


def test_generate_summaries_all_llm_failed_is_partial_not_pending(kb):
    sid, trees = kb["sid"], kb["trees"]
    result = generate_summaries(sid, summarizer=lambda title, text: None)
    assert result["summaries_status"] == "partial"
    assert result["summarised"] == 0


def test_generate_summaries_missing_tree_raises(kb):
    with pytest.raises(KeyError):
        generate_summaries("no-such-source", summarizer=lambda t, x: "摘要")


def test_summarize_endpoint_drives_status_machine(kb, monkeypatch):
    sid, trees = kb["sid"], kb["trees"]
    monkeypatch.setattr(summaries_mod, "_default_summarizer", lambda title, text: f"摘要：{title}")
    from app.main import app

    client = TestClient(app)
    r = client.post(f"/api/kb/sources/{sid}/toc-tree/summarize")
    assert r.status_code == 202, r.text

    deadline = time.time() + 15
    while time.time() < deadline:
        body = client.get(f"/api/kb/sources/{sid}/toc-tree").json()
        if body["summaries_status"] in ("done", "partial"):
            break
        time.sleep(0.2)
    assert body["summaries_status"] == "done"
    got = _summaries(trees, sid)
    assert got and all(v and v.startswith("摘要：") for v in got.values())


def test_summarize_endpoint_404_without_tree(kb):
    from app.main import app

    client = TestClient(app)
    assert client.post("/api/kb/sources/no-such-source/toc-tree/summarize").status_code == 404
