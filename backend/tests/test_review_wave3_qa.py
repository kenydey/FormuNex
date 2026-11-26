"""Review 2026-11-26 wave 3 — 问答域 Q3（QA-16 / QA-17 / QA-19 / QA-20）定向测试。

只覆盖本波四项的安全选项：QA-16 文档标注、QA-17 离线 verdict 标注
（阈值 0.25/0.12 不动）、QA-19 unverified 进 notices（门分母/采样不动）、
QA-20 纯代词追问澄清 + 化学白名单配置化。QA-18（kb_bilingual 去留）是
决策项，本文件不碰。
"""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.domain.chat_schemas import ChatTurn, SourcedClaim
from app.domain.schemas import Evidence
from app.pipeline.claim_checker import (
    _SUPPORTED_OVERLAP,
    _WEAK_OVERLAP,
    ClaimVerdict,
    VerifiedClaim,
    verify_claim_offline,
    verify_claims_llm,
)
from app.services.chat_claims import build_sources_audit, build_sourced_claims


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ev(snippet: str) -> Evidence:
    return Evidence(source="kb", identifier="kb:s1#c0", title="t", snippet=snippet, relevance=0.9)


# ── QA-16：命名误导 → 文档标注 + 调用方日志打印 meta["backend"] ────────


def test_qa16_rerank_docstring_states_cpu_gpu_backends():
    """QA-16：docstring 写明 CPU 下 BM25+FAISS、GPU 下 build_store() 回落 TF-IDF（不改名）。

    2026-11-27 协调员修正：初版写 "GPU 下为 ColBERT"，与代码不符——
    rag.build_store() 对 colbert/pylate 后端无分支，直接回落 TfidfStore()，
    本路径从不跑真 ColBERT（真 ColBERT 只在 colbert_store.search() 直接调用时走）。
    """
    from app.services.colbert_store import rerank_candidates
    from app.services.pageindex.two_stage import _colbert_rerank_stage1

    phrase_cpu = "CPU 下为 BM25+FAISS 重打分"
    phrase_gpu = "GPU 下"
    phrase_fallback = "回落 TF-IDF"
    for doc in (rerank_candidates.__doc__ or "", _colbert_rerank_stage1.__doc__ or ""):
        assert phrase_cpu in doc
        assert phrase_gpu in doc
        assert phrase_fallback in doc


def test_qa16_config_comment_states_cpu_gpu_backends():
    """QA-16：pageindex_colbert_rerank 开关注释写明实际后端含义。

    2026-11-27 协调员修正：初版断言 "GPU 下为 ColBERT"，与 build_store()
    的实际回落行为不符（见 test_qa16_rerank_docstring_states_cpu_gpu_backends
    的注释），改为断言回落 TF-IDF 表述。
    """
    text = (Path(__file__).parent.parent / "app" / "config.py").read_text(encoding="utf-8")
    line = next(l for l in text.splitlines() if "pageindex_colbert_rerank" in l)
    assert "CPU 下为 BM25+FAISS 重打分" in line
    assert "GPU 下 build_store() 回落 TF-IDF" in line


def test_qa16_rerank_meta_reports_backend():
    """QA-16：meta["backend"] 如实记录后端（CPU 默认 bm25_faiss）。"""
    from app.services.colbert_store import rerank_candidates

    _order, meta = rerank_candidates("磷酸锌", [])
    assert meta["backend"] == "bm25_faiss"


def test_qa16_stage1_logs_backend(monkeypatch, caplog):
    """QA-16：调用方（two_stage）日志打印 meta["backend"]。"""
    from app.services.pageindex import two_stage

    monkeypatch.setattr(
        two_stage, "get_settings",
        lambda: SimpleNamespace(pageindex_colbert_rerank=True),
    )
    monkeypatch.setattr(
        "app.services.colbert_store.rerank_candidates",
        lambda q, cands: (list(cands), {"applied": True, "backend": "bm25_faiss"}),
    )
    chunks = [
        SimpleNamespace(id="c1", text="磷酸锌 耐盐雾", relevance=0.9),
        SimpleNamespace(id="c2", text="环氧树脂 固化剂", relevance=0.8),
    ]
    with caplog.at_level(logging.INFO, logger="app.services.pageindex.two_stage"):
        ranked, applied = two_stage._colbert_rerank_stage1("磷酸锌", chunks)
    assert applied is True
    assert [c.id for c in ranked] == ["c1", "c2"]
    assert "backend=bm25_faiss" in caplog.text


