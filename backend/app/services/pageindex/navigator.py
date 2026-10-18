"""Stage 2 of PageIndex retrieval: walk one document's ToC tree to the sections
that answer a question, across pages, and record why each step was taken.

Two modes share one budget (``max_hops`` steps, ``max_input_chars`` of node
text) and one output shape:

* ``llm`` — the model sees the outline (titles, pages, anchors, refs — never the
  whole document), picks nodes, reads them, and either stops or names the next
  nodes to read. Cross references in the tree ("见表 2") are offered as
  candidates at every step.
* ``heuristic`` — no model (offline install, or the model failed): nodes are
  scored by query overlap with their title and text, the best ones are read,
  and the reference edges are followed breadth-first. Deterministic, so it is
  also the regression baseline.

Every result carries ``node_path`` (visit order) and ``reasoning_trace`` (one
entry per step), the auditable record plan §5.1.1 asks for.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .tree_builder import index_nodes, outline, parent_map

TextLoader = Callable[[dict[str, Any]], str]
LLMJson = Callable[[str], dict | None]

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*|[一-鿿]", re.I)


def estimate_tokens(text: str) -> int:
    """Heuristic token count for budget enforcement (plan §5.1.1: 16k input tokens).

    ~1 token per CJK char, ~1 token per 4 non-CJK chars. A heuristic, not a
    tokenizer — it only gates how much node text the navigator may read, so
    over-estimating is safe (stops early) and under-estimating is bounded by
    the parallel ``max_chars`` cap.
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff" or "\uac00" <= ch <= "\ud7af")
    return cjk + (len(text) - cjk + 3) // 4


def _truncate_to_tokens(text: str, budget: int) -> str:
    """Largest prefix of ``text`` whose estimate_tokens() fits ``budget`` (exact scan)."""
    if budget <= 0:
        return ""
    if estimate_tokens(text) <= budget:
        return text
    # Track cost in quarter-tokens: CJK = 4 quarters, other = 1 quarter.
    used = 0
    out: list[str] = []
    for ch in text:
        cost = 4 if "\u3040" <= ch <= "\u30ff" or "\u3400" <= ch <= "\u9fff" or "\uac00" <= ch <= "\ud7af" else 1
        if used + cost > budget * 4:
            break
        used += cost
        out.append(ch)
    return "".join(out)


@dataclass
class NavigationResult:
    source_id: str
    mode: str  # llm | heuristic
    node_path: list[str] = field(default_factory=list)
    reasoning_trace: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    hops: int = 0
    chars_read: int = 0
    tokens_read: int = 0  # estimate_tokens() over the node text actually read
    stopped: str = ""  # answered | budget | exhausted | error
    # Plan §5.1.1 output: an answer composed from the evidence actually read.
    # "llm" = model-composed; "extractive" = quoted snippets (no model);
    # "none" = no evidence to answer from.
    answer: str | None = None
    answer_source: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id, "mode": self.mode, "node_path": self.node_path,
            "reasoning_trace": self.reasoning_trace, "evidence": self.evidence, "hops": self.hops,
            "chars_read": self.chars_read, "tokens_read": self.tokens_read, "stopped": self.stopped,
            "answer": self.answer, "answer_source": self.answer_source,
        }


def _tokens(text: str) -> set[str]:
    toks = [t.lower() for t in _TOKEN_RE.findall(text or "")]
    out = set(toks)
    # Chinese has no word boundaries: character bigrams stand in for words.
    cjk = [t for t in toks if len(t) == 1 and "一" <= t <= "鿿"]
    out.update(a + b for a, b in zip(cjk, cjk[1:], strict=False))
    return out


def _pages(node: dict[str, Any]) -> list[int | None]:
    return list(node.get("page_range") or [None, None])


