"""P3: chat_claims 共享 executor 测试(2026-09-04)。"""
from __future__ import annotations

import time

from app.domain.schemas import Evidence
from app.services import chat_claims as cc


def test_claim_executor_is_singleton():
    a = cc._claim_executor()
    b = cc._claim_executor()
    assert a is b
    assert a._max_workers == 2


def test_submit_quick_success(monkeypatch):
    """正常路径: LLM 验证快速返回 → 结果透传(不触发 offline)。"""
    def fake_verify(question, claims, sources):
        return "ok"

    monkeypatch.setattr(cc, "verify_claims_llm", fake_verify)
    ex = cc._claim_executor()
    fut = ex.submit(fake_verify, "q", [], [])
    assert fut.result(timeout=5) == "ok"


def test_slow_claim_does_not_spawn_threads_per_call(monkeypatch):
    """慢验证(>12s 超时)连续触发时线程有界(共享池 max_workers=2),
    不再每问新建孤儿线程。不实际等 12s —— 验证池为单例且线程名前缀固定。"""
    import threading

    ex = cc._claim_executor()
    assert ex is cc._claim_executor()
    assert ex._max_workers == 2
    names = {t.name for t in threading.enumerate()}
    assert any(n.startswith("claim-verify") for n in names)


def test_exception_falls_back_offline(monkeypatch):
    """verify_claims_llm 抛异常 → offline 验证兜底(共享池下行为不变)。"""
    calls = {"offline": 0}

    def fake_verify(question, claims, sources):
        raise RuntimeError("llm down")

    def fake_offline(c, sources):
        calls["offline"] += 1
        verdict = type("V", (), {"verdict": "supported", "text": c,
                                 "evidence_indices": [0]})()
        return verdict

    monkeypatch.setattr(cc, "verify_claims_llm", fake_verify)
    monkeypatch.setattr(cc, "verify_claim_offline", fake_offline)
    monkeypatch.setattr(cc, "get_settings",
                        lambda: type("S", (), {"chat_claim_check_enabled": True})())

    # QA-12：离线兜底 verdict 引用 evidence index 0，须传入对应的证据池；
    # sources=[] 时 chunk_ids 为空，supported 会按逃逸 A 正确降级为 weak。
    ev = Evidence(source="kb", identifier="kb:s1#c0", title="t",
                  snippet="证据。", relevance=0.9)
    out = cc.build_sourced_claims("问题?", "这是第一句。这是第二句。", [ev])
    assert calls["offline"] == 2
    assert len(out) == 2
    assert all(c.status == "supported" for c in out)


def test_conflicting_verdict_structured_passthrough():
    """A-6: conflicting 判词结构化透出 — status 保持 weak（back-compat），
    raw_verdict 携带 "conflicting" 供前端渲染独立冲突态。"""
    from app.domain.schemas import Evidence
    from app.pipeline.claim_checker import ClaimVerdict, VerifiedClaim

    verified = [
        VerifiedClaim(
            text="某成分耐盐雾 1000 小时",
            verdict=ClaimVerdict.conflicting,
            evidence_indices=[],
        ),
        VerifiedClaim(
            text="水性体系",
            verdict=ClaimVerdict.supported,
            # QA-12：supported 须有证据下标才保持 supported；evidence_indices
            # 为空 + sources 为空时按逃逸 A 降级为 weak（见新单测）。
            evidence_indices=[0],
        ),
    ]
    ev = Evidence(source="kb", identifier="kb:s1#c0", title="t",
                  snippet="证据。", relevance=0.9)
    out = cc.build_sourced_claims("问题?", "答案。", [ev], verified=verified)
    assert out is not None and len(out) == 2
    conflict, ok = out
    assert conflict.status == "weak"  # back-compat: 不破坏旧前端
    assert conflict.raw_verdict == "conflicting"
    assert ok.status == "supported"
    assert ok.raw_verdict == "supported"


# ── 批 C（Reviewer 切分修复）：非命题片段过滤与计数对齐 ──


def test_trailing_marker_is_not_a_claim():
    """批 A：答案尾 "…表现。[^1]" 不产出 [^1] claim，实句保留。"""
    out = cc._extract_claims("环氧涂层耐盐雾 1000 小时。[^1]", None)
    assert out == ["环氧涂层耐盐雾 1000 小时。"]


def test_clarification_boilerplate_is_not_a_claim():
    """批 A：澄清提示整段（含 [^1] 前缀与尾部 ））→ 零 claim。"""
    answer = "表现优异。[^1]\n\n（默认按「双酚A型环氧树脂」理解；如需其他含义请说明。）"
    assert cc._extract_claims(answer, None) == ["表现优异。"]


def test_isolated_bracket_and_punctuation_are_not_claims():
    """批 A：孤立 ）、空片段、纯标点片段 → 零 claim。"""
    assert cc._extract_claims("句一。[^1][^2]", None) == ["句一。"]
    assert cc._extract_claims("句一。", None) == ["句一。"]
    assert cc._extract_claims("[^1]", None) == []
    assert cc._extract_claims("。", None) == []
    assert cc._extract_claims("（默认按「X」理解；如需其他含义请说明。）", None) == []


def test_mid_sentence_marker_survives():
    """批 A 回归保护：句中标记 "pH 4.0[^1]。" 仍为 1 条 claim。"""
    assert cc._extract_claims("pH 4.0[^1]。", None) == ["pH 4.0[^1]。"]


def test_claim_sentence_count_matches_filtered_claims():
    """批 B：计数与过滤后 claim 数同源。"""
    assert cc._claim_sentence_count("第一句。第二句。[^1]", None) == 2
    assert cc._claim_sentence_count("[^1]", None) == 0


def test_unverified_count_excludes_artifacts(monkeypatch):
    """批 B：过滤后 _total 与 len(verified) 一致，n_unverified 不虚增。
    修复前：_total 按原始分句计为 3（含孤立 [^1]），n_unverified 虚增为 1。"""
    from app.services import chat_orchestration as co

    def fake_llm(question, claims, sources):
        raise RuntimeError("llm down")

    monkeypatch.setattr(cc, "verify_claims_llm", fake_llm)
    settings = type(
        "S",
        (),
        {
            "chat_claim_check_enabled": True,
            "sources_audit_enabled": True,
            "wiki_claim_confidence_cap": 1.0,
        },
    )()
    _sc, audit, verified = co._claims_and_audit(
        "问题?", "第一句。第二句。[^1]", [], settings=settings
    )
    assert verified is not None and len(verified) == 2
    assert audit is not None
    assert audit["summary"]["unverified"] == 0
    assert len(audit["rows"]) == 2
