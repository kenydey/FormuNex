"""Claim-level sourcing for chat answers (+ Wave D sources_audit)."""
from __future__ import annotations

import logging
import re
from typing import Any

from ..config import Settings, get_settings
from ..domain.chat_schemas import SourcedClaim, StructuredAnswer
from ..domain.schemas import Evidence
from ..pipeline.claim_checker import (
    ClaimVerdict,
    VerifiedClaim,
    verify_claim_offline,
    verify_claims_llm,
)

logger = logging.getLogger(__name__)

# A full stop ends a sentence only when blank space (or the end) follows it. Splitting after every "." cut "pH 6.5" into
# "pH 6." and "5", a DOI "10.1000/x" into "10." and "1000/x", and "e.g." / "Fig.2" in two - so the claims that were verified
# against the evidence were fragments, and every decimal in an answer was checked as if it were a separate statement.
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?])\s*|(?<=\.)\s+|(?<=\.)$")

# 2026-09-04 (P3): 每次问答新建 executor + shutdown(wait=False) 会让超时的
# deepseek 阻塞线程成为孤儿(60s idle×2 重试 ≈ 2 分钟才自然消亡), 慢窗口
# 高频问答下 OS 线程持续积累。改为模块级共享池(max_workers=2): 超时任务
# 仍占 worker 直至上游返回, 但总数封顶、排队自然节流, 不再无限新增线程。
import concurrent.futures as _cf

_CLAIM_EXECUTOR: _cf.ThreadPoolExecutor | None = None


def _claim_executor() -> _cf.ThreadPoolExecutor:
    global _CLAIM_EXECUTOR
    if _CLAIM_EXECUTOR is None:
        _CLAIM_EXECUTOR = _cf.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="claim-verify"
        )
    return _CLAIM_EXECUTOR


def verify_answer_claims(
    question: str,
    answer: str,
    sources: list[Evidence],
    structured: StructuredAnswer | None = None,
    *,
    settings: Settings | None = None,
) -> list[VerifiedClaim] | None:
    """Run claim verification once; None when claim-check disabled."""
    settings = settings or get_settings()
    if not settings.chat_claim_check_enabled:
        return None

    claims = _extract_claims(answer, structured)
    if not claims:
        return []

    # v27 P2-16: 记录验证覆盖率（实际验证句数 / 答案总句数）。分母与
    # _extract_claims 同源（过滤后句数），否则孤立 [^n] 等伪影会虚增分母。
    _total_sentences = _claim_sentence_count(answer, structured)
    if _total_sentences:
        logger.info(
            "chat claim coverage: %d/%d sentences sampled for verification",
            len(claims),
            _total_sentences,
        )

    try:
        _ex = _claim_executor()
        _fut = None
        try:
            _fut = _ex.submit(verify_claims_llm, question, claims, sources)
            return list(_fut.result(timeout=12))
        except Exception:
            if _fut is not None:
                _fut.cancel()
            raise
    except Exception:
        return [verify_claim_offline(c, sources) for c in claims]


def build_sourced_claims(
    question: str,
    answer: str,
    sources: list[Evidence],
    structured: StructuredAnswer | None = None,
    *,
    settings: Settings | None = None,
    verified: list[VerifiedClaim] | None = None,
) -> list[SourcedClaim] | None:
    settings = settings or get_settings()
    if verified is None:
        verified = verify_answer_claims(
            question, answer, sources, structured=structured, settings=settings
        )
    if verified is None:
        return None

    out: list[SourcedClaim] = []
    # _indices_to_chunk_ids strips the "kb:" prefix, so index both forms.
    by_id: dict[str, object] = {}
    for e in sources:
        by_id[e.identifier] = e
        if (e.identifier or "").startswith("kb:"):
            by_id[e.identifier[3:]] = e
    cap = float(getattr(settings, "wiki_claim_confidence_cap", 1.0) or 1.0)
    for v in verified:
        chunk_ids = _indices_to_chunk_ids(v.evidence_indices, sources)
        status = _map_verdict(v.verdict)
        conf = 0.9 if status == "supported" else 0.4 if status == "weak" else 0.1
        # QA-12 逃逸 A：supported 但 chunk_ids 为空（核验未给出任何证据
        # 下标）是无据支撑，不得拿 0.9，降级为 weak。
        if not chunk_ids and status == "supported":
            logger.warning(
                "wiki cap: supported claim with no evidence indices, "
                "downgrading to weak: %r",
                (v.text or "")[:80],
            )
            status = "weak"
            conf = 0.4
        # QA-12 逃逸 B：封顶条件改为"全部已知来源均为 wiki 且至少一个
        # 已知"（见 _wiki_only_sources）；未知标识不否决封顶，只记 warning，
        # 否则任一不可解析的标识就能让整条封顶逃逸。
        elif _wiki_only_sources(chunk_ids, by_id):
            conf = min(conf, cap)
        out.append(
            SourcedClaim(
                text=v.text,
                chunk_ids=chunk_ids,
                confidence=conf,
                status=status,
                raw_verdict=str(getattr(v.verdict, "value", v.verdict))
                if v.verdict is not None
                else None,
                # QA-17: 离线 verdict 标注 verifier="offline-overlap"，与 LLM
                #  verdict 区分。SourcedClaim.verifier 字段本身见协调员 patch
                # （domain/chat_schemas.py 不在本波文件清单内）；pydantic
                #  默认忽略多余字段，故此行在字段落地前静默不起效、落地后生效。
                verifier=getattr(v, "verifier", None),
            )
        )
    return out


