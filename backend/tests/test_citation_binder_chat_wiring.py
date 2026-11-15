"""1-1：bind_citations 接入问答生产路径（RAG 升级第 1 批）。

覆盖：
- ``evidence_to_citation_anchors``：Evidence/dict → anchors，kb: 前缀剥离、
  页/段透传、编号与 citations 列表同序（index 0 → [^1]）。
- ``_bind_citations``：越界 [^n] 清除并计数、无越界时原文不动、fail-open。
- ``_finalize_evidence_fields``（流式）：越界标记清除 + citation_binding 进元数据。
- ``POST /api/chat`` 同步路径（mode=evidence）：越界标记清除 +
  ``citation_binding`` 随 ChatResponse 返回；无越界时答案逐字不动。
- 旧行为对照：``rebind_citations_to_answer`` 仍保留越界标记供 reviewer
  标记（test_chat_conflict_section.py::test_rebind_keeps_hallucinated_marker），
  本批只在 review 结束后的最终答案上 scrub，两者不冲突。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services.citation_binder import evidence_to_citation_anchors


def _ev(identifier="kb:src1#c0", title="t1", snippet="s1", page=None, paragraph=None):
    return Evidence(
        source="literature",
        identifier=identifier,
        title=title,
        snippet=snippet,
        relevance=0.9,
        page=page,
        paragraph=paragraph,
    )


# ── evidence_to_citation_anchors ──────────────────────────────────────────


def test_anchors_follow_citation_order_and_strip_kb_prefix():
    anchors = evidence_to_citation_anchors(
        [
            _ev("kb:doc-A#c0", page=3, paragraph=42),
            _ev("kb:doc-B#c1"),
            {"identifier": "plain-id", "title": "t3", "snippet": "s3", "page": 7},
        ]
    )
    assert [a.chunk_id for a in anchors] == ["kb:doc-A#c0", "kb:doc-B#c1", "plain-id"]
    assert [a.source_id for a in anchors] == ["doc-A", "doc-B", "plain-id"]
    assert anchors[0].page == 3 and anchors[0].paragraph == 42
    assert anchors[2].page == 7
    assert anchors[0].text == "s1"


def test_anchors_skip_unidentifiable_entries():
    anchors = evidence_to_citation_anchors([{"title": "", "snippet": "x"}, _ev()])
    assert len(anchors) == 1
    assert anchors[0].chunk_id == "kb:src1#c0"


def test_anchors_empty_in_empty_out():
    assert evidence_to_citation_anchors([]) == []
    assert evidence_to_citation_anchors(None) == []


# ── _bind_citations ───────────────────────────────────────────────────────


def test_bind_drops_out_of_range_and_counts():
    import app.services.chat_orchestration as chat_mod

    answer, binding = chat_mod._bind_citations(
        "结论 A[^1]，结论 B[^99]，结论 C[^2]。",
        [_ev("kb:src1#c0"), _ev("kb:src2#c1")],
    )
    assert "[^99]" not in answer
    assert "[^1]" in answer and "[^2]" in answer
    assert binding["stats"] == {
        "total_anchors": 2,
        "used": 2,
        "dropped_out_of_range": 1,
    }
    assert [r["index"] for r in binding["references"]] == [1, 2]
    assert binding["references"][0]["source_id"] == "src1"


def test_bind_leaves_clean_answer_byte_identical():
    import app.services.chat_orchestration as chat_mod

    answer, binding = chat_mod._bind_citations(
        "结论 A[^1]，结论 B[^2]。", [_ev("kb:src1#c0"), _ev("kb:src2#c1")]
    )
    assert answer == "结论 A[^1]，结论 B[^2]。"
    assert binding["stats"]["dropped_out_of_range"] == 0
    assert binding["stats"]["used"] == 2


def test_bind_with_no_citations_drops_every_marker():
    import app.services.chat_orchestration as chat_mod

    answer, binding = chat_mod._bind_citations("幻觉引用[^1]。", [])
    assert "[^1]" not in answer
    assert binding["stats"] == {
        "total_anchors": 0,
        "used": 0,
        "dropped_out_of_range": 1,
    }


def test_bind_is_fail_open(monkeypatch):
    """anchors 构造抛错也不阻断问答：原答案 + None。"""
    import app.services.chat_orchestration as chat_mod
    import app.services.citation_binder as binder_mod

    monkeypatch.setattr(
        binder_mod,
        "evidence_to_citation_anchors",
        lambda evidence: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    answer, binding = chat_mod._bind_citations("原文[^1]。", [_ev()])
    assert answer == "原文[^1]。"
    assert binding is None


# ── _finalize_evidence_fields（流式） ─────────────────────────────────────


def _finalize_settings():
    from types import SimpleNamespace

    return SimpleNamespace(
        evidence_doi_verify_enabled=False,
        citation_expand_enabled=False,
    )


def test_finalize_binds_and_reports_stats(monkeypatch):
    """流式：越界 [^99] 在 _finalize 内清除，stats 进 citation_binding。"""
    import app.services.chat_orchestration as chat_mod
    import app.services.evidence_synthesis as es

    monkeypatch.setattr(es, "evidence_mode_active", lambda mode, settings: True)
    monkeypatch.setattr(
        es, "postprocess_evidence_answer", lambda answer, settings=None: (answer, {})
    )
    monkeypatch.setattr(
        chat_mod, "_run_evidence_review", lambda *a, **k: {"status": "pass"}
    )

    cits = [_ev("kb:src1#c0"), _ev("kb:src2#c1")]
    out = chat_mod._finalize_evidence_fields(
        "q",
        "结论[^1]，幻觉[^99]。",
        cits,
        settings=_finalize_settings(),
        mode="evidence",
        selected_skills=None,
    )
    assert len(out) == 7
    answer, _d, _r, _rf, _ce, citations, binding = out
    assert "[^99]" not in answer
    assert "[^1]" in answer
    assert citations == cits
    assert binding["stats"] == {
        "total_anchors": 2,
        "used": 1,
        "dropped_out_of_range": 1,
    }


# ── POST /api/chat 同步路径 ───────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client() -> TestClient:
    from app.main import app

    return TestClient(app)


def _sources():
    return [
        {
            "source": "literature",
            "identifier": "kb:src1#c0",
            "title": "磷酸锌",
            "snippet": "磷酸锌添加量 3-5 wt% 时涂层附着力提升。",
            "relevance": 0.9,
        },
        {
            "source": "literature",
            "identifier": "kb:src2#c1",
            "title": "硅烷",
            "snippet": "硅烷偶联剂预处理铝基材。",
            "relevance": 0.9,
        },
    ]


def test_sync_chat_drops_hallucinated_marker_and_reports_binding(monkeypatch):
    """同步 evidence 路径：[^99] 被清除，citation_binding 带 dropped=1。"""
    import app.services.chat_orchestration as chat_mod

    cits = _sources()
    monkeypatch.setattr(
        chat_mod,
        "answer_question",
        lambda *a, **k: (
            "磷酸锌添加量 3-5 wt%[^1]，幻觉结论[^99]。",
            [
                Evidence(
                    source="literature",
                    identifier="kb:src1#c0",
                    title="磷酸锌",
                    snippet="磷酸锌添加量 3-5 wt% 时涂层附着力提升。",
                    relevance=0.9,
                ),
                Evidence(
                    source="literature",
                    identifier="kb:src2#c1",
                    title="硅烷",
                    snippet="硅烷偶联剂预处理铝基材。",
                    relevance=0.9,
                ),
            ],
        ),
    )
    monkeypatch.setattr("app.services.kb_index.search_chunks", lambda *a, **k: [])
    resp = _client().post(
        "/api/chat",
        json={"question": "磷酸锌添加量多少?", "sources": cits, "mode": "evidence"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "[^99]" not in data["answer"], data["answer"]
    assert "[^1]" in data["answer"]
    binding = data["citation_binding"]
    assert binding is not None
    assert binding["stats"]["dropped_out_of_range"] == 1
    assert binding["stats"]["total_anchors"] == 2
    assert binding["stats"]["used"] == 1


def test_sync_chat_clean_answer_unchanged(monkeypatch):
    """无越界时：同步 evidence 路径答案逐字不动，stats dropped=0。"""
    import app.services.chat_orchestration as chat_mod

    monkeypatch.setattr(
        chat_mod,
        "answer_question",
        lambda *a, **k: (
            "磷酸锌添加量 3-5 wt%[^1]，硅烷预处理[^2]。",
            [
                Evidence(
                    source="literature",
                    identifier="kb:src1#c0",
                    title="磷酸锌",
                    snippet="磷酸锌添加量 3-5 wt% 时涂层附着力提升。",
                    relevance=0.9,
                ),
                Evidence(
                    source="literature",
                    identifier="kb:src2#c1",
                    title="硅烷",
                    snippet="硅烷偶联剂预处理铝基材。",
                    relevance=0.9,
                ),
            ],
        ),
    )
    monkeypatch.setattr("app.services.kb_index.search_chunks", lambda *a, **k: [])
    resp = _client().post(
        "/api/chat",
        json={
            "question": "磷酸锌添加量多少?",
            "sources": _sources(),
            "mode": "evidence",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "磷酸锌添加量 3-5 wt%[^1]，硅烷预处理[^2]。"
    assert data["citation_binding"]["stats"]["dropped_out_of_range"] == 0
    assert data["citation_binding"]["stats"]["used"] == 2
