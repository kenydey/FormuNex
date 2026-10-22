"""P1-2: chunks/by-source must report the full total and page past 200.

Regression for the silent truncation: a source with >200 chunks used to
look like a 200-chunk document to the frontend (no total field, single
request), so tree nodes past chunk 200 rendered Empty.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import func

from app.api import kb as kb_api
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.models import DocumentChunk


def _row(i: int):
    return SimpleNamespace(
        id=f"c{i}", source_id="s1", ord=i, text=f"text{i}",
        heading_path="", page_no=None, paragraph_idx=None,
        offset_start=None, offset_end=None, meta={},
    )


class _FakeStore:
    def __init__(self, n: int):
        self._rows = [_row(i) for i in range(n)]

    def get_by_source(self, source_id, *, limit=None, offset=0):
        rows = self._rows[offset:]
        return rows[:limit] if limit is not None else rows

    def count_by_source(self, source_id):
        return len(self._rows)


def test_chunks_total_exposes_full_count_and_pages_past_default_limit(monkeypatch):
    import app.db.chunk_store as chunk_store_mod

    monkeypatch.setattr(chunk_store_mod, "get_chunk_store", lambda: _FakeStore(250))

    first = kb_api.chunks_by_source("s1", limit=200, offset=0)
    assert first.total == 250
    assert len(first.chunks) == 200
    assert [c.ord for c in first.chunks[:3]] == [0, 1, 2]

    tail = kb_api.chunks_by_source("s1", limit=200, offset=200)
    assert tail.total == 250
    assert len(tail.chunks) == 50
    assert tail.chunks[-1].ord == 249


@pytest.fixture()
def factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


def test_chunk_store_count_by_source_matches_rows(factory):
    store = ChunkStore(factory)
    store.replace_for_source(
        "long-doc",
        [{"text": f"chunk {i}", "heading_path": "", "page_no": None} for i in range(250)],
    )
    assert store.count_by_source("long-doc") == 250
    assert store.count_by_source("missing-source") == 0
    page = store.get_by_source("long-doc", limit=200, offset=200)
    assert len(page) == 50
    assert page[0].ord == 200