def build_sources_audit(
    sources: list[Evidence],
    *,
    verified: list[VerifiedClaim] | None = None,
    sourced_claims: list[SourcedClaim] | None = None,
    enabled: bool = True,
    # P1-l: 未验证 claim 计数（长答案超 _MAX_CLAIMS 截断时尾部逃逸）
    n_unverified: int = 0,
) -> dict[str, Any] | None:
    """Wave D — claim→passage audit table (fail-open when disabled)."""
    if not enabled:
        return None

    rows: list[dict[str, Any]] = []
    if verified is not None:
        for v in verified:
            chunk_ids = _indices_to_chunk_ids(v.evidence_indices, sources)
            grade = _audit_grade_from_verdict(v.verdict)
            rows.append(
                {
                    "claim": v.text,
                    "grade": grade,
                    "chunk_ids": chunk_ids,
                    "locators": _locators_for_chunks(chunk_ids, sources),
                    "note": (v.reason or "").strip()[:240] or None,
                    # QA-17: 离线 verdict 标注核验器，与 LLM verdict 区分。
                    "verifier": getattr(v, "verifier", None),
                }
            )
    elif sourced_claims is not None:
        for sc in sourced_claims:
            grade = _audit_grade_from_status(sc.status)
            rows.append(
                {
                    "claim": sc.text,
                    "grade": grade,
                    "chunk_ids": list(sc.chunk_ids or []),
                    "locators": _locators_for_chunks(list(sc.chunk_ids or []), sources),
                    "note": None,
                    # QA-17: 同上（SourcedClaim.verifier 字段落地前为 None）。
                    "verifier": getattr(sc, "verifier", None),
                }
            )
    else:
        return {
            "schema_version": 1,
            "rows": [],
            "summary": {
                "supported": 0,
                "partial": 0,
                "unsupported": 0,
                "contradicted": 0,
                "unverified": n_unverified,
            },
        }

    summary = {"supported": 0, "partial": 0, "unsupported": 0, "contradicted": 0, "unverified": n_unverified}
    for row in rows:
        g = str(row.get("grade") or "unsupported")
        if g in summary:
            summary[g] += 1
        else:
            summary["unsupported"] += 1

    return {"schema_version": 1, "rows": rows, "summary": summary}


# 孤立引用标记（如 [^1]、[^1][^2] 连写）。
_MARKER_RE = re.compile(r"\[\^\d+\]")
# 澄清提示 boilerplate（run_chat 在 review 之前追加进答案，见
# chat_orchestration 追加模板 "（默认按「{hint}」理解；如需其他含义请说明。）"）。
_CLARIFICATION_RE = re.compile(r"^（默认按「[^」]*」理解；如需其他含义请说明。?）?$")
# 纯标点 / 纯括号片段判定用字符集（含空白）。
_PUNCT_ONLY_CHARS = frozenset("。！？!?，、；：·…—-（）()「」『』《》〈〉[] \t\n\r")


def _is_non_propositional(fragment: str) -> bool:
    """True when a split fragment carries no proposition and must not be
    verified as a claim: only citation markers, only punctuation/brackets,
    or the clarification boilerplate run_chat appends before review.
    Batch 1-3 shadow run: all 35 flagged claims were such fragments
    (100% false positives), so they are filtered here, not in the
    verifier — the verdict thresholds stay untouched.
    """
    s = (fragment or "").strip()
    if not s:
        return True
    # 剥离引用标记后看剩余内容。
    s = _MARKER_RE.sub("", s).strip()
    if not s:
        return True  # 孤立 [^n]（含 [^1][^2] 连写）
    if all(ch in _PUNCT_ONLY_CHARS for ch in s):
        return True  # 空 / 纯标点 / 纯括号片段（如孤立 "）"）
    return _CLARIFICATION_RE.match(s) is not None