class _Walk:
    def __init__(self, source_id: str, tree: dict, load_text: TextLoader, max_hops: int, max_chars: int,
                 mode: str, max_tokens: int = 16000) -> None:
        self.nodes = index_nodes(tree)
        self.parents = parent_map(tree)
        self.load_text = load_text
        self.max_hops = max_hops
        self.max_chars = max_chars
        self.max_tokens = max_tokens
        self.result = NavigationResult(source_id=source_id, mode=mode)
        self.read: set[str] = set()

    def branch(self, node_id: str) -> str:
        """The top-level section a node sits in (its child-of-root ancestor)."""
        cur = node_id
        while self.parents.get(cur) is not None and self.parents.get(self.parents[cur]) is not None:
            cur = self.parents[cur]
        return cur

    def budget_left(self) -> bool:
        return (self.result.hops < self.max_hops and self.result.chars_read < self.max_chars
                and self.result.tokens_read < self.max_tokens)

    def visit(self, node_id: str, action: str, reason: str) -> str | None:
        node = self.nodes.get(node_id)
        if node is None or node_id in self.read or not self.budget_left():
            return None
        text = self.load_text(node) or ""
        remaining = self.max_chars - self.result.chars_read
        text = text[:remaining]
        # Token budget is enforced alongside the char budget (exact prefix
        # truncation — proportional shrinking can overshoot when the prefix
        # is denser in CJK than the whole text).
        token_remaining = self.max_tokens - self.result.tokens_read
        text = _truncate_to_tokens(text, token_remaining)
        self.read.add(node_id)
        self.result.hops += 1
        self.result.chars_read += len(text)
        self.result.tokens_read += estimate_tokens(text)
        self.result.node_path.append(node_id)
        self.result.reasoning_trace.append({
            "step": self.result.hops, "action": action, "node_id": node_id, "title": node.get("title"),
            "label": node.get("label"), "page_range": node.get("page_range"), "reason": reason,
        })
        self.result.evidence.append({
            "node_id": node_id, "title": node.get("title"), "label": node.get("label"),
            "page_range": node.get("page_range"), "chunk_ids": list(node.get("chunk_ids") or []),
            "table_ids": list(node.get("table_ids") or []), "text": text,
        })
        return text

    def ref_targets(self, node_id: str) -> list[tuple[str, dict]]:
        node = self.nodes.get(node_id) or {}
        return [(r["to"], r) for r in node.get("refs") or [] if r["to"] not in self.read]

    def back_refs(self, node_id: str) -> list[str]:
        """Nodes whose text cites *node_id* (e.g. the results table that cites 实施例 3)."""
        return [nid for nid, n in self.nodes.items()
                if nid not in self.read and any(r["to"] == node_id for r in n.get("refs") or [])]


def _extractive_answer(result: NavigationResult, query: str) -> None:
    """Fail-open answer: quote the top evidence with page refs, labelled extractive.

    Used when no LLM is available (heuristic mode) or the LLM answer call
    failed. Quotes only — no generated claims, so nothing can be hallucinated.
    """
    if not result.evidence:
        result.answer = None
        result.answer_source = "none"
        return
    parts = []
    for ev in result.evidence[:2]:
        pr = ev.get("page_range") or [None, None]
        page = f"p.{pr[0]}" if pr[0] else ""
        snippet = (ev.get("text") or "").strip().replace("\n", " ")[:400]
        if snippet:
            parts.append(f"[{ev.get('title') or ev['node_id']}{f' {page}' if page else ''}] {snippet}")
    result.answer = "\n".join(parts) if parts else None
    result.answer_source = "extractive" if parts else "none"


_ANSWER_PROMPT = """根据下面从长文档中读到的证据回答问题。只使用证据中的内容，不要编造；证据不足时明说哪部分缺失。只输出 JSON：{{"answer": "..."}}。

问题：{query}

证据：
{evidence}"""


def _llm_answer(result: NavigationResult, query: str, llm: LLMJson) -> bool:
    """Ask the model to compose an answer from the evidence actually read. False on any failure."""
    try:
        ev_text = "\n".join(
            f"[{e.get('title') or e['node_id']} p.{(e.get('page_range') or [None])[0]}] "
            f"{(e.get('text') or '')[:2000]}"
            for e in result.evidence
        )
        out = llm(_ANSWER_PROMPT.format(query=query, evidence=ev_text))
        if isinstance(out, dict) and isinstance(out.get("answer"), str) and out["answer"].strip():
            result.answer = out["answer"].strip()
            result.answer_source = "llm"
            return True
    except Exception:
        pass
    return False


