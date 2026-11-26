"""QA-05（2026-11-26 审查第一波）：PaperQA 数值门同步 / 流式统一（二选一之②）。

决策：paperqa 引用格式无法映射为 [^n]，数值检查会误报（D P2-4），故
同步与流式两路统一跳过，并在 notices 里写明"PaperQA 路径数值检查已
跳过"。不选方案①——真实 PaperQA 输出格式未经有 key 环境核实，无法
确认其引用可映射为 [^n]。

- ``is_paperqa=True``：数值不符也不追加数值提示，notices 含
  numeric_check_skipped（PaperQA 明示）。
- ``is_paperqa=False``：数值不符仍追加数值提示（原行为不变）。
"""
from __future__ import annotations

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services.chat_orchestration import _apply_answer_gates


def _ev():
    return Evidence(
        source="kb",
        identifier="kb:s1#c0",
        title="盐雾报告",
        snippet="中性盐雾试验达到 500 h。",
        relevance=0.9,
    )


def _codes(notices):
    return [n["code"] for n in notices]


def test_paperqa_path_skips_numeric_check_with_notice(monkeypatch):
    import app.services.numeric_check as nc

    monkeypatch.setattr(
        nc,
        "check_answer_numbers",
        lambda answer, texts: [{"claim": "盐雾 720 h", "reason": "证据为 500 h"}],
    )
    gated, _, abstained, notices = _apply_answer_gates(
        "q",
        "盐雾 720 h[^1]。",
        [_ev()],
        [],
        [],
        get_settings(),
        is_paperqa=True,
    )
    assert gated == "盐雾 720 h[^1]。", "paperqa 路径不得追加数值不符提示"
    assert abstained is False
    assert "numeric_check_skipped" in _codes(notices)
    msg = next(
        n["message"] for n in notices if n["code"] == "numeric_check_skipped"
    )
    assert "PaperQA" in msg
    assert "已跳过" in msg


def test_non_paperqa_path_still_runs_numeric_check(monkeypatch):
    import app.services.numeric_check as nc

    monkeypatch.setattr(
        nc,
        "check_answer_numbers",
        lambda answer, texts: [{"claim": "盐雾 720 h", "reason": "证据为 500 h"}],
    )
    gated, _, _, notices = _apply_answer_gates(
        "q",
        "盐雾 720 h[^1]。",
        [_ev()],
        [],
        [],
        get_settings(),
        is_paperqa=False,
    )
    assert "720" in gated and len(gated) > len("盐雾 720 h[^1]。")
    assert "numeric_check_skipped" not in _codes(notices)


def test_paperqa_skip_notice_only_when_check_enabled():
    s = get_settings()
    s.chat_numeric_check_enabled = False
    _, _, _, notices = _apply_answer_gates(
        "q", "盐雾 720 h[^1]。", [_ev()], [], [], s, is_paperqa=True
    )
    assert notices == []
