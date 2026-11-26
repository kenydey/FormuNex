"""QA-01（2026-11-26 审查第一波）：reviewer 状态进 notices + warning-only 影子 ReviewRun。

- ``_reviewer_notices``：warning / failure 产生用户可见提示（此前只有
  error / None 才产生，warning/failure 只躺在响应 JSON 里前端不看的字段上）。
- ``write_shadow_review_run``：fix-loop 关时 warning/failure 仍落盘
  ReviewRun（不进 fix-loop），兑现 Reviewer 方案 H 第 150 行；pass /
  error / None 不写；run_id 挂到 ``evidence_reviewer`` 上。
- ``_finalize_evidence_fields``（流式共用收尾）：warning + fix-loop 关 →
  reviewer 含 review_run_id 且磁盘有 run 文件。
- ``run_chat``（同步）：warning 答案的 notices 含 reviewer 提示，
  evidence_reviewer 含 review_run_id。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import app.services.chat_orchestration as chat_mod
from app.services import reviewer_fix_loop as rfl


@pytest.fixture()
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(rfl, "_data_root", lambda: tmp_path)
    return tmp_path


def _warning():
    return {"status": "warning", "notes": ["1 条断言支撑弱"], "findings": []}


def _failure():
    return {"status": "failure", "notes": ["1 条断言无据"], "findings": []}


def test_reviewer_notices_warning_and_failure():
    warning = chat_mod._reviewer_notices(_warning())
    assert len(warning) == 1
    assert warning[0]["code"] == "reviewer_warning"
    assert "谨慎采信" in warning[0]["message"]
    failure = chat_mod._reviewer_notices(_failure())
    assert len(failure) == 1
    assert failure[0]["code"] == "reviewer_failure"
    assert "未通过" in failure[0]["message"]
    assert "谨慎采信" in failure[0]["message"]


def test_reviewer_notices_pass_error_none_unchanged():
    assert chat_mod._reviewer_notices({"status": "pass"}) == []
    assert chat_mod._reviewer_notices({"status": "error", "reviewer_error": "boom"}) == [
        {
            "code": "reviewer_error",
            "message": "证据评审未完成（reviewer 异常），回答未经第二轮校验",
        }
    ]
    assert chat_mod._reviewer_notices(None) == [
        {
            "code": "reviewer_skipped",
            "message": "证据评审未运行（已跳过），回答未经第二轮校验",
        }
    ]


def test_write_shadow_run_warning_persists_flagged(tmp_data):
    run = rfl.write_shadow_review_run(
        question="q?", answer="a", review=_warning(), project_id="p1"
    )
    assert run is not None
    assert run["outcome"] == "flagged"
    assert run["status"] == "complete"
    saved = rfl.load_review_run(run["run_id"])
    assert saved is not None
    assert saved["outcome"] == "flagged"
    assert saved["status"] == "complete"


def test_write_shadow_run_failure_persists_flagged(tmp_data):
    run = rfl.write_shadow_review_run(
        question="q?", answer="a", review=_failure(), project_id="p1"
    )
    assert run is not None
    assert run["outcome"] == "flagged"


def test_write_shadow_run_pass_error_none_write_nothing(tmp_data):
    assert rfl.write_shadow_review_run(question="q", answer="a", review={"status": "pass"}) is None
    assert rfl.write_shadow_review_run(
        question="q", answer="a", review={"status": "error", "reviewer_error": "boom"}
    ) is None
    assert rfl.write_shadow_review_run(question="q", answer="a", review=None) is None
    assert list((tmp_data / "reviews" / "runs").glob("*.json")) == []


def test_finalize_writes_shadow_run_when_fix_loop_off(tmp_data, monkeypatch):
    import app.services.evidence_reviewer as er
    import app.services.evidence_synthesis as es

    monkeypatch.setattr(es, "evidence_mode_active", lambda mode, settings: True)
    monkeypatch.setattr(
        es,
        "postprocess_evidence_answer",
        lambda answer, settings=None: (answer, {}),
    )
    monkeypatch.setattr(er, "review_answer", lambda *a, **k: _warning())

    settings = SimpleNamespace(evidence_reviewer_fix_loop_enabled=False)
    answer, _doi, reviewer, reviewer_fix, _expand, _cits, _binding = (
        chat_mod._finalize_evidence_fields(
            "q?",
            "原始答案",
            [],
            settings=settings,
            mode="evidence",
            selected_skills=[],
            project_id="p1",
        )
    )
    assert answer == "原始答案"
    assert reviewer_fix is None  # fix-loop 关，不进 fix-loop
    assert reviewer is not None
    assert reviewer["status"] == "warning"
    assert reviewer.get("review_run_id"), "warning-only 也应写影子 run 并挂 run_id"
    saved = rfl.load_review_run(reviewer["review_run_id"])
    assert saved is not None
    assert saved["outcome"] == "flagged"


def test_run_chat_warning_notices_and_shadow_run(tmp_data, monkeypatch):
    """同步 run_chat：warning 进 notices，影子 run 落盘且 run_id 挂回响应。"""
    import app.services.evidence_reviewer as er
    import app.services.evidence_synthesis as es
    from app.domain.chat_schemas import ChatRequest
    from app.domain.schemas import Evidence

    ev = Evidence(
        source="kb",
        identifier="kb:s1#c0",
        title="t",
        snippet="盐雾 720 h。",
        relevance=0.9,
    )
    monkeypatch.setattr(
        chat_mod, "_augment_with_kb", lambda q, s, **kw: (list(s), 1, None, None)
    )
    monkeypatch.setattr(
        chat_mod, "answer_question", lambda *a, **k: ("盐雾 720 h[^1]。", [ev])
    )
    monkeypatch.setattr(
        es,
        "postprocess_evidence_answer",
        lambda answer, settings=None: (answer, {}),
    )
    monkeypatch.setattr(er, "review_answer", lambda *a, **k: _warning())
    monkeypatch.setattr(
        chat_mod, "_claims_and_audit", lambda *a, **k: ([], None, [])
    )

    req = ChatRequest(question="盐雾多久？", mode="evidence", sources=[ev])
    resp = chat_mod.run_chat(req, None)
    assert resp.evidence_reviewer is not None
    assert resp.evidence_reviewer["status"] == "warning"
    assert resp.reviewer_fix is None  # fix-loop 默认关，不进 fix-loop
    run_id = resp.evidence_reviewer.get("review_run_id")
    assert run_id, "warning-only 也应写影子 run 并挂 run_id"
    assert rfl.load_review_run(run_id)["outcome"] == "flagged"
    assert resp.notices is not None
    assert any(n.get("code") == "reviewer_warning" for n in resp.notices)
