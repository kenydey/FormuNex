"""Soft clarification for ambiguous chat queries."""
from __future__ import annotations

import logging
import re

from ..config import Settings, get_settings
from ..domain.chat_schemas import ChatTurn, ClarificationOption, ClarifiedEntity, StructuredAnswer
from .errors import degrade_return

logger = logging.getLogger(__name__)

# QA-20(a)：纯代词追问 —— 仅由代词（那/它/这个/那个/这）与轻疑问词
# （怎么样/如何/呢/吗）组成、无任何实义成分的追问，如"它？"、"它怎么样？"、
# "这个呢？"。旧模式只匹配单个 token，"它怎么样？""这个呢？"根本进不了
# 该分支（直接落到 KG/词典路径返回 None）；现改为重复组合，覆盖任务示例。
_PRONOUN_ONLY = re.compile(r"^(?:那|它|这个|那个|这|怎么样|如何|呢|吗)+[？?]?$")


def detect_clarification(
    question: str,
    history: list[ChatTurn] | None,
    clarified_entities: list[ClarifiedEntity] | None,
    *,
    settings: Settings | None = None,
) -> ClarificationOption | None:
    settings = settings or get_settings()
    if not settings.chat_clarification_enabled:
        return None

    q = (question or "").strip()
    if not q:
        return None
    # QA-20(a)：纯代词追问（"它怎么样？""这个呢？"）不再一律放行 —— 历史为空
    # 或代词无先行词时返回澄清候选；有先行词时仍返回 None，交给 rewrite_query
    # 的代词消解处理。
    if _PRONOUN_ONLY.match(q):
        return _clarify_pronoun_only(q, history, clarified_entities)

    resolved_terms = {(ce.term or "").strip() for ce in (clarified_entities or []) if ce.term}

    try:
        kg_option = _clarify_via_kg(q, resolved_terms, settings)
        if kg_option:
            return kg_option
        return _clarify_via_lexicon(q, resolved_terms)
    except Exception as exc:
        degrade_return(logger, exc, "chat clarification failed", None)
        return None


def apply_assumption_to_structured(
    structured: StructuredAnswer | None,
    clarification: ClarificationOption | None,
) -> StructuredAnswer | None:
    if structured is None or clarification is None:
        return structured
    assumption = (
        f"「{clarification.ambiguous_term}」按 {clarification.possible_meanings[0]} 解释"
        if clarification.possible_meanings
        else f"「{clarification.ambiguous_term}」存在多种含义"
    )
    assumptions = list(structured.assumptions or [])
    if assumption not in assumptions:
        assumptions.append(assumption)
    return structured.model_copy(update={"assumptions": assumptions})


def _clarify_pronoun_only(
    question: str,
    history: list[ChatTurn] | None,
    clarified_entities: list[ClarifiedEntity] | None,
) -> ClarificationOption | None:
    """QA-20(a)：纯代词追问的澄清候选。

    历史中有可消解的先行词（CAS / 化学实体 / 文献标题，见
    ``chat_context._antecedents``）时返回 None——代词消解能接住，不打断
    用户；历史为空或无任何先行词时返回澄清候选（possible_meanings 为空：
    指代对象未知，无法枚举候选，只能请用户明示）。Fail-open：先行词判定
    异常时按"无先行词"处理（返回澄清候选，不静默放行）。
    """
    try:
        from .chat_context import _antecedents

        antecedents = _antecedents(list(history or []), list(clarified_entities or []))
    except Exception as exc:  # noqa: BLE001 - fail-open: 判不出来就澄清
        logger.debug("pronoun-only antecedent check failed: %s", exc)
        antecedents = []
    if antecedents:
        return None
    return ClarificationOption(
        ambiguous_term=question,
        possible_meanings=[],
        question=f"你说的「{question}」是指什么？请说明具体指代对象。",
        candidate_entity_ids=[],
    )


def _clarify_via_kg(
    question: str,
    resolved_terms: set[str],
    settings: Settings,
) -> ClarificationOption | None:
    if not settings.kg_enabled:
        return None
    from .kg.entity_resolver import resolve_query

    resolved = resolve_query(question, settings=settings)
    if len(resolved.chemicals) >= 2 and len(resolved.trade_products) >= 1:
        term = question[:32]
        if term in resolved_terms:
            return None
        meanings = [c.canonical_name for c in resolved.chemicals[:3]]
        meanings += [t.trade_name for t in resolved.trade_products[:2]]
        ids = [c.id for c in resolved.chemicals[:3]] + [t.id for t in resolved.trade_products[:2]]
        return ClarificationOption(
            ambiguous_term=term,
            possible_meanings=meanings,
            question=f"你提到的实体可能指：{' / '.join(meanings[:3])}？",
            candidate_entity_ids=ids[:5],
        )
    return None


def _clarify_via_lexicon(question: str, resolved_terms: set[str]) -> ClarificationOption | None:
    from .rule_loader import load_rules  # R1: 歧义词典配置化(2026-09-04)

    for term, cfg in load_rules("ambiguous_terms").items():
        if term not in question or term in resolved_terms:
            continue
        candidates = [tuple(c) for c in cfg.get("candidates", [])]  # [含义, 实体id]
        meanings = [c[0] for c in candidates]
        ids = [c[1] for c in candidates if len(c) > 1 and c[1]]
        return ClarificationOption(
            ambiguous_term=term,
            possible_meanings=meanings,
            question=f"你说的「{term}」更接近哪一种？",
            candidate_entity_ids=[i for i in ids if i],
        )
    return None
