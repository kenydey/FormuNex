"""WeKnora 第一批 2.3：证据合并（邻块扩展 + 再去重）测试。"""

from __future__ import annotations

from app.domain.schemas.chat import Evidence
from app.services.evidence_merge import merge_evidence


def _ev(ident, snippet, relevance=0.5, source="kb"):
    return Evidence(source=source, identifier=ident, title=f"T-{ident}",
                    snippet=snippet, relevance=relevance)


def _kb(src, ord_no, snippet, relevance=0.5):
    return _ev(f"kb:{src}#c{ord_no}", snippet, relevance)


def test_id_and_signature_dedup():
    a = _kb("s1", 0, "环氧树脂防腐底漆磷酸锌")
    b = _kb("s1", 0, "环氧树脂防腐底漆磷酸锌")
    out = merge_evidence([a, b])
    assert [e.identifier for e in out] == ["kb:s1#c0"]


def test_consecutive_ord_merge_with_overlap_elimination():
    # 两段共享 200 字以内的衔接重叠（此处用短重叠模拟），合并后重叠只留一次。
    a = _kb("s1", 0, "第一段正文内容结束于关键词")
    b = _kb("s1", 1, "关键词第二段正文内容")
    out = merge_evidence([a, b])
    assert len(out) == 1
    assert out[0].snippet.count("关键词") == 1
    assert out[0].merged_from == ["kb:s1#c1"]
    assert out[0].relevance == 0.5


def test_non_consecutive_and_cross_source_not_merged():
    a = _kb("s1", 0, "第一段甲")
    b = _kb("s1", 2, "第三段丙")
    c = _kb("s2", 0, "第一段甲")
    out = merge_evidence([a, b, c])
    assert len(out) == 3


def test_max_chars_guard_keeps_separate():
    a = _kb("s1", 0, "甲" * 100)
    b = _kb("s1", 1, "乙" * 100)
    out = merge_evidence([a, b], max_chars=150)
    assert len(out) == 2


def test_neighbour_expansion_short_chunk():
    short = _kb("s1", 1, "短句")
    lookup = {"s1": {0: ("前邻块文本", "kb:s1#c0"), 2: ("后邻块文本", "kb:s1#c2")}}
    out = merge_evidence(
        [short],
        neighbour_lookup=lambda src, o: lookup.get(src, {}).get(o),
        short_chars=400,
    )
    assert len(out) == 1
    assert "前邻块文本" in out[0].snippet and "后邻块文本" in out[0].snippet
    assert out[0].identifier == "kb:s1#c1"
    assert set(out[0].merged_from) == {"kb:s1#c0", "kb:s1#c2"}


def test_neighbour_expansion_keeps_unrelated_listed_item():
    # 邻块不在列表中时扩展；列表中不相邻的另一项不受影响、不被消耗。
    short = _kb("s1", 1, "短句")
    other = _kb("s1", 9, "另一段较长正文内容" * 20)
    lookup = {"s1": {0: ("前邻块文本", "kb:s1#c0"), 2: ("后邻块文本", "kb:s1#c2")}}
    out = merge_evidence(
        [short, other],
        neighbour_lookup=lambda src, o: lookup.get(src, {}).get(o),
        short_chars=400,
    )
    assert [e.identifier for e in out] == ["kb:s1#c1", "kb:s1#c9"]
    assert "前邻块文本" in out[0].snippet


def test_neighbour_already_merged_is_not_readded():
    # 连续项先合并，扩展时已在 merged_from 中的邻块不再重复并入。
    a = _kb("s1", 0, "前邻块文本内容")
    short = _kb("s1", 1, "短句")
    lookup = {"s1": {2: ("后邻块文本", "kb:s1#c2")}}
    out = merge_evidence(
        [a, short],
        neighbour_lookup=lambda src, o: lookup.get(src, {}).get(o),
        short_chars=400,
    )
    assert len(out) == 1
    assert out[0].snippet.count("前邻块文本内容") == 1
    assert "后邻块文本" in out[0].snippet


def test_neighbour_lookup_failure_skips_side():
    short = _kb("s1", 1, "短句")

    def _boom(src, o):
        raise RuntimeError("store down")

    out = merge_evidence([short], neighbour_lookup=_boom, short_chars=400)
    assert [e.identifier for e in out] == ["kb:s1#c1"]
    assert out[0].snippet == "短句"


def test_non_kb_evidence_skips_expansion_but_dedups():
    a = _ev("CN123", "专利文本甲", source="USPTO")
    b = _ev("CN123", "专利文本甲", source="USPTO")
    out = merge_evidence([a, b], neighbour_lookup=lambda s, o: ("邻块", "kb:x#c0"))
    assert len(out) == 1
    assert "邻块" not in out[0].snippet


def test_final_dedup_substring_and_jaccard():
    long_ev = _kb("s1", 0, "环氧树脂 防腐 底漆 磷酸锌 盐雾")
    short_ev = _kb("s1", 5, "环氧树脂 防腐")
    out = merge_evidence([long_ev, short_ev])
    assert [e.identifier for e in out] == ["kb:s1#c0"]
    # Jaccard ≥ 0.85：低 relevance 者被丢（23 个共享 token / 27 个并集 ≈ 0.852）。
    shared = " ".join(f"w{i}" for i in range(23))
    a = _kb("s1", 0, f"{shared} aa ab", relevance=0.9)
    b = _kb("s1", 3, f"{shared} ba bb", relevance=0.5)
    out = merge_evidence([a, b])
    assert [e.identifier for e in out] == ["kb:s1#c0"]


def test_originals_untouched_and_switch_off_by_caller():
    a = _kb("s1", 0, "第一段正文内容结束于关键词")
    b = _kb("s1", 1, "关键词第二段正文内容")
    merge_evidence([a, b])
    assert a.snippet == "第一段正文内容结束于关键词"
    assert a.merged_from == []