# --------------------------------------------------------------------------- heuristic


_STOP = set("的了是在和与及或其中为对于按将从由该所之一个们么什哪些如何怎样请问吗呢")
_DEFINITION_Q = re.compile(r"定义|是什么|指什么|含义|defined?|definition|what is", re.I)
_DEFINITION_TEXT = re.compile(r"定义为|是指|指的是|称为|defined as|refers to|means", re.I)


def _weight(token: str) -> float:
    # One Chinese character carries little meaning on its own; bigrams and ASCII terms carry it.
    return 0.3 if len(token) == 1 and not token.isascii() else 1.0


def _score_nodes(walk: _Walk, query: str) -> list[tuple[float, str]]:
    import math

    q = {t for t in _tokens(query) if t not in _STOP}
    if not q:
        return []
    docs: dict[str, tuple[set[str], set[str], str]] = {}
    for nid, node in walk.nodes.items():
        if node.get("kind") == "root" or not node.get("chunk_ids"):
            continue
        text = walk.load_text(node)[:4000]
        docs[nid] = (_tokens(node.get("title") or ""), _tokens(text), text)
    if not docs:
        return []
    # IDF over this document's nodes: "前体A" in one section outweighs "实施例" in all of them.
    n = len(docs)
    df: dict[str, int] = {}
    for title, body, _ in docs.values():
        for t in q & (title | body):
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log(1 + n / df[t]) * _weight(t) for t in df}
    wants_definition = bool(_DEFINITION_Q.search(query))
    scored: list[tuple[float, str]] = []
    for nid, (title, body, text) in docs.items():
        score = sum(2.0 * idf[t] for t in q & title) + sum(idf[t] for t in q & body)
        if score <= 0:
            continue
        if wants_definition and _DEFINITION_TEXT.search(text):
            score *= 1.5
        # Prefer specific nodes: a table / example beats the section that contains it.
        score += {"table": 0.5, "example": 0.3}.get(walk.nodes[nid].get("kind") or "", 0.0)
        scored.append((score, nid))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored


def navigate_heuristic(source_id: str, tree: dict, query: str, load_text: TextLoader, *,
                       max_hops: int = 6, max_chars: int = 48000, max_tokens: int = 16000,
                       seeds: int = 2) -> NavigationResult:
    walk = _Walk(source_id, tree, load_text, max_hops, max_chars, "heuristic", max_tokens)
    queue: list[tuple[str, str, str]] = []
    ranked = _score_nodes(walk, query)
    # Seeds from different top-level sections first: a question that spans a definition
    # (发明内容) and the examples needs a foothold in both, and refs only connect within reach.
    branches: set[str] = set()
    floor = ranked[0][0] * 0.35 if ranked else 0.0  # a weak match is not worth a hop for diversity
    for score, nid in ranked:
        if len(queue) >= seeds or score < floor:
            break
        if walk.branch(nid) not in branches:
            branches.add(walk.branch(nid))
            queue.append((nid, "select", f"query relevance {score:.2f}"))
    for score, nid in ranked:
        if len(queue) >= seeds:
            break
        if all(nid != q[0] for q in queue):
            queue.append((nid, "select", f"query relevance {score:.2f}"))
    while queue and walk.budget_left():
        nid, action, reason = queue.pop(0)
        if walk.visit(nid, action, reason) is None:
            continue
        for target, ref in walk.ref_targets(nid):
            queue.append((target, "follow_ref", f"{walk.nodes[nid].get('title')} cites {ref.get('text')}"))
        for cited_by in walk.back_refs(nid):
            queue.append((cited_by, "follow_backref",
                          f"{walk.nodes[cited_by].get('title')} cites {walk.nodes[nid].get('title')}"))
    walk.result.stopped = "budget" if queue else "exhausted"
    _extractive_answer(walk.result, query)
    return walk.result


