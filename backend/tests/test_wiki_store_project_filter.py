"""B2（2026-11-26 全面审查）：Wiki ``list_pages`` 项目过滤改为项目 OR 全局。

全局页（``project_id IS NULL``）在项目内关键词检索中必须可见，与
``chunk_store`` / ``entity_store`` 的口径一致；跨项目页仍不可见；
``upsert_page`` 的跨项目路径冲突 fail-closed 不受此改动影响。
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.wiki_store import WikiStore


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_WIKI_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def wiki(tmp_path):
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{tmp_path}/wiki_b2.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = WikiStore(factory, root=wiki_root)
    store.upsert_page(
        path="global.md", kind="material", title="全局页",
        norm_key="global", entity_id=None,
        markdown="# 全局页\n\nPVAM 全局内容。\n",
        project_id=None,
    )
    store.upsert_page(
        path="p1.md", kind="material", title="P1 页",
        norm_key="p1", entity_id=None,
        markdown="# P1 页\n\nPVAM 项目一内容。\n",
        project_id="p1",
    )
    store.upsert_page(
        path="p2.md", kind="theme", title="P2 页",
        norm_key="p2", entity_id=None,
        markdown="# P2 页\n\nPVAM 项目二内容。\n",
        project_id="p2",
    )
    return store


def test_global_page_visible_inside_a_project(wiki):
    paths = {r.path for r in wiki.list_pages(project_id="p1")}
    assert paths == {"global.md", "p1.md"}


def test_other_project_page_not_visible(wiki):
    paths = {r.path for r in wiki.list_pages(project_id="p2")}
    assert paths == {"global.md", "p2.md"}


def test_no_project_filter_returns_everything(wiki):
    paths = {r.path for r in wiki.list_pages()}
    assert paths == {"global.md", "p1.md", "p2.md"}


def test_kind_filter_still_applies_with_project_filter(wiki):
    paths = {r.path for r in wiki.list_pages(project_id="p1", kind="material")}
    assert paths == {"global.md", "p1.md"}
    paths = {r.path for r in wiki.list_pages(project_id="p1", kind="theme")}
    assert paths == set()
