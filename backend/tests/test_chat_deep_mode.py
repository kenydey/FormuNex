"""Chat 深度模式 (``deep=true``): PageIndex stage-2 reads join the evidence, fail-open with a visible notice."""
from __future__ import annotations

from types import SimpleNamespace

from app.api import chat as chat_api
from app.domain.schemas import Evidence


def _req(**kw):
    return chat_api.ChatRequestValidated(question="盐雾 1000 h 的配方？", **kw)


def _ev(identifier: str, source: str = "kb") -> Evidence:
    return Evidence(source=source, identifier=identifier, title=identifier, snippet="…", relevance=0.5)


def test_off_by_default_leaves_sources_untouched(monkeypatch):
    called = []
    monkeypatch.setattr("app.services.pageindex.two_stage.two_stage_retrieve", lambda *a, **k: called.append(1))
    sources = [_ev("a")]
    out, notices = chat_api._with_deep_evidence(_req(), "q", sources)
    assert out == sources and notices == [] and called == []


def test_deep_reads_go_first_and_deduplicate(monkeypatch):
    seen = {}

    def fake_retrieve(query, **kw):
        seen.update(kw, query=query)
        return SimpleNamespace(documents=[SimpleNamespace(source_id="doc-1")])

    monkeypatch.setattr("app.services.pageindex.two_stage.two_stage_retrieve", fake_retrieve)
    monkeypatch.setattr("app.services.pageindex.two_stage.navigation_evidence",
                        lambda docs, titles=None: [_ev("doc-1#n3", "kb_pageindex"), _ev("a", "kb_pageindex")])
    monkeypatch.setattr("app.db.source_store.get_source_store",
                        lambda: SimpleNamespace(get=lambda sid: SimpleNamespace(title="长专利")))
    out, notices = chat_api._with_deep_evidence(_req(deep=True, project_id="p1"), "盐雾", [_ev("a"), _ev("b")])
    assert [e.identifier for e in out] == ["doc-1#n3", "a", "b"]
    assert out[0].source == "kb_pageindex" and notices == []
    assert seen["deep"] is True and seen["project_id"] == "p1" and seen["query"] == "盐雾"


def test_failure_is_fail_open_with_notice(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("index down")

    monkeypatch.setattr("app.services.pageindex.two_stage.two_stage_retrieve", boom)
    out, notices = chat_api._with_deep_evidence(_req(deep=True), "q", [_ev("a")])
    assert [e.identifier for e in out] == ["a"] and notices[0]["code"] == "deep_mode_failed"


def test_deep_notices_reach_the_answer_notices():
    req = _req(deep=True)
    req._deep_notices = [{"code": "deep_mode_empty", "message": "x"}]
    assert chat_api._retrieval_degradation_notices(0, req) == [{"code": "deep_mode_empty", "message": "x"}]
    assert chat_api._retrieval_degradation_notices(0) == []
