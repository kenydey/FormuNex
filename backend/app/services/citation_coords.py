"""v25: reviewer 引用坐标对齐的共享函数。

Batch 2 (Q3=A): Wiki 证据不再从 Claims 剥离 —— 答案中的 ``[^n]`` 指向
wiki 条目时不再被删除。Wiki 证据以 ``source="wiki"`` 标注（见
``wiki.retrieve.is_wiki_evidence``），纯 wiki 支撑的论断在
``chat_claims.build_sourced_claims`` 里置信度封顶，低于原始文献。
``claims_evidence`` 保留为兼容入口（现为恒等），映射函数随之成为恒等映射。

chat.py 与 reviewer_fix_loop.py 共用，消除双实现漂移。
"""
from __future__ import annotations


def claims_evidence(evidence: list) -> list:
    """Batch 2: Wiki rows stay citable (labeled, confidence-capped downstream).

    Previously stripped Wiki rows so claims could not cite compiled pages
    as Raw proof. Q3=A reverses that: wiki evidence enters Claims with its
    ``source="wiki"`` label; the confidence cap lives in build_sourced_claims.
    """
    return list(evidence)


def claims_evidence_with_mapping(evidence: list) -> tuple[list, dict[int, int]]:
    """返回 (过滤后列表, old_idx → new_idx 映射)，供 reviewer 坐标对齐用。

    用 id() 建立 old → new 映射（Evidence 可能不可哈希）。
    """
    filtered = claims_evidence(evidence)
    id_to_new = {id(e): i for i, e in enumerate(filtered)}
    mapping = {
        old: id_to_new[id(e)] for old, e in enumerate(evidence) if id(e) in id_to_new
    }
    return filtered, mapping


def remap_citation_numbers(
    answer: str,
    mapping: dict[int, int],
    n_evidence: int | None = None,
) -> str:
    """用 old → new 映射重写答案中的 ``[^n]``，删除指向被过滤条目的标记。

    ``[^1]`` 是 1-based，mapping 的 key 是 0-based old_idx。

    v27 P2-17: 越界引用（如 ``[^99]`` 而原文只有 5 条 evidence）不再无痕删除 ——
    传 ``n_evidence``（过滤前原文条数）时记 warning，调用方可在 reviewer 前看到。
    Batch 2 起 wiki 条目不再被过滤，映射为恒等，wiki 引用不再删除。
    """
    import logging
    import re

    logger = logging.getLogger(__name__)
    dangling: list[int] = []

    def _repl(m: re.Match) -> str:
        old_idx = int(m.group(1)) - 1
        new_idx = mapping.get(old_idx)
        if new_idx is None:
            if n_evidence is not None and old_idx >= n_evidence:
                dangling.append(old_idx + 1)  # 记回 1-based
            return ""  # 越界或映射外，删除标记
        return f"[^{new_idx + 1}]"

    out = re.sub(r"\[\^(\d+)\]", _repl, answer)
    if dangling:
        logger.warning(
            "remap_citation_numbers: %d dangling citation(s) %s beyond %d evidence items — "
            "possible hallucinated citation numbers",
            len(dangling),
            sorted(set(dangling)),
            n_evidence,
        )
    return out


def reviewer_view(answer: str, evidence: list) -> tuple[str, list]:
    """The (answer, evidence) pair a reviewer should see: raw evidence only, with the answer's ``[^n]`` rewritten to match."""
    kept, mapping = claims_evidence_with_mapping(evidence)
    return remap_citation_numbers(answer, mapping, n_evidence=len(evidence)), kept