# ── QA-17：离线 verdict 标注 verifier="offline-overlap" ──────────────────


def test_qa17_thresholds_untouched():
    """QA-17：阈值 0.25/0.12 不动，只做标注。"""
    assert _SUPPORTED_OVERLAP == 0.25
    assert _WEAK_OVERLAP == 0.12


def test_qa17_offline_verdicts_carry_verifier_on_all_paths():
    """QA-17：离线核验各返回路径（supported/insufficient/unsupported/
    空 claim/无证据）都带 verifier="offline-overlap"。"""
    rich = _ev("磷酸锌提高环氧涂层的耐盐雾性能，添加量十五个百分点。")
    assert verify_claim_offline("磷酸锌提高环氧涂层的耐盐雾性能", [rich]).verifier == "offline-overlap"
    assert verify_claim_offline("磷酸锌提高环氧涂层的耐盐雾性能", [_ev("无关内容")]).verifier == "offline-overlap"
    assert verify_claim_offline("某物质耐盐雾五千小时", [_ev("通用防腐涂层说明")]).verifier == "offline-overlap"
    assert verify_claim_offline("", [rich]).verifier == "offline-overlap"
    assert verify_claim_offline("磷酸锌提高耐盐雾性能", []).verifier == "offline-overlap"


def test_qa17_llm_verdicts_carry_verifier_llm(monkeypatch):
    """QA-17：LLM verdict 标注 verifier="llm"，与离线区分。"""
    monkeypatch.setattr(
        "app.pipeline.claim_checker.llm.complete_json",
        lambda *a, **k: {
            "claims": [
                {"index": 0, "verdict": "supported", "evidence_indices": [0], "reason": "ok"}
            ]
        },
    )
    out = verify_claims_llm("主题", ["磷酸锌提高耐盐雾性能"], [_ev("磷酸锌提高耐盐雾性能")])
    assert len(out) == 1
    assert out[0].verdict == ClaimVerdict.supported
    assert out[0].verifier == "llm"


def test_qa17_sources_audit_rows_carry_verifier():
    """QA-17：sources_audit 每行带 verifier；summary 口径不变。"""
    verified = [verify_claim_offline("磷酸锌提高耐盐雾性能", [_ev("磷酸锌提高耐盐雾性能")])]
    audit = build_sources_audit([_ev("磷酸锌提高耐盐雾性能")], verified=verified, enabled=True)
    assert audit is not None
    assert audit["rows"][0]["verifier"] == "offline-overlap"
    assert audit["summary"]["supported"] == 1
    assert audit["summary"]["unverified"] == 0


def test_qa17_sourced_claims_carry_verifier_when_field_landed():
    """QA-17：SourcedClaim 带 verifier（字段本身待协调员 patch 落地；
    落地前 pydantic 静默忽略，落地后透传——未落地时跳过不断言）。"""
    verified = [verify_claim_offline("磷酸锌提高耐盐雾性能", [_ev("磷酸锌提高耐盐雾性能")])]
    out = build_sourced_claims(
        "问题?", "磷酸锌提高耐盐雾性能。", [_ev("磷酸锌提高耐盐雾性能")], verified=verified
    )
    assert out is not None and len(out) == 1
    if "verifier" not in SourcedClaim.model_fields:
        pytest.skip("SourcedClaim.verifier 待协调员 patch 落地（domain/chat_schemas.py）")
    assert out[0].verifier == "offline-overlap"


# ── QA-19：unverified 尾部进 notices（门分母/采样不动）──────────────────


def _long_answer(n: int) -> str:
    return "".join(f"第{i}条论断描述磷酸锌在环氧涂层中的作用。" for i in range(1, n + 1))


