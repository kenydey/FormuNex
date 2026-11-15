"""1-2：证据合成 opt-in 路径验证（RAG 升级第 1 批）。

- ``evidence_mode_active``：req.mode 显式指定（evidence/paperqa/
  hybrid_strict）即开；否则看全局 ``evidence_synthesis_mode``，
  默认 "off" 时关。
- 默认保持 "off"：``get_settings().evidence_synthesis_mode == "off"``
  （本批只给建议与数字，不改默认）。
- ``try_paperqa_answer``：本沙箱无 paperqa 包 → fail-open 返回 None，
  调用方回退普通问答（见 test_citation_binder_chat_wiring.py 的
  mode=evidence 同步测试：mock 的 answer_question 被实际调用）。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.evidence_synthesis import evidence_mode_active, try_paperqa_answer
from app.services.paperqa_engine import paperqa_available


def _s(mode="off"):
    return SimpleNamespace(evidence_synthesis_mode=mode)


def test_mode_explicit_opt_in_wins_over_global_off():
    for mode in ("evidence", "paperqa", "hybrid_strict", " Evidence ", "PAPERQA"):
        assert evidence_mode_active(mode, _s("off")) is True, mode


def test_mode_chat_does_not_opt_in():
    assert evidence_mode_active("chat", _s("off")) is False
    assert evidence_mode_active(None, _s("off")) is False
    assert evidence_mode_active("", _s("off")) is False


def test_global_mode_on_activates_without_request_mode():
    assert evidence_mode_active(None, _s("paperqa")) is True
    assert evidence_mode_active("chat", _s("hybrid_strict")) is True


def test_global_mode_off_variants_stay_off():
    for flag in ("off", "false", "0", "", "OFF"):
        assert evidence_mode_active(None, _s(flag)) is False, flag


def test_default_evidence_synthesis_mode_is_off():
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        assert get_settings().evidence_synthesis_mode == "off"
    finally:
        get_settings.cache_clear()


def test_paperqa_unavailable_in_this_sandbox():
    """沙箱 .venv 无 paperqa 包 → paperqa_available() 为 False。

    这是环境事实断言（与 CI 的 BM25-only 一致）：若将来环境装了
    paperqa 且配了 LLM key，本测试需随环境更新。
    """
    assert paperqa_available() is False


def test_try_paperqa_answer_fails_open_without_paperqa():
    from app.domain.schemas import Evidence

    sources = [
        Evidence(
            source="literature",
            identifier="kb:src1#c0",
            title="t",
            snippet="s",
            relevance=0.9,
        )
    ]
    assert try_paperqa_answer("q?", sources) is None
    assert try_paperqa_answer("q?", []) is None
