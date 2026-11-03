"""2.1 tests: 引用句柄注册表 + 渲染 fail-closed。

WeKnora modelcontext 范式在 FormuNex 的落地：请求级句柄注册、
未知句柄渲染时删空、已验证引用结构化载荷。

注：流式跨段缓冲不在此文件——实施审计结论：chat 流式路径不经过
bind_citations（绑定是整段后处理），半截标记问题不存在，故跳过。
"""

from __future__ import annotations

from app.domain.citations import CitationAnchor, CitationLocator
from app.services.citation_binder import (
    CitationHandleRegistry,
    bind_citations,
    format_answer_with_citations,
)


def _make_anchors() -> list[CitationAnchor]:
    return [
        CitationAnchor(
            chunk_id="chunk-a",
            source_id="patent-EPO-001",
            text="环氧树脂交联 2 小时，盐雾耐受≥720h。",
            page=3,
            paragraph=42,
            offset_start=1024,
            offset_end=2048,
        ),
        CitationAnchor(
            chunk_id="chunk-b",
            source_id="literature-arXiv-456",
            text="磷酸锌 3–5 wt% 时附着力提升 35%。",
            page=7,
            paragraph=15,
            offset_start=512,
            offset_end=1024,
            locator=CitationLocator(page=7, figure="2"),
        ),
    ]


def test_out_of_range_markers_stripped_and_counted():
    """超范围标记被清除并计数，范围内标记保留。"""
    binding = bind_citations("结论 A[^1]，结论 B[^99]，结论 C[^2]。", _make_anchors())
    assert "[^99]" not in binding["answer"]
    assert "[^1]" in binding["answer"]
    assert "[^2]" in binding["answer"]
    assert binding["stats"] == {"total_anchors": 2, "used": 2, "dropped_out_of_range": 1}
    assert [p[0] for p in binding["used_citations"]] == [1, 2]
    assert "[^99]:" not in binding["footnotes"]


def test_zero_handle_stripped():
    """[^0] 不是合法句柄，同样 fail-closed 清除。"""
    binding = bind_citations("结论[^0]。", _make_anchors())
    assert "[^0]" not in binding["answer"]
    assert binding["stats"]["dropped_out_of_range"] == 1
    assert binding["used_citations"] == []


def test_references_payload_matches_anchors():
    """references 载荷与 anchor 字段一致；无 locator 时省略该键。"""
    binding = bind_citations("A[^1] B[^2]", _make_anchors())
    assert binding["references"] == [
        {
            "index": 1,
            "chunk_id": "chunk-a",
            "source_id": "patent-EPO-001",
            "page": 3,
            "paragraph": 42,
            "offset_start": 1024,
            "offset_end": 2048,
        },
        {
            "index": 2,
            "chunk_id": "chunk-b",
            "source_id": "literature-arXiv-456",
            "page": 7,
            "paragraph": 15,
            "offset_start": 512,
            "offset_end": 1024,
            "locator": {"page": 7, "figure": "2"},
        },
    ]


def test_footnote_definition_line_does_not_count_as_citation():
    """模型自写脚注定义行先被清除，其编号不产生脚注。"""
    answer = "结论[^1]。\n[^2]: Source: 模型编造的脚注\n"
    binding = bind_citations(answer, _make_anchors())
    assert "[^2]:" not in binding["footnotes"]
    assert [p[0] for p in binding["used_citations"]] == [1]


def test_strict_false_keeps_legacy_behavior():
    """strict=False 时保留旧行为：超范围标记留原地、计数为 0。"""
    binding = bind_citations("结论[^99]。", _make_anchors(), strict=False)
    assert "[^99]" in binding["answer"]
    assert binding["stats"]["dropped_out_of_range"] == 0
    assert binding["used_citations"] == []
    assert binding["footnotes"] == ""


def test_format_answer_passthrough():
    """format_answer_with_citations 默认 strict，显式可关。"""
    combined = format_answer_with_citations("结论[^99]。", _make_anchors())
    assert "[^99]" not in combined
    legacy = format_answer_with_citations("结论[^99]。", _make_anchors(), strict=False)
    assert "[^99]" in legacy


def test_registry_navigation_handle_reserved():
    """导航句柄可解析但不可作证据、不计入 used。"""
    registry = CitationHandleRegistry(_make_anchors())
    assert registry.resolve(1) is not None
    assert registry.is_evidence(1) is True
    assert registry.mark_navigation(1) is True
    assert registry.is_evidence(1) is False
    assert registry.resolve(1) is not None  # 仍可解析跳转
    assert registry.mark_navigation(99) is False
    assert registry.resolve(99) is None
    assert len(registry) == 2


def test_registries_are_request_scoped():
    """两个注册表互不影响（一次问答一实例）。"""
    first = CitationHandleRegistry(_make_anchors())
    second = CitationHandleRegistry([])
    assert first.resolve(1) is not None
    assert second.resolve(1) is None
    first.mark_used(1)
    assert 1 in first.used
    assert second.used == set()
