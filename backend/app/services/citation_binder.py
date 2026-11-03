"""Task 2.6: 答案引用绑定 [^n] — citation formatting, LLM prompt template,
citation extraction, and footnote generation.

Integrates ``CitationAnchor`` (Task 2.3) with LLM answer synthesis so that
generated responses embed footnote-style ``[^n]`` markers that map back to
specific KB chunks with page/paragraph/offset provenance.
"""

from __future__ import annotations

import re
from typing import TypedDict

from ..domain.citations import CitationAnchor


# ── Regex patterns ─────────────────────────────────────────────────────────────

# Matches [^n] markers (1 or more digits) in LLM-generated text.
_CITATION_REF_RE = re.compile(r"\[\^(\d+)\]")

# Matches a single footnote definition line: [^n]: ...
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^(\d+)\]:\s", re.MULTILINE)


# ── Typed results ──────────────────────────────────────────────────────────────


class CitationBinding(TypedDict):
    """Result of binding citations into an LLM answer."""

    answer: str
    """The cleaned answer text (original footnotes stripped)."""

    footnotes: str
    """Markdown footnotes section generated from the anchors used in the answer."""

    used_citations: list[tuple[int, CitationAnchor]]
    """(index, anchor) pairs for every citation referenced in the answer."""

    references: list[dict]
    """2.1: machine-readable verified references, one per used citation.

    Each item carries ``index`` (1-based), ``chunk_id``, ``source_id``,
    ``page``, ``paragraph``, ``offset_start``, ``offset_end`` and, when
    present, ``locator`` (``CitationLocator.to_dict()``). Only citations
    that survived fail-closed validation appear here.
    """

    stats: dict
    """2.1: binding counters — ``total_anchors``, ``used`` and
    ``dropped_out_of_range`` (body markers removed by fail-closed strip)."""


# ── Request-scoped handle registry ──────────────────────────────────────────


class CitationHandleRegistry:
    """Request-scoped citation handle registry (2.1, WeKnora modelcontext parity).

    One instance per answer; never persisted, never shared across requests.
    The ``[^n]`` numbers the model sees are the handles: handle ``n`` maps
    to ``anchors[n - 1]``. The model never sees real chunk IDs, and only
    handles registered for this request can resolve — anything else is
    dropped fail-closed at render time.

    ``kind`` distinguishes evidence handles (citable) from navigation-only
    handles (jump targets, never citable). No FormuNex caller emits
    navigation handles yet; the field is reserved so a future frontend
    jump-target feature does not need a registry redesign.
    """

    def __init__(self, anchors: list[CitationAnchor]) -> None:
        self._anchors = list(anchors)
        self._kinds = ["evidence"] * len(anchors)
        self.used: set[int] = set()
        self.dropped_out_of_range = 0

    def __len__(self) -> int:
        return len(self._anchors)

    def resolve(self, n: int) -> CitationAnchor | None:
        """Return the anchor for handle ``n`` (1-based), or None if unknown."""
        if 1 <= n <= len(self._anchors):
            return self._anchors[n - 1]
        return None

    def mark_used(self, n: int) -> bool:
        """Record handle ``n`` as used; return False when it is unknown."""
        if self.resolve(n) is None:
            return False
        self.used.add(n)
        return True

    def mark_navigation(self, n: int) -> bool:
        """Demote handle ``n`` to navigation-only; False when unknown."""
        if self.resolve(n) is None:
            return False
        self._kinds[n - 1] = "navigation"
        self.used.discard(n)
        return True

    def is_evidence(self, n: int) -> bool:
        """True only for registered, evidence-kind handles."""
        return self.resolve(n) is not None and self._kinds[n - 1] == "evidence"


# ── Formatting ────────────────────────────────────────────────────────────────


def format_citation_list(
    anchors: list[CitationAnchor],
    *,
    max_text_len: int = 300,
) -> str:
    """Build a numbered citation reference list for the LLM prompt.

    Each entry is one line::

        [^1] Source: src-id, pp. 3, ¶42 — chunk text preview...

    This is injected into the prompt so the LLM knows which ``[^n]``
    markers correspond to which evidence.

    Parameters
    ----------
    anchors : list[CitationAnchor]
        Ordered list of citations (index 0 → [^1], index 1 → [^2], etc.).
    max_text_len : int
        Truncate each chunk's text preview to this many characters (default 300).
    """
    if not anchors:
        return "_无可引用的文献。_"

    lines: list[str] = []
    for i, a in enumerate(anchors, start=1):
        ref = a.to_reference(i)
        cite = a.to_citation_text()
        preview = (a.text or "")[:max_text_len]
        if len(a.text or "") > max_text_len:
            preview = f"{preview}…"
        lines.append(f"{ref} {cite} — {preview}")

    return "\n".join(lines)