def _claim_sections(
    answer: str, structured: StructuredAnswer | None
) -> list[list[str]]:
    """Split an answer into per-section claim sentences, dropping
    non-propositional fragments. Shared by _extract_claims and the
    coverage / unverified counters so all three count the same
    sentences (batch B). Structured answers use key_findings verbatim.
    """
    if structured and structured.key_findings:
        return [[c.strip() for c in structured.key_findings if c.strip()]]
    from ..pipeline.claim_checker import _SECTION_HEADING_RE

    sections = [
        p for p in _SECTION_HEADING_RE.split(answer or "") if (p or "").strip()
    ] or [answer or ""]
    out: list[list[str]] = []
    for sec in sections:
        sentences = [
            p.strip()
            for p in _SENTENCE_SPLIT.split(sec)
            if p.strip() and not _is_non_propositional(p)
        ]
        out.append(sentences)
    return out


def _claim_sentence_count(
    answer: str, structured: StructuredAnswer | None
) -> int:
    """Total filtered claim sentences, before the _MAX_CLAIMS cap."""
    return sum(len(s) for s in _claim_sections(answer, structured))


def _extract_claims(answer: str, structured: StructuredAnswer | None) -> list[str]:
    if structured and structured.key_findings:
        return [c.strip() for c in structured.key_findings if c.strip()]
    # v27 P2-16: 与 claim_checker.check_claims 统一采样策略 —— per-section
    # 每 section 最多 8 句、总共最多 20 条，不再硬截前 8 句。
    # （check_claims 侧按行切分适合 markdown 报告；问答答案是散文体，
    # 这里按句切分，但采样策略一致，避免长答案尾部逃逸验证。）
    from ..pipeline.claim_checker import _MAX_CLAIMS

    max_total = _MAX_CLAIMS  # 20
    sections = _claim_sections(answer, structured)
    # 多 section 时每段最多 8 句（防头部偏置）；单段答案允许到总上限，
    # 否则 12 句的单段答案尾部仍然逃逸。
    per_section = 8 if len(sections) > 1 else max_total
    out: list[str] = []
    for sentences in sections:
        for s in sentences[:per_section]:
            out.append(s)
            if len(out) >= max_total:
                return out
    return out


def _wiki_only_sources(chunk_ids: list[str], by_id: dict[str, object]) -> bool:
    """QA-12：chunk_ids 是否全部指向已知的 Wiki 来源（至少一个已知）。

    未知标识不否决封顶（只记 warning）：调用方无法确认它是不是 Wiki，
    但也不能让一条未知标识豁免整条论断的封顶。空 chunk_ids 返回 False
    （无据支撑走逃逸 A 的降级分支，不走封顶）；全未知同样返回 False
    （无已知来源可判定，封顶无依据——该情形已记 warning）。
    """
    from .wiki.retrieve import is_wiki_evidence

    if not chunk_ids:
        return False
    known = [by_id[c] for c in chunk_ids if c in by_id]
    unknown = [c for c in chunk_ids if c not in by_id]
    if unknown:
        logger.warning(
            "wiki cap: %d/%d claim sources unresolvable, "
            "capping on known sources only: %r",
            len(unknown),
            len(chunk_ids),
            unknown[:3],
        )
    if not known:
        return False
    return all(is_wiki_evidence(e) for e in known)


def _indices_to_chunk_ids(indices: list[int], sources: list[Evidence]) -> list[str]:
    ids: list[str] = []
    for idx in indices:
        if 0 <= idx < len(sources):
            ident = sources[idx].identifier or ""
            if ident.startswith("kb:"):
                ids.append(ident[3:])
            elif ident:
                ids.append(ident)
    return list(dict.fromkeys(ids))


def _map_verdict(verdict: ClaimVerdict) -> str:
    """Legacy SourcedClaim status (conflicting stays weak for back-compat)."""
    if verdict == ClaimVerdict.supported:
        return "supported"
    if verdict in (ClaimVerdict.insufficient, ClaimVerdict.conflicting):
        return "weak"
    return "unsupported"


def _audit_grade_from_verdict(verdict: ClaimVerdict) -> str:
    if verdict == ClaimVerdict.supported:
        return "supported"
    if verdict == ClaimVerdict.insufficient:
        return "partial"
    if verdict == ClaimVerdict.conflicting:
        return "contradicted"
    return "unsupported"


def _audit_grade_from_status(status: str) -> str:
    if status == "supported":
        return "supported"
    if status == "weak":
        return "partial"
    return "unsupported"


def _locators_for_chunks(
    chunk_ids: list[str], sources: list[Evidence]
) -> list[dict[str, Any]]:
    by_id: dict[str, Evidence] = {}
    for ev in sources:
        ident = ev.identifier or ""
        if ident.startswith("kb:"):
            by_id[ident[3:]] = ev
        if ident:
            by_id[ident] = ev
    out: list[dict[str, Any]] = []
    for cid in chunk_ids:
        ev = by_id.get(cid)
        if ev is None:
            out.append({"chunk_id": cid, "page": None, "paragraph": None})
            continue
        out.append(
            {
                "chunk_id": cid,
                "page": ev.page,
                "paragraph": ev.paragraph,
            }
        )
    return out