def _verified(n: int, unsupported: int = 0) -> list[VerifiedClaim]:
    out = []
    for i in range(n):
        verdict = ClaimVerdict.unsupported if i < unsupported else ClaimVerdict.supported
        out.append(VerifiedClaim(text=f"claim {i}", verdict=verdict, evidence_indices=[0]))
    return out


def test_qa19_unverified_tail_in_notices():
    """QA-19：25 句答案、20 句已验证 → notices 有 claims_unverified（5 句）。"""
    from app.services.chat_orchestration import _apply_answer_gates

    answer = _long_answer(25)
    _a, _sc, abstained, notices = _apply_answer_gates(
        "问题?", answer, [_ev("磷酸锌")], [], _verified(20), get_settings()
    )
    assert abstained is False
    hit = [n for n in notices if n.get("code") == "claims_unverified"]
    assert len(hit) == 1
    assert "5 句" in hit[0]["message"] and "20 句" in hit[0]["message"]


def test_qa19_no_notice_when_fully_verified():
    """QA-19：全部句都已验证 → 无 claims_unverified。"""
    from app.services.chat_orchestration import _apply_answer_gates

    answer = _long_answer(3)
    _a, _sc, abstained, notices = _apply_answer_gates(
        "问题?", answer, [_ev("磷酸锌")], [], _verified(3), get_settings()
    )
    assert abstained is False
    assert not [n for n in notices if n.get("code") == "claims_unverified"]


def test_qa19_no_notice_when_claim_check_off():
    """QA-19：claim-check 关闭（verified is None）→ 无 claims_unverified。"""
    from app.services.chat_orchestration import _apply_answer_gates

    _a, _sc, _ab, notices = _apply_answer_gates(
        "问题?", _long_answer(25), [_ev("磷酸锌")], [], None, get_settings()
    )
    assert not [n for n in notices if n.get("code") == "claims_unverified"]


def test_qa19_gate_denominator_unchanged():
    """QA-19：拒答门分母仍是已验证子集（20 条），不含 unverified 尾部；
    拒答时不追加 unverified 提示。"""
    from app.services.chat_orchestration import _apply_answer_gates

    answer, _sc, abstained, notices = _apply_answer_gates(
        "问题?", _long_answer(25), [_ev("磷酸锌")], [], _verified(20, unsupported=11),
        get_settings(),
    )
    assert abstained is True
    assert "20 条论断中 11 条缺乏证据支撑" in answer
    assert not [n for n in notices if n.get("code") == "claims_unverified"]


# ── QA-20(a)：纯代词追问澄清 ─────────────────────────────────────────────


class _ClarifySettings:
    chat_clarification_enabled = True
    kg_enabled = False


def test_qa20a_pronoun_only_empty_history_returns_clarification():
    """QA-20(a)：历史为空时"它怎么样？"返回澄清候选，不再一律 None。"""
    from app.services.chat_clarify import detect_clarification

    opt = detect_clarification("它怎么样？", [], [], settings=_ClarifySettings())
    assert opt is not None
    assert opt.ambiguous_term == "它怎么样？"
    assert "指代" in opt.question


def test_qa20a_pronoun_only_this_returns_clarification():
    """QA-20(a)："这个呢？"同样返回澄清候选。"""
    from app.services.chat_clarify import detect_clarification

    opt = detect_clarification("这个呢？", None, None, settings=_ClarifySettings())
    assert opt is not None
    assert opt.ambiguous_term == "这个呢？"


def test_qa20a_pronoun_only_with_antecedent_returns_none():
    """QA-20(a)：历史中有先行词（磷酸锌）时仍返回 None，交给代词消解。"""
    from app.services.chat_clarify import detect_clarification

    history = [ChatTurn(role="user", content="磷酸锌在环氧底漆中的添加量是多少？")]
    assert detect_clarification("它怎么样？", history, [], settings=_ClarifySettings()) is None