# --------------------------------------------------------------------------- llm


_SELECT_PROMPT = """你在一篇长文档（专利 / 标准 / 论文）的目录树上为问题寻找证据。目录只含标题、页码、锚点(<table:2> 等)和交叉引用(refs)，不含正文。

问题：{query}

目录：
{outline}

选择最可能包含答案起点的 1-3 个节点。只输出 JSON：{{"select": ["节点ID", ...], "reason": "一句话说明"}}"""

_STEP_PROMPT = """问题：{query}

已读节点（按顺序）：
{read}

刚读到的内容：
{text}

当前节点引用 / 被引用的候选节点：{candidates}
完整目录：
{outline}

判断：证据是否已足够回答问题？如果不够，指出接下来要读的节点（通常沿交叉引用，例如从配方表跳到性能表、从实施例跳到前体定义）。
只输出 JSON：{{"done": true/false, "next": ["节点ID", ...], "reason": "一句话说明"}}"""


def navigate_llm(source_id: str, tree: dict, query: str, load_text: TextLoader, llm: LLMJson, *,
                 max_hops: int = 6, max_chars: int = 48000, max_tokens: int = 16000) -> NavigationResult:
    walk = _Walk(source_id, tree, load_text, max_hops, max_chars, "llm", max_tokens)
    toc = outline(tree)
    first = llm(_SELECT_PROMPT.format(query=query, outline=toc))
    if not isinstance(first, dict) or not first.get("select"):
        raise ValueError("model returned no selection")
    queue = [(str(n), "select", str(first.get("reason") or "")) for n in first["select"] if str(n) in walk.nodes]
    if not queue:
        raise ValueError("model selected unknown nodes")
    while queue and walk.budget_left():
        nid, action, reason = queue.pop(0)
        text = walk.visit(nid, action, reason)
        if text is None:
            continue
        if not walk.budget_left():
            break
        candidates = [t for t, _ in walk.ref_targets(nid)] + walk.back_refs(nid)
        read = "\n".join(f"[{e['node_id']}] {e['title']}" for e in walk.result.evidence)
        step = llm(_STEP_PROMPT.format(
            query=query, read=read, text=text[:6000],
            candidates=", ".join(f"[{c}] {walk.nodes[c].get('title')}" for c in candidates) or "(无)",
            outline=toc,
        ))
        if not isinstance(step, dict):
            raise ValueError("model step was not JSON")
        if step.get("done"):
            walk.result.stopped = "answered"
            walk.result.reasoning_trace[-1]["conclusion"] = str(step.get("reason") or "")
            if not _llm_answer(walk.result, query, llm):
                _extractive_answer(walk.result, query)
            return walk.result
        for n in step.get("next") or []:
            if str(n) in walk.nodes and str(n) not in walk.read:
                queue.append((str(n), "follow", str(step.get("reason") or "")))
    walk.result.stopped = "budget" if queue or not walk.budget_left() else "exhausted"
    if not _llm_answer(walk.result, query, llm):
        _extractive_answer(walk.result, query)
    return walk.result


def navigate(source_id: str, tree: dict, query: str, load_text: TextLoader, *, llm: LLMJson | None = None,
             max_hops: int = 6, max_chars: int = 48000, max_tokens: int = 16000) -> NavigationResult:
    """LLM navigation when a model is available, heuristic otherwise or on model failure."""
    if llm is not None:
        try:
            return navigate_llm(source_id, tree, query, load_text, llm, max_hops=max_hops,
                                max_chars=max_chars, max_tokens=max_tokens)
        except Exception as exc:  # model down, malformed JSON, unknown ids: fall back, never fail retrieval
            result = navigate_heuristic(source_id, tree, query, load_text, max_hops=max_hops,
                                        max_chars=max_chars, max_tokens=max_tokens)
            result.reasoning_trace.insert(0, {"step": 0, "action": "fallback", "reason": f"llm navigation failed: {exc}"})
            return result
    return navigate_heuristic(source_id, tree, query, load_text, max_hops=max_hops,
                              max_chars=max_chars, max_tokens=max_tokens)
