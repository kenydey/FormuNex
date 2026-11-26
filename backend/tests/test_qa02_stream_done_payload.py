"""QA-02（2026-11-26 审查第一波）：化学工具成功分支补收尾 + 三条流式收尾共享函数。

- ``_stream_done_payload``：三条收尾（化学工具失败 / 化学工具成功 /
  主 token 流）共用的 done 负载构造器，字段齐全（doi_results /
  citation_expand / evidence_provenance / evidence_reviewer /
  reviewer_fix / citation_binding / mode + 三类 notices）；tools_used
  仅化学工具路径含该键。
- 流式化学工具成功分支：必须调 ``_finalize_evidence_fields``，done
  事件须带回其结果（此前该分支只做 claims+gates 就发 done，DOI /
  reviewer / fix-loop / 引用绑定全部缺席）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.services.chat_orchestration as chat_mod
from app.domain.schemas import Evidence
from app.main import app


def _ev(ident="kb:s1#c0"):
    return Evidence(
        source="kb", identifier=ident, title="t", snippet="盐雾 720 h。",
        relevance=0.9,
    )


def _plan(req, settings=None):
    return {
        "question": getattr(req, "question", "q"),
        "prompt": "p",
        "sources": [],
        "kb_used": 1,
        "entity_resolution": None,
        "kg_stats": None,
        "clarification": None,
        "rewritten_query": None,
        "relevant": [_ev()],
        "rerank": {"applied": False, "backend": "none", "reason": "chat_rerank_disabled"},
        "mode": req.mode,
        "selected_skills": [],
        "selected_mcp_servers": [],
        "data_sources": ["kb_evidence"],
    }


def test_stream_done_payload_has_full_fields():
    req = SimpleNamespace(mode="chat")
    payload = chat_mod._stream_done_payload(
        answer="a[^1]",
        citations=[_ev()],
        kb_used=1,
        plan=_plan(req),
        req=req,
        claims=[],
        sources_audit=None,
        gate_notices=[],
        doi_results={"doi:10.1/x": {"ok": True}},
        citation_expand={"n": 1},
        evidence_provenance={"sources": []},
        reviewer={"status": "warning"},
        reviewer_fix=None,
        citation_binding={"stats": {}, "references": []},
    )
    assert payload["type"] == "done"
    assert payload["doi_results"] == {"doi:10.1/x": {"ok": True}}
    assert payload["citation_expand"] == {"n": 1}
    assert payload["evidence_provenance"] == {"sources": []}
    assert payload["evidence_reviewer"] == {"status": "warning"}
    assert payload["citation_binding"] == {"stats": {}, "references": []}
    assert payload["mode"] == "chat"
    assert payload["rag_backend"]
    assert "tools_used" not in payload  # 主 token 流不含该键
    assert payload["notices"] is not None
    assert any(n.get("code") == "reviewer_warning" for n in payload["notices"])


def test_stream_done_payload_tools_used_only_for_chem_tools():
    req = SimpleNamespace(mode="chat")
    payload = chat_mod._stream_done_payload(
        answer="a",
        citations=[],
        kb_used=0,
        plan=_plan(req),
        req=req,
        tools_used=["mol_descriptors"],
    )
    assert payload["tools_used"] == ["mol_descriptors"]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_DEEPSEEK_API_KEY", "sk-test-qa02")
    monkeypatch.setenv("FORMUNEX_LLM_PROVIDER", "deepseek")
    from app.config import get_settings

    get_settings.cache_clear()
    return TestClient(app)


def _parse_events(body: str) -> list[dict]:
    out = []
    for block in body.split("\n\n"):
        line = block.strip()
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


def test_chem_tools_success_branch_finalizes_evidence_fields(monkeypatch, client):
    """化学工具成功分支：done 须带 _finalize_evidence_fields 的全部结果。"""
    import app.services.chat_chem_tools as cct
    import app.services.chat_claims as cc_mod

    monkeypatch.setattr(chat_mod, "_stream_answer_plan", _plan)
    monkeypatch.setattr(cc_mod, "build_sourced_claims", lambda *a, **k: [])
    monkeypatch.setattr(cct, "should_enable_tools", lambda provider, settings: True)
    monkeypatch.setattr(
        cct, "openai_tool_schemas", lambda ctx: [{"type": "function", "function": {"name": "mol_descriptors"}}]
    )
    monkeypatch.setattr(
        cct, "build_tool_context", lambda **kw: {"tools": ["mol_descriptors"]}
    )
    monkeypatch.setattr(
        cct, "build_chat_messages", lambda prompt, ctx: [{"role": "user", "content": prompt}]
    )

    def fake_tool_loop(*, messages, ctx, api_key, model, base_url, max_tokens, settings):
        yield {"type": "tool_start", "name": "mol_descriptors"}
        yield {"type": "token", "delta": "乙醇 LogP 约 -0.3。"}
        yield {"type": "loop_done", "answer": "乙醇 LogP 约 -0.3。", "tools_used": ["mol_descriptors"]}

    monkeypatch.setattr(cct, "run_tool_loop_events", fake_tool_loop)

    calls: dict = {}

    def fake_finalize(question, answer, citations, *, settings, mode, selected_skills,
                     project_id=None, sources=None, domain=None, history=None, structure=None):
        calls["n"] = calls.get("n", 0) + 1
        return (
            answer,
            {"doi:10.1/x": {"ok": True}},  # doi_results
            {"status": "pass"},  # reviewer
            None,  # reviewer_fix
            {"expanded": 1},  # citation_expand
            list(citations),
            {"stats": {"total_anchors": 0, "used": 0, "dropped_out_of_range": 0}, "references": []},
        )

    monkeypatch.setattr(chat_mod, "_finalize_evidence_fields", fake_finalize)

    resp = client.post(
        "/api/chat/stream",
        json={"question": "乙醇 logp?", "sources": [], "response_format": "markdown"},
    )
    assert resp.status_code == 200
    events = _parse_events(resp.text)
    assert events[-1]["type"] == "done"
    done = events[-1]
    assert calls.get("n") == 1, "成功分支必须调 _finalize_evidence_fields"
    assert done["doi_results"] == {"doi:10.1/x": {"ok": True}}
    assert done["citation_expand"] == {"expanded": 1}
    assert done["evidence_reviewer"] == {"status": "pass"}
    assert done["citation_binding"] == {
        "stats": {"total_anchors": 0, "used": 0, "dropped_out_of_range": 0},
        "references": [],
    }
    assert done["mode"] == "chat"
    assert "evidence_provenance" in done
    assert done["tools_used"] == ["mol_descriptors"]