def build_footnotes_section(
    anchors: list[CitationAnchor],
    used_indices: list[int] | None = None,
) -> str:
    """Generate a Markdown footnotes section from the anchor list.

    Produces::

        ---
        [^1]: Source: src-42, pp. 3, ¶5 — preview…
        [^3]: Source: src-99, pp. 12 — preview…

    When *used_indices* is provided (1-based), only those entries are emitted.

    Parameters
    ----------
    anchors : list[CitationAnchor]
        Ordered list.  Index 0 → `[^1]`.
    used_indices : list[int] or None
        1-based indices to include.  When None, all anchors are included.
    """
    if not anchors:
        return ""

    if used_indices is not None:
        used_set = set(used_indices)
        selected = [(i + 1, anchors[i]) for i in range(len(anchors)) if (i + 1) in used_set]
        selected.sort(key=lambda pair: pair[0])
    else:
        selected = [(i + 1, a) for i, a in enumerate(anchors)]

    if not selected:
        return ""

    lines: list[str] = ["", "---", ""]
    for n, a in selected:
        cite = a.to_citation_text()
        preview = (a.text or "")[:200]
        lines.append(f"[^{n}]: {cite} — {preview}")

    return "\n".join(lines)


# ── Prompt template ───────────────────────────────────────────────────────────


def build_citation_prompt(
    question: str,
    anchors: list[CitationAnchor],
    *,
    domain: str | None = None,
    system_instruction: str | None = None,
) -> str:
    """Build a citation-aware LLM prompt with numbered reference list.

    The prompt instructs the LLM to:

    1. Answer using ONLY the provided sources.
    2. Embed ``[^n]`` markers after every factual claim.
    3. Never cite evidence not present in the reference list.
    4. State 证据不足 (insufficient evidence) explicitly when sources don't cover
       a claim.

    Parameters
    ----------
    question : str
        The user's research question.
    anchors : list[CitationAnchor]
        Ordered citation anchors from retrieved chunks.
    domain : str or None
        Optional domain context (e.g., "防腐蚀涂料").
    system_instruction : str or None
        Override the default system instruction.
    """
    cite_list = format_citation_list(anchors)

    if system_instruction is None:
        system_lines = [
            "你是一位资深配方研究员（formulation scientist）。",
            "请严格遵循以下引用协议回答用户问题：",
            "",
            "**引用规则**",
            "1. 每条技术论断后插入对应的引用标记 [^n]（n 为下方引用列表中方括号内的编号）。",
            "2. 只能引用下方提供的文献——不得编造、假设或引用不存在的证据。",
            "3. 一个论断如果有多条文献支持，可写 [^1][^3]。",
            "4. 来源数据冲突时必须指出冲突，并说明取舍理由。",
            "5. 缺乏证据支撑的地方务必写「证据不足」——宁可保守，不可编造。",
            "6. 用简体中文回答，Markdown 格式，保持表格、化学式、平衡式完整。",
        ]
        system = "\n".join(system_lines)
    else:
        system = (
            f"{system_instruction}\n\n"
            "**引用规则**\n"
            "1. 每条技术论断后插入对应的引用标记 [^n]（n 为下方引用列表中方括号内的编号）。\n"
            "2. 只能引用下方提供的文献——不得编造、假设或引用不存在的证据。\n"
            "3. 一个论断如果有多条文献支持，可写 [^1][^3]。\n"
            "4. 来源数据冲突时必须指出冲突，并说明取舍理由。\n"
            "5. 缺乏证据支撑的地方务必写「证据不足」——宁可保守，不可编造。\n"
            "6. 用简体中文回答，Markdown 格式，保持表格、化学式、平衡式完整。"
        )

    domain_hint = f"\n领域：{domain}\n" if domain else ""

    return (
        f"{system}\n\n"
        f"**可引用文献**（严格按照编号引用）：\n\n"
        f"{cite_list}\n\n"
        f"{domain_hint}"
        f"**问题**：{question}\n\n"
        "请严格遵循上述引用规则回答："
    )


# ── Extraction ────────────────────────────────────────────────────────────────


def extract_citation_indices(text: str) -> list[int]:
    """Extract all unique citation reference numbers from an LLM answer.

    Finds every ``[^n]`` marker in *text* and returns the sorted,
    deduplicated list of numeric indices.

    Parameters
    ----------
    text : str
        LLM-generated answer text potentially containing ``[^1]``, etc.

    Returns
    -------
    list[int]
        Sorted unique 1-based citation indices (e.g., ``[1, 2, 5]``).
    """
    raw = _CITATION_REF_RE.findall(text)
    seen: set[int] = set()
    indices: list[int] = []
    for s in raw:
        n = int(s)
        if n > 0 and n not in seen:
            seen.add(n)
            indices.append(n)
    indices.sort()
    return indices