def test_qa20a_contentful_question_not_pronoun_only():
    """QA-20(a) 防过度扩展：含实义成分的追问（"它的耐盐雾表现呢？"、
    "这个配方怎么样？"）不是纯代词追问，不走澄清候选分支。"""
    from app.services.chat_clarify import _PRONOUN_ONLY

    assert _PRONOUN_ONLY.match("它怎么样？") is not None
    assert _PRONOUN_ONLY.match("这个呢？") is not None
    assert _PRONOUN_ONLY.match("它？") is not None
    assert _PRONOUN_ONLY.match("怎么样？") is not None
    assert _PRONOUN_ONLY.match("它的耐盐雾表现呢？") is None
    assert _PRONOUN_ONLY.match("这个配方怎么样？") is None
    assert _PRONOUN_ONLY.match("它好吗？") is None


def test_qa20a_pronoun_only_unrelated_history_returns_clarification():
    """QA-20(a)：历史无任何先行词（无关内容）时返回澄清候选。"""
    from app.services.chat_clarify import detect_clarification

    history = [ChatTurn(role="user", content="今天天气怎么样")]
    opt = detect_clarification("这个呢？", history, [], settings=_ClarifySettings())
    assert opt is not None


# ── QA-20(b)：化学白名单配置化 + D P2-1 回归防护 ───────────────────────


def test_qa20b_default_whitelist_matches_old_hardcoded():
    """QA-20(b)：默认白名单与旧硬编码一致（配置化不改变行为）。"""
    from app.services.chat_context import _chem_token_re, _DEFAULT_CHEM_TOKEN_PATTERNS

    assert _DEFAULT_CHEM_TOKEN_PATTERNS == (
        "磷酸锌", "环氧树脂", "固化剂", "防锈颜料", "乳液",
        "牌号", "盐雾", "添加量", "wt%", "实施例", r"[A-Za-z]{2,}[- ]?\d{2,4}",
    )
    rx = _chem_token_re()
    for tok in ("磷酸锌", "环氧树脂", "固化剂", "wt%", "HW-300"):
        assert rx.search(tok) is not None, tok


def test_qa20b_epoxy_primer_not_an_antecedent(monkeypatch):
    """QA-20(b)/D P2-1 回归防护：默认表不含"环氧底漆"/"锌黄"——代词"它"
    仍消解为主题"磷酸锌"，而非最近提及的"环氧底漆"。"""
    from app.services.chat_context import _antecedents, _chem_token_re, rewrite_query

    monkeypatch.setenv("FORMUNEX_CHAT_MULTI_TURN_ENABLED", "true")
    get_settings.cache_clear()

    assert _chem_token_re().search("环氧底漆") is None
    assert _chem_token_re().search("锌黄") is None
    history = [ChatTurn(role="user", content="磷酸锌在环氧底漆中的添加量是多少？")]
    assert _antecedents(history, []) == ["磷酸锌"]
    q, rewritten = rewrite_query("它的耐盐雾表现呢？", history)
    assert rewritten is not None
    assert "磷酸锌的耐盐雾" in rewritten


def test_qa20b_whitelist_configurable_via_rules(tmp_path, monkeypatch):
    """QA-20(b)：白名单可经规则表 chem_tokens 扩展（与 ambiguous_terms
    同机制）；规则表未落地时跳过（待协调员 patch：rule_loader.py +
    chem_tokens.toml）。"""
    from app.services.chat_context import _chem_token_re
    from app.services.rule_loader import load_rules, reload_rules

    try:
        load_rules("chem_tokens")
    except KeyError:
        pytest.skip("chem_tokens 规则表待协调员 patch 落地（rule_loader.py + chem_tokens.toml）")
    over = tmp_path / "rules"
    over.mkdir()
    patterns = [
        "磷酸锌", "环氧树脂", "固化剂", "防锈颜料", "乳液",
        "牌号", "盐雾", "添加量", "wt%", "实施例", "环氧底漆",
        r"[A-Za-z]{2,}[- ]?\d{2,4}",
    ]
    (over / "chem_tokens.toml").write_text(
        # TOML 基本字符串不允许 \d 转义，正则项用单引号字面量。
        "patterns = [\n" + "".join(f"  '{p}',\n" for p in patterns) + "]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FORMUNEX_RULES_DIR", str(over))
    try:
        reload_rules()
        assert _chem_token_re().search("环氧底漆") is not None
    finally:
        monkeypatch.delenv("FORMUNEX_RULES_DIR", raising=False)
        reload_rules()
