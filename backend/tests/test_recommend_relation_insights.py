"""Async recommend path carries the KG relation insights into the research block (leaderboard banner)."""
from __future__ import annotations

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.domain.project_workspace import ProjectWorkspace


class _FakeTracker:
    def __init__(self, *a, **k):
        pass

    def emit(self, *a, **k):
        pass

    def thought(self, *a, **k):
        pass

    def finish(self, *a, **k):
        pass


class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def model_dump(self):
        return dict(self._payload)


_INSIGHTS = [{"component": "KH-550", "cas_no": "919-30-2", "substitutes": ["KH-560"],
              "relations": [{"type": "SUBSTITUTES", "target": "e2", "confidence": 0.8}]}]


def _run(monkeypatch, payload: dict):
    from app.worker import tasks as wt

    seen: dict = {}

    def fake_recommend(body):
        seen["relation_insight"] = body.relation_insight
        return _FakeResp({"formulas": [], "scored": [], "engine": "llm", "warnings": [], "grounded_evidence": [],
                          "recommend_id": "r1", "relation_insights": _INSIGHTS if body.relation_insight else []})

    monkeypatch.setattr("app.api.formulations.recommend_formulations", fake_recommend)
    monkeypatch.setattr(wt, "ThinkingTracker", _FakeTracker)
    monkeypatch.setattr(wt, "dispatch_kb_ingest", lambda *a, **k: None)
    monkeypatch.setattr(wt, "persist_result", lambda *a, **k: None)
    monkeypatch.setattr(wt, "_persist_terminal", lambda *a, **k: None)
    out = wt.run_recommend_task.apply(args=(payload,), task_id="task-ri").get()
    return out, seen


def test_task_requests_and_returns_relation_insights(monkeypatch):
    out, seen = _run(monkeypatch, {"requirement": {"domain": "anticorrosion_coating"}, "relation_insight": True})
    assert seen["relation_insight"] is True
    assert out["research"]["relation_insights"] == _INSIGHTS


def test_task_without_flag_skips_insights(monkeypatch):
    out, seen = _run(monkeypatch, {"requirement": {"domain": "anticorrosion_coating"}})
    assert seen["relation_insight"] is False
    assert out["research"]["relation_insights"] == []


def test_workspace_research_block_keeps_insights():
    ws = ProjectWorkspace.model_validate({"research": {
        "requirement_headline": "h", "evidence": [], "mechanism": "", "recommended": [], "chat_markdown": "",
        "relation_insights": _INSIGHTS}})
    assert ws.research is not None
    assert ws.model_dump()["research"]["relation_insights"] == _INSIGHTS
    legacy = ProjectWorkspace.model_validate({"research": {
        "requirement_headline": "h", "evidence": [], "mechanism": "", "recommended": [], "chat_markdown": ""}})
    assert legacy.research is not None and legacy.research.relation_insights == []


def test_recommend_endpoint_forwards_flag(monkeypatch):
    from app.api import research as research_api
    from app.main import app

    captured: dict = {}

    def fake_submit(task, payload, kind, **kw):
        captured["payload"] = payload
        return JSONResponse(status_code=202, content={"task_id": "t", "stream_url": "", "status_url": ""})

    monkeypatch.setattr(research_api, "submit", fake_submit)
    monkeypatch.setattr(research_api, "_enqueue_outbox", lambda *a, **k: None)
    c = TestClient(app)
    r = c.post("/api/research/recommend", json={"domain": "anticorrosion_coating"})
    assert r.status_code == 202, r.text
    assert captured["payload"]["relation_insight"] is True
    assert "relation_insight" not in captured["payload"]["requirement"]
    c.post("/api/research/recommend", json={"domain": "anticorrosion_coating", "relation_insight": False})
    assert captured["payload"]["relation_insight"] is False