def extract_citation_indices_validated(
    text: str,
    total_anchors: int,
) -> tuple[list[int], list[int]]:
    """Extract citation indices and separate valid vs out-of-range.

    Returns
    -------
    (valid_indices, out_of_range)
        *valid_indices* are within [1, total_anchors].
        *out_of_range* are indices that exceed total_anchors (likely LLM
        hallucination — the model cited a reference number that doesn't exist).
    """
    all_indices = extract_citation_indices(text)
    valid: list[int] = []
    out_of_range: list[int] = []
    for n in all_indices:
        if 1 <= n <= total_anchors:
            valid.append(n)
        else:
            out_of_range.append(n)
    return valid, out_of_range


def has_citation_markers(text: str) -> bool:
    """Return True if *text* contains any ``[^n]`` markers."""
    return bool(_CITATION_REF_RE.search(text))


def strip_existing_footnotes(text: str) -> str:
    """Remove any pre-existing ``[^n]: …`` footnote definition lines from *text*.

    Useful when merging LLM output with a fresh footnotes section to avoid
    duplication.
    """
    return _FOOTNOTE_DEF_RE.sub("", text).rstrip()


# ── Binding: full pipeline ────────────────────────────────────────────────────


def bind_citations(
    answer: str,
    anchors: list[CitationAnchor],
    *,
    strict: bool = True,
) -> CitationBinding:
    """Post-process an LLM answer: extract used citations + append footnotes.

    Parameters
    ----------
    answer : str
        Raw LLM answer that (should) contain ``[^n]`` markers.
    anchors : list[CitationAnchor]
        Ordered citation anchors (index 0 → ``[^1]``).
    strict : bool
        2.1 fail-closed rendering (default True). When True, body markers
        whose handle is not registered for this request are removed from
        the answer and counted in ``stats["dropped_out_of_range"]``; only
        surviving markers feed ``used_citations``/``references``/footnotes.
        When False, legacy behavior is kept (unknown markers left in place).

    Returns
    -------
    CitationBinding
        Contains the cleaned answer, footnotes section, list of
        (index, anchor) pairs for each cited reference, the machine-readable
        ``references`` payload, and ``stats`` counters.
    """
    registry = CitationHandleRegistry(anchors)

    # Strip any footnotes the LLM might have generated itself, BEFORE
    # extracting markers: a footnote-definition line must not itself count
    # as a citation claim.
    clean_answer = strip_existing_footnotes(answer)

    if strict:
        def _fail_closed(match: re.Match[str]) -> str:
            n = int(match.group(1))
            if registry.resolve(n) is None:
                registry.dropped_out_of_range += 1
                return ""
            registry.mark_used(n)
            return match.group(0)

        clean_answer = _CITATION_REF_RE.sub(_fail_closed, clean_answer)

    used_indices = extract_citation_indices(clean_answer)
    used_pairs: list[tuple[int, CitationAnchor]] = []
    for n in used_indices:
        idx = n - 1
        if 0 <= idx < len(anchors):
            used_pairs.append((n, anchors[idx]))

    footnotes = build_footnotes_section(anchors, used_indices)

    references: list[dict] = []
    for n, a in used_pairs:
        ref: dict = {
            "index": n,
            "chunk_id": a.chunk_id,
            "source_id": a.source_id,
            "page": a.page,
            "paragraph": a.paragraph,
            "offset_start": a.offset_start,
            "offset_end": a.offset_end,
        }
        if a.locator is not None:
            loc = a.locator.to_dict()
            if loc:
                ref["locator"] = loc
        references.append(ref)

    stats = {
        "total_anchors": len(anchors),
        "used": len(used_pairs),
        "dropped_out_of_range": registry.dropped_out_of_range,
    }

    # W2-6 (P1-2): link the claim (answer text) to its evidence sources.
    # Fail-open: provenance must never break citation binding.
    try:
        from . import provenance as _prov

        _prov.record_claim_sources(
            clean_answer,
            [a.source_id for _, a in used_pairs],
            relation="cites",
        )
    except Exception:
        pass

    return CitationBinding(
        answer=clean_answer,
        footnotes=footnotes,
        used_citations=used_pairs,
        references=references,
        stats=stats,
    )


def format_answer_with_citations(
    answer: str,
    anchors: list[CitationAnchor],
    *,
    strict: bool = True,
) -> str:
    """Convenience: return answer + footnotes as a single markdown string."""
    binding = bind_citations(answer, anchors, strict=strict)
    return f"{binding['answer']}\n{binding['footnotes']}"


__all__ = [
    "CitationBinding",
    "CitationHandleRegistry",
    "build_citation_prompt",
    "build_footnotes_section",
    "format_citation_list",
    "extract_citation_indices",
    "extract_citation_indices_validated",
    "has_citation_markers",
    "strip_existing_footnotes",
    "bind_citations",
    "format_answer_with_citations",
]
