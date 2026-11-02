"""Stage 5 / SR-093: full rebuilds run in the background and swap atomically."""

from __future__ import annotations

import os
import threading
import time

import pytest

faiss = pytest.importorskip("faiss")
np = pytest.importorskip("numpy")

from app.db import chunk_store as cs_mod
from app.db.database import Base, make_engine, make_session_factory
from app.services import kb_ann


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    def _tmpdir():
        d = str(tmp_path / "kb_ann")
        os.makedirs(d, exist_ok=True)
        return d

    monkeypatch.setattr(kb_ann, "_index_dir", _tmpdir)
    kb_ann.invalidate()
    monkeypatch.setattr(cs_mod, "_store", None)
    yield
    kb_ann.invalidate()
    monkeypatch.setattr(cs_mod, "_store", None)


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    import app.db.database as db_mod

    engine = make_engine(f"sqlite:///{tmp_path}/ann_bg.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    yield factory


def _normed(dim, seed):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=dim).astype(np.float32)
    v /= np.linalg.norm(v)
    return [float(x) for x in v]


def _seed(n, dim=32, model="test-model", start=0):
    return [{"text": f"chunk text {i} about chemical formulation",
             "embedding": _normed(dim, 1000 + i),
             "embedding_model": model} for i in range(start, start + n)]


def test_rebuild_serves_old_until_swap(tmpdb, monkeypatch):
    store = cs_mod.get_chunk_store()
    store.replace_for_source("src-1", _seed(20))
    assert kb_ann.ensure_index()["ready"] is True
    q = _normed(32, 4242)
    assert kb_ann.search("test-model", q, 5) is not None

    # Force a FULL rebuild (not the delta): drop the persisted manifest
    # while the in-memory index still serves.
    store.replace_for_source("src-2", _seed(20, start=100))
    os.remove(os.path.join(kb_ann._index_dir(), "manifest.json"))
    res = kb_ann.ensure_index()
    assert res["ready"] is True  # old index still serving
    assert res["buckets"]["buckets"]["test-model"]["count"] == 20
    assert kb_ann.wait_for_build(timeout_s=30.0) is True
    res = kb_ann.ensure_index()
    assert res["buckets"]["buckets"]["test-model"]["count"] == 40


def test_search_is_not_blocked_by_a_slow_rebuild(tmpdb, monkeypatch):
    store = cs_mod.get_chunk_store()
    store.replace_for_source("src-1", _seed(20))
    assert kb_ann.ensure_index()["ready"] is True
    q = _normed(32, 4242)
    assert kb_ann.search("test-model", q, 5) is not None

    started = threading.Event()
    release = threading.Event()
    real_scan = kb_ann._scan_vectors

    def slow_scan():
        started.set()
        assert release.wait(timeout=30.0)
        return real_scan()

    monkeypatch.setattr(kb_ann, "_scan_vectors", slow_scan)
    store.replace_for_source("src-2", _seed(5, start=100))
    os.remove(os.path.join(kb_ann._index_dir(), "manifest.json"))
    assert kb_ann.ensure_index()["ready"] is True  # background job started
    assert started.wait(timeout=10.0)
    t0 = time.monotonic()
    hits = kb_ann.search("test-model", q, 5)  # must not wait for the scan
    assert hits is not None and time.monotonic() - t0 < 5.0
    release.set()
    assert kb_ann.wait_for_build(timeout_s=30.0) is True
    assert kb_ann.ensure_index()["buckets"]["buckets"]["test-model"]["count"] == 25


def test_invalidate_supersedes_a_running_rebuild(tmpdb, monkeypatch):
    store = cs_mod.get_chunk_store()
    store.replace_for_source("src-1", _seed(20))
    assert kb_ann.ensure_index()["ready"] is True

    release = threading.Event()
    real_scan = kb_ann._scan_vectors

    def slow_scan():
        assert release.wait(timeout=30.0)
        return real_scan()

    monkeypatch.setattr(kb_ann, "_scan_vectors", slow_scan)
    store.replace_for_source("src-2", _seed(5, start=100))
    os.remove(os.path.join(kb_ann._index_dir(), "manifest.json"))
    assert kb_ann.ensure_index()["ready"] is True
    kb_ann.invalidate()  # bumps the generation: the job must not publish
    release.set()
    assert kb_ann.wait_for_build(timeout_s=30.0) is False
    assert kb_ann._buckets == {}
