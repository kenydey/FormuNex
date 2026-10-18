"""PageIndex ToC-tree node summaries, generated in the background (plan §5.1.1: Celery low-priority).

The tree builder (``tree_builder.py``) is synchronous and leaves every node's
``summary`` empty; ``DocumentTocTreeRow.summaries_status`` tracks the state
machine::

    none -> pending -> done | partial

Summaries are generated lazily, not at ingest — the full tree walk (LLM over
every node) costs too much for every upload. Generation is scheduled only for
documents that were actually visited by Stage 2, or that a user opens at
≥20 pages (the Stage-2 page threshold), plus the explicit trigger
(``POST /kb/sources/{id}/toc-tree/summarize``). While the summary status is
"pending" or "generating", the navigator still works — it reads node text directly.
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


def maybe_schedule_summaries(source_id: str, *, visited_by_stage2: bool = False) -> bool:
    """Schedule node summaries for one document, if it qualifies (plan §5.1.1).

    Triggers: Stage-2-visited documents, or documents with ≥20 pages (the
    Stage-2 page threshold). Not at ingest — ingest only builds the tree, and
    a full-library sweep would pay the LLM cost for documents nobody reads.
    Only fires when the current status is "none" (idempotent: a document is
    scheduled at most once). A broker failure only logs: the status stays
    "none" and the next qualifying visit retries.
    """
    from ...config import get_settings
    from ...db.toc_tree_store import get_toc_tree_store

    if not get_settings().pageindex_enabled:
        return False
    store = get_toc_tree_store()
    row = store.get(source_id)
    if row is None:
        return False
    if (row.summaries_status or STATUS_NONE) != STATUS_NONE:
        return False
    if not visited_by_stage2 and (row.page_count or 0) < 20:
        return False
    store.set_summaries_status(source_id, STATUS_PENDING)
    try:
        # Same task the explicit endpoint uses (formunex.toc_summaries).
        # No dedicated "low" queue exists in celery_app.py (only default /
        # ingest / optimize / render / regulatory / molscribe), so the task
        # stays on default — inventing a queue no worker consumes would
        # silently drop it. Async execution is what keeps it off the request.
        from ...worker.tasks import run_toc_summaries_task
        from ...api import _dispatch

        _dispatch.submit(run_toc_summaries_task, {"source_id": source_id}, kind="toc_summaries")
    except Exception as exc:  # broker down (503) etc: keep "pending", retry on next visit
        log.warning("pageindex: summary dispatch failed for %s: %s", source_id, exc)
    return True


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
