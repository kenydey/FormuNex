"""P1-7: NotebookLM notebook id has one resolution path.

Priority: explicit caller id > project workspace binding > legacy global
config (migration fallback only). Previously three call sites each did
their own lookup (external search: manual only; deep research: binding
only when explicit was None; smart collections: inline project-store
lookup), so a project binding was invisible to external search.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import notebooklm as nb
from app.services import smart_collections as sc


@pytest.fixture()
def settings(monkeypatch):
    monkeypatch.setattr(
        nb, "get_settings",
        lambda: SimpleNamespace(notebooklm_notebook_id="legacy-global"),
    )


def _bound_store(notebook_id):
    detail = SimpleNamespace(
        workspace=SimpleNamespace(notebooklm_notebook_id=notebook_id)
    )
    return SimpleNamespace(get=lambda pid: detail)


def test_explicit_beats_binding_beats_legacy(monkeypatch, settings):
    import app.db.project_store as ps_mod

    monkeypatch.setattr(ps_mod, "get_project_store", lambda: _bound_store("proj-bound"))
    assert nb.resolve_notebook_id(project_id="p1", explicit="manual-id") == "manual-id"
    assert nb.resolve_notebook_id(project_id="p1") == "proj-bound"


def test_binding_missing_falls_back_to_legacy_global(monkeypatch, settings):
    import app.db.project_store as ps_mod

    monkeypatch.setattr(ps_mod, "get_project_store", lambda: _bound_store(None))
    assert nb.resolve_notebook_id(project_id="p1") == "legacy-global"


def test_project_store_error_falls_back_to_legacy_global(monkeypatch, settings):
    import app.db.project_store as ps_mod

    def _boom():
        raise RuntimeError("store down")

    monkeypatch.setattr(ps_mod, "get_project_store", _boom)
    assert nb.resolve_notebook_id(project_id="p1") == "legacy-global"


def test_no_source_returns_none(monkeypatch):
    import app.db.project_store as ps_mod

    monkeypatch.setattr(nb, "get_settings", lambda: SimpleNamespace(notebooklm_notebook_id=""))
    monkeypatch.setattr(ps_mod, "get_project_store", lambda: _bound_store(None))
    assert nb.resolve_notebook_id(project_id="p1") is None
    assert nb.resolve_notebook_id() is None


def test_smart_collection_refresh_uses_project_binding(tmp_path, monkeypatch):
    """刷新链路经同一解析器取项目绑定（不再是内联 lookup）。"""
    import app.db.project_store as ps_mod

    from app.services import literature_manifest as lm

    monkeypatch.setattr(sc, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(lm, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(ps_mod, "get_project_store", lambda: _bound_store("proj-bound"))
    monkeypatch.setattr(nb, "get_settings", lambda: SimpleNamespace(notebooklm_notebook_id=""))

    seen = {}

    def fake_run_search(query, filters, settings=None, notebooklm_notebook_id=None):
        seen["notebooklm_notebook_id"] = notebooklm_notebook_id
        return []

    monkeypatch.setattr(sc, "_run_search", fake_run_search)
    col = sc.create_collection("p1", name="c", query="q")
    sc.refresh_collection("p1", col["collection_id"])
    assert seen["notebooklm_notebook_id"] == "proj-bound"
