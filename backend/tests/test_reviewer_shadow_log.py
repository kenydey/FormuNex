"""批 G 浸泡采集（结构化日志行）+ 批 H warning-only 默认开。

批 G：每次 reviewer 结果非 pass 时记一条 `reviewer_shadow` 日志行，
只含 status / weak / unsupported / difficulty / source / domain，
不含问题原文、答案原文、证据文本。
批 H：evidence_reviewer_enabled 默认开；fix-loop / LLM 仍关；
warning / failure 状态下答案不变（回答永不拦截）。
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import app.services.chat_orchestration as chat_mod
from app.config import Settings
from app.services import evidence_reviewer as er
from app.services import reviewer_fix_loop as rfl


@pytest.fixture(autouse=True)
def _info_level(caplog):
    caplog.set_level(logging.INFO, logger="app.services.evidence_reviewer")


def _shadow_records(caplog):
    return [
        r
        for r in caplog.records
        if r.name == "app.services.evidence_reviewer"
        and er.REVIEWER_SHADOW_LOG_MARKER in r.getMessage()
    ]


# ---------------------------------------------------------------- 批 G：helper


def test_shadow_log_warning_one_line(caplog):
    review = {
        "status": "warning",
        "weak_count": 2,
        "unsupported_count": 1,
        "difficulty": 0.3,
    }
    er.log_reviewer_shadow(review, source="chat_sync", domain="chemistry")
    recs = _shadow_records(caplog)
    assert len(recs) == 1
    msg = recs[0].getMessage()
    assert msg.startswith("reviewer_shadow")
    assert "status=warning" in msg
    assert "weak=2" in msg
    assert "unsupported=1" in msg
    assert "difficulty=0.30" in msg
    assert "source=chat_sync" in msg
    assert "domain=chemistry" in msg


def test_shadow_log_pass_and_none_silent(caplog):
    er.log_reviewer_shadow(
        {"status": "pass", "weak_count": 0, "unsupported_count": 0},
        source="chat_sync",
    )
    er.log_reviewer_shadow(None, source="chat_sync")
    er.log_reviewer_shadow(
        {"status": "", "weak_count": 0, "unsupported_count": 0},
        source="chat_sync",
    )
    assert _shadow_records(caplog) == []


def test_shadow_log_error_status_logged_with_zero_counts(caplog):
    er.log_reviewer_shadow(
        {"status": "error", "reviewer_error": "boom", "notes": [], "findings": []},
        source="chat_stream",
    )
    recs = _shadow_records(caplog)
    assert len(recs) == 1
    msg = recs[0].getMessage()
    assert "status=error" in msg
    assert "weak=0" in msg
    assert "unsupported=0" in msg
    assert "difficulty=n/a" in msg


def test_shadow_log_domain_sanitized_and_unknown(caplog):
    er.log_reviewer_shadow(
        {"status": "warning", "weak_count": 1, "unsupported_count": 0},
        source="manual",
        domain="a b\nc",
    )
    assert "domain=a_b_c" in _shadow_records(caplog)[0].getMessage()
    caplog.clear()
    er.log_reviewer_shadow(
        {"status": "warning", "weak_count": 1, "unsupported_count": 0},
        source="manual",
        domain=None,
    )
    assert "domain=unknown" in _shadow_records(caplog)[0].getMessage()


def test_shadow_log_contains_no_source_text(caplog):
    """真实 review 路径：日志行不得出现问题/答案原文。"""
    settings = SimpleNamespace(
        evidence_reviewer_enabled=True, chat_claim_check_enabled=True
    )
    question = "UNIQUE_Q_7f3a91"
    answer = "UNIQUE_A_7f3a91 的第一句。这是第二句。"
    review = er.review_answer(question, answer, [], settings=settings)
    assert review is not None and review["status"] != "pass"
    er.log_reviewer_shadow(review, source="chat_sync", domain="chemistry")
    recs = _shadow_records(caplog)
    assert len(recs) == 1
    assert "UNIQUE_Q_7f3a91" not in recs[0].getMessage()
    assert "UNIQUE_A_7f3a91" not in recs[0].getMessage()


# ------------------------------------------------------- 批 G：调用点接线


def test_run_evidence_review_logs_non_pass_once(monkeypatch, caplog):
    monkeypatch.setattr(
        er,
        "review_answer",
        lambda q, a, e, *, settings: {
            "status": "warning",
            "weak_count": 1,
            "unsupported_count": 0,
            "difficulty": 0.2,
        },
    )
    out = chat_mod._run_evidence_review(
        "q", "a", [], SimpleNamespace(), source="chat_stream", domain="chem"
    )
    assert out["status"] == "warning"
    recs = _shadow_records(caplog)
    assert len(recs) == 1
    assert "source=chat_stream" in recs[0].getMessage()
    assert "domain=chem" in recs[0].getMessage()


def test_run_evidence_review_pass_logs_nothing(monkeypatch, caplog):
    monkeypatch.setattr(
        er,
        "review_answer",
        lambda q, a, e, *, settings: {"status": "pass"},
    )
    out = chat_mod._run_evidence_review("q", "a", [], SimpleNamespace())
    assert out["status"] == "pass"
    assert _shadow_records(caplog) == []


def test_fix_loop_internal_rereview_does_not_log(tmp_path, monkeypatch, caplog):
    """fix-loop 内部重审不调 log 钩子：同一问答最多记一行。"""
    monkeypatch.setattr(rfl, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(rfl, "_AUTO_STATE", {})
    settings = SimpleNamespace(evidence_reviewer_fix_loop_enabled=True)
    monkeypatch.setattr(
        er,
        "review_answer",
        lambda q, a, c, *, settings: {
            "status": "failure",
            "weak_count": 0,
            "unsupported_count": 1,
            "difficulty": 0.6,
        },
    )
    final, fix = rfl.run_fix_loop(
        question="q",
        answer="old answer text",
        citations=[],
        review={
            "status": "failure",
            "weak_count": 0,
            "unsupported_count": 1,
            "difficulty": 0.6,
        },
        settings=settings,
        repair_fn=lambda q, hint: "repaired answer text",
        max_rounds=1,
    )
    assert final == "repaired answer text"
    assert _shadow_records(caplog) == []


def test_maybe_auto_review_logs_non_pass(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(rfl, "_data_root", lambda: tmp_path)
    monkeypatch.setattr(rfl, "_AUTO_STATE", {})
    settings = SimpleNamespace(
        auto_audit_enabled=True, evidence_reviewer_enabled=True
    )
    monkeypatch.setattr(
        er,
        "review_answer",
        lambda q, a, c, *, settings: {
            "status": "warning",
            "weak_count": 1,
            "unsupported_count": 0,
            "difficulty": 0.2,
        },
    )
    out = rfl.maybe_auto_review(
        "t1", question="q", answer="a", citations=[], settings=settings
    )
    assert out is not None and out["review"]["status"] == "warning"
    recs = _shadow_records(caplog)
    assert len(recs) == 1
    assert "source=auto_audit" in recs[0].getMessage()


# ---------------------------------------------------------------- 批 H


def test_reviewer_enabled_by_default_fix_loop_and_llm_still_off(monkeypatch):
    """批 H 已批准的行为变更：reviewer 默认开；fix-loop / LLM 仍关；
    证据合成仍 off。"""
    monkeypatch.delenv("FORMUNEX_EVIDENCE_REVIEWER_ENABLED", raising=False)
    monkeypatch.delenv(
        "FORMUNEX_EVIDENCE_REVIEWER_FIX_LOOP_ENABLED", raising=False
    )
    monkeypatch.delenv("FORMUNEX_EVIDENCE_REVIEWER_LLM_ENABLED", raising=False)
    monkeypatch.delenv("FORMUNEX_EVIDENCE_SYNTHESIS_MODE", raising=False)
    settings = Settings()
    assert settings.evidence_reviewer_enabled is True
    assert settings.evidence_reviewer_fix_loop_enabled is False
    assert settings.evidence_reviewer_llm_enabled is False
    assert settings.evidence_synthesis_mode == "off"


@pytest.mark.parametrize("status", ["warning", "failure"])
def test_finalize_warning_and_failure_do_not_change_answer(
    monkeypatch, caplog, status
):
    """流式路径：warning / failure 状态下答案原样返回，永不拦截。"""
    import app.services.evidence_synthesis as es

    monkeypatch.setattr(es, "evidence_mode_active", lambda mode, settings: True)
    monkeypatch.setattr(
        es,
        "postprocess_evidence_answer",
        lambda answer, settings=None: (answer, {}),
    )
    monkeypatch.setattr(
        er,
        "review_answer",
        lambda q, a, e, *, settings, _s=status: {
            "status": _s,
            "weak_count": 1,
            "unsupported_count": 0,
            "difficulty": 0.2,
        },
    )
    (
        answer,
        _doi,
        reviewer,
        reviewer_fix,
        _ce,
        citations,
        _cb,
    ) = chat_mod._finalize_evidence_fields(
        "q?",
        "原始答案",
        [],
        settings=SimpleNamespace(),
        mode="evidence",
        selected_skills=[],
        project_id="p1",
    )
    assert answer == "原始答案"
    assert reviewer["status"] == status
    assert reviewer_fix is None
    assert citations == []
    # 批 G：流式终态的非 pass 结果记一行影子日志。
    assert len(_shadow_records(caplog)) == 1
