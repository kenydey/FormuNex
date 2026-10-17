"""PUT /api/projects/{id}: the empty-sources guard stays, but an explicit clear is allowed."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import projects as projects_api
from app.db.database import make_engine, make_session_factory
from app.db.project_store import ProjectStore

_SRC = {"identifier": "s1", "title": "文献1", "source": "patents", "snippet": "…", "relevance": 0.9, "url": ""}


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{(tmp_path / 'p.db').as_posix()}")
    store = ProjectStore(make_session_factory(engine))
    monkeypatch.setattr(projects_api, "get_project_store", lambda: store)
    app = FastAPI()
    app.include_router(projects_api.router)
    pid = store.create(title="t").id
    store.update(pid, {"sources": [_SRC], "selected_sources": ["s1"]})
    yield TestClient(app), pid
    engine.dispose()


def test_empty_sources_still_guarded(ctx):
    c, pid = ctx
    r = c.put(f"/api/projects/{pid}", json={"workspace": {"sources": []}})
    assert r.status_code == 200, r.text
    assert len(r.json()["workspace"]["sources"]) == 1


def test_clear_sources_flag(ctx):
    c, pid = ctx
    r = c.put(f"/api/projects/{pid}", json={"workspace": {"sources": [], "selected_sources": []}, "clear_sources": True})
    assert r.status_code == 200, r.text
    assert r.json()["workspace"]["sources"] == []
    assert r.json()["workspace"]["selected_sources"] == []


def test_allow_empty_sources_query(ctx):
    c, pid = ctx
    r = c.put(f"/api/projects/{pid}?allow_empty_sources=1", json={"workspace": {"sources": []}})
    assert r.status_code == 200, r.text
    assert r.json()["workspace"]["sources"] == []


def test_flag_does_not_touch_absent_sources(ctx):
    c, pid = ctx
    r = c.put(f"/api/projects/{pid}", json={"workspace": {"search_query": "x"}, "clear_sources": True})
    assert r.status_code == 200
    assert len(r.json()["workspace"]["sources"]) == 1
