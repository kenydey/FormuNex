"""PageIndex ToC-tree node summaries, generated in the background.

The tree builder (``tree_builder.py``) is synchronous and leaves every node's
``summary`` empty; ``DocumentTocTreeRow.summaries_status`` tracks the state
machine::

    none -> pending -> done | partial

Summaries are **only** generated for a document the user explicitly triggers
(``POST /kb/sources/{id}/toc-tree/summarize``) — never a full-library sweep,
so LLM cost stays bounded by what the user asked for. A node whose LLM call
fails keeps ``summary=None`` and the document lands in ``partial`` instead of
failing the whole job; re-triggering retries just the missing nodes.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger(__name__)

# Bound per-node input so one giant section cannot blow up the prompt budget.
MAX_NODE_TEXT_CHARS = 4000
# Summaries are navigation aids, not prose: one line, hard cap.
MAX_SUMMARY_CHARS = 200

# Status machine values (must match DocumentTocTreeRow.summaries_status comment).
STATUS_NONE = "none"
STATUS_PENDING = "pending"
STATUS_PARTIAL = "partial"
STATUS_DONE = "done"

# Node kinds worth summarising. The root node spans the whole document (too
# long to summarise usefully) and is skipped.
SUMMARISABLE_KINDS = {"heading", "section", "example", "table"}

Summarizer = Callable[[str, str], str | None]


def _default_summarizer(title: str, text: str) -> str | None:
    """LLM summary of one node: one line, <= 200 chars. None when unavailable."""
    from ..llm import complete_json

    prompt = (
        "用一句话概括下面这节文档的核心内容，不超过 200 字，只输出 JSON："
        '{"summary": "..."}。不要添加任何解释。\n\n'
        f"标题：{title}\n正文：{text[:MAX_NODE_TEXT_CHARS]}"
    )
    try:
        out = complete_json(prompt)
    except Exception as exc:  # LLM misconfigured / provider down — fail open
        log.warning("pageindex: summary LLM call failed for %r: %s", title, exc)
        return None
    if not isinstance(out, dict):
        return None
    summary = out.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    return summary.strip()[:MAX_SUMMARY_CHARS]


def _walk_nodes(node: dict[str, Any]):
    yield node
    for child in node.get("children") or []:
        yield from _walk_nodes(child)


def _count_summaries(tree: dict[str, Any]) -> tuple[int, int]:
    """(summarised, summarisable) node counts, root excluded."""
    done = total = 0
    for node in _walk_nodes(tree):
        if node.get("kind") == "root":
            continue
        if node.get("kind") not in SUMMARISABLE_KINDS:
            continue
        total += 1
        if node.get("summary"):
            done += 1
    return done, total


def generate_summaries(source_id: str, *, summarizer: Summarizer | None = None) -> dict[str, Any]:
    """Fill missing node summaries for one document's stored tree.

    Returns ``{source_id, summaries_status, summarised, total}``. Never raises
    for per-node LLM failures (they land the document in ``partial``); raises
    only when the tree row itself is missing.
    """
    from ...db.toc_tree_store import get_toc_tree_store
    from .two_stage import _NodeText

    store = get_toc_tree_store()
    row = store.get(source_id)
    if row is None:
        raise KeyError(f"no ToC tree for source {source_id}")
    tree = dict(row.tree or {})
    node_text = _NodeText(source_id)
    summarizer = summarizer or _default_summarizer

    summarised_before, total = _count_summaries(tree)
    newly = 0
    for node in _walk_nodes(tree):
        if node.get("kind") == "root" or node.get("kind") not in SUMMARISABLE_KINDS:
            continue
        if node.get("summary"):
            continue
        text = node_text(node)
        if not text.strip():
            continue
        try:
            summary = summarizer(node.get("title") or "", text)
        except Exception as exc:  # a broken stub must not kill the whole job
            log.warning("pageindex: summarizer raised for node %r: %s", node.get("node_id"), exc)
            summary = None
        if summary:
            node["summary"] = summary
            newly += 1

    summarised, total = _count_summaries(tree)
    if total == 0 or summarised == total:
        status = STATUS_DONE
    elif summarised > summarised_before:
        status = STATUS_PARTIAL
    else:
        # Nothing new summarised (LLM down / all failed): stay partial, not
        # pending, so a re-trigger is a deliberate retry rather than a loop.
        status = STATUS_PARTIAL
    store.update_tree(source_id, tree)
    store.set_summaries_status(source_id, status)
    return {"source_id": source_id, "summaries_status": status,
            "summarised": summarised, "total": total, "newly": newly}
