"""Unified invalidation protocol (fusion batch 6).

One source change, four layers in sync. ``invalidate_source`` is the
single propagation function for a content change or a deletion of a
``SourceDocument``; the propagation key is the source content hash
(``SourceDocument.content_hash`` at create time, or the hash the caller
computed for the new text — the row hash itself is provenance
write-protected and goes stale on ``index_source`` re-index, so the
caller threads the fresh hash through instead of rewriting the row).

Layers, in order (each fail-open: a layer failure is recorded as a
notice and never raises, never blocks ingest/delete or Q&A):

1. chunk — content change: verify the caller's chunk replace landed
   (chunk count + chunk-text hash); deletion: delete the source's
   chunks + FTS mirror (idempotent: 0 rows is an assertable outcome).
2. KG — content change: ``link_source`` (delete-then-rebuild mentions
   and extracted links); deletion: ``delete_mentions_for_source`` +
   ``delete_links_for_source`` (idempotent, catalog_alias links kept).
3. Wiki — content change: synchronous ``compile_source`` with the fresh
   hash (drift refresh, batch 5-3), then sweep pages that still list the
   source but were not refreshed by this compile — the source no longer
   evidences them, so its ``### Source`` block is removed and the source
   is dropped from ``source_ids`` (``stale`` flag when the list empties);
   deletion: the same sweep for every page listing the source.
4. tree — content change: ``build_tree_on_ingest`` (rebuild); deletion:
   delete the tree row. Read side keeps the batch 3-1 version check as
   the second line of defense; this function is the write-side pass.

``log.md`` records one line per run (``WikiStore.append_invalidation_log``);
a log-write failure is recorded but does not fail the run.

The function is for offline/background use (ingest/delete paths), never
on the Q&A path, so it adds zero query latency. All behavior is gated
by ``kb_invalidation_enabled`` (default off); when off the function is a
no-op and the pre-existing scattered hooks run instead. 0 LLM calls.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from ..config import get_settings

logger = logging.getLogger(__name__)

# Layers whose failure fails the run. The log.md write is record-keeping,
# not a data layer: its failure is recorded + noticed but never fails
# the invalidation itself.
_GATED_LAYERS = ("chunk", "kg", "wiki", "tree")


def source_content_hash(text: str) -> str:
    """Canonical propagation key: sha256 of the source's full text.

    Same formula the ingest paths use when they create the
    ``SourceDocument`` row (``ingest_tx`` / ``source_store.create``).
    """
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def chunk_text_hash(chunks: Any) -> str:
    """Hash of the current chunk texts (compile_source fallback formula)."""
    return hashlib.sha256(
        "\n".join((getattr(c, "text", "") or "") for c in (chunks or [])).encode(
            "utf-8"
        )
    ).hexdigest()


def invalidate_source(
    source_id: str,
    *,
    reason: str = "content_changed",
    old_content_hash: str | None = None,
    new_content_hash: str | None = None,
    prior_chunk_count: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Invalidate one source across chunk / KG / Wiki / tree. Never raises.

    ``reason`` is ``"content_changed"`` or ``"deleted"``. For a content
    change the caller must already have replaced the chunks (``index_source``
    does that before calling); this function verifies the replace and
    drives the other three layers. ``old_content_hash`` / ``new_content_hash``
    default to the stored row hash / the hash of the current chunk texts.
    When the key is unchanged (same hash, prior chunks existed) and not
    ``force``, the run short-circuits as ``"unchanged"`` — the derived
    state already matches the content.
    """
    sid = (source_id or "").strip()
    settings = get_settings()
    result: dict[str, Any] = {
        "ok": False,
        "reason": reason,
        "source_id": sid,
        "layers": {},
        "notices": [],
    }
    if not sid:
        result["reason"] = "missing_source_id"
        return result
    if not bool(getattr(settings, "kb_invalidation_enabled", False)):
        result["reason"] = "disabled"
        return result
    if reason not in ("content_changed", "deleted"):
        result["reason"] = "unknown_reason"
        return result

    # Resolve the propagation key. The stored row hash is only written at
    # create/revive time; on a re-index it is the *old* content's hash,
    # which is exactly what "old" must be. The new hash comes from the
    # caller (sha256 of the new full text) or, failing that, from the
    # current chunk texts.
    stored_hash = ""
    try:
        from ..db.source_store import get_source_store

        doc = get_source_store().get(sid)
        stored_hash = (getattr(doc, "content_hash", "") or "").strip() if doc else ""
    except Exception as exc:  # noqa: BLE001 — hash resolution must not break the run
        logger.debug("kb invalidation: stored hash unreadable for %s: %s", sid, exc)
    old_hash = (old_content_hash or "").strip() or stored_hash
    if reason == "deleted":
        new_hash = ""
    else:
        new_hash = (new_content_hash or "").strip()
        if not new_hash:
            try:
                from ..db.chunk_store import get_chunk_store

                new_hash = chunk_text_hash(get_chunk_store().get_by_source(sid))
            except Exception as exc:  # noqa: BLE001
                logger.debug("kb invalidation: chunk hash unreadable for %s: %s", sid, exc)
    result["old_content_hash"] = old_hash
    result["new_content_hash"] = new_hash

    if (
        reason == "content_changed"
        and not force
        and old_hash
        and new_hash
        and old_hash == new_hash
        and (prior_chunk_count or 0) > 0
    ):
        result["ok"] = True
        result["reason"] = "unchanged"
        return result

    layers: dict[str, Any] = result["layers"]
    notices: list[str] = result["notices"]

    def _fail(layer: str, exc: Exception) -> None:
        layers[layer] = {"status": "failed", "error": str(exc)}
        msg = f"kb invalidation: {layer} failed for {sid} ({reason}): {exc}"
        notices.append(msg)
        logger.warning(msg)

    for layer, fn in (
        ("chunk", _layer_chunk),
        ("kg", _layer_kg),
        ("wiki", _layer_wiki),
        ("tree", _layer_tree),
    ):
        try:
            layers[layer] = fn(sid, reason, new_hash)
        except Exception as exc:  # noqa: BLE001 — per-layer fail-open
            _fail(layer, exc)

    # log.md change history (批 5-2): one line per run; never gates the run.
    try:
        from ..db.wiki_store import get_wiki_store

        detail = (
            f"layers="
            + ",".join(f"{k}:{v.get('status', '?')}" for k, v in layers.items())
        )
        logged = get_wiki_store().append_invalidation_log(sid, reason, detail)
        layers["log"] = {"status": "ok" if logged else "failed"}
        if not logged:
            notices.append(f"kb invalidation: log.md write failed for {sid}")
    except Exception as exc:  # noqa: BLE001 — log must never break invalidation
        layers["log"] = {"status": "failed", "error": str(exc)}
        notices.append(f"kb invalidation: log.md write failed for {sid}: {exc}")

    result["ok"] = all(
        layers.get(layer, {}).get("status") in ("ok", "skipped")
        for layer in _GATED_LAYERS
    )
    return result


def _layer_chunk(source_id: str, reason: str, new_hash: str) -> dict[str, Any]:
    from ..db.chunk_store import get_chunk_store

    store = get_chunk_store()
    if reason == "deleted":
        removed = store.delete_for_source(source_id) or {}
        try:
            from ..config import get_settings as _settings
            from .kb_index import _sync_source_fts

            _sync_source_fts(source_id, [], _settings())
        except Exception:  # noqa: BLE001 — FTS mirror is best-effort here
            pass
        return {
            "status": "ok",
            "chunks_removed": int(removed.get("total", 0)),
            "embedded_removed": int(removed.get("embedded", 0)),
        }
    chunks = store.get_by_source(source_id)
    return {
        "status": "ok" if chunks else "failed",
        "chunk_count": len(chunks),
        "chunk_text_hash": chunk_text_hash(chunks),
        **({} if chunks else {"error": "no chunks after replace"}),
    }


def _layer_kg(source_id: str, reason: str, new_hash: str) -> dict[str, Any]:
    from ..db.entity_store import get_entity_store

    settings = get_settings()
    ents = get_entity_store()
    if reason == "deleted":
        mentions = int(ents.delete_mentions_for_source(source_id) or 0)
        links = int(ents.delete_links_for_source(source_id) or 0)
        return {"status": "ok", "mentions_removed": mentions, "links_removed": links}
    if not (
        settings.kg_enabled
        and settings.kg_link_on_ingest
        and (settings.kg_entities_on_ingest or settings.kg_relations_on_ingest)
    ):
        return {"status": "skipped", "reason": "kg link off"}
    from .kg.entity_linker import link_source

    report = link_source(source_id, settings=settings)
    return {
        "status": "ok",
        "entities_upserted": int(getattr(report, "entities_upserted", 0) or 0),
        "mentions_upserted": int(getattr(report, "mentions_upserted", 0) or 0),
        "relations_upserted": int(getattr(report, "relations_upserted", 0) or 0),
    }


def _layer_wiki(source_id: str, reason: str, new_hash: str) -> dict[str, Any]:
    from ..db.wiki_store import get_wiki_store
    from .wiki.schema import (
        extract_evidence_blocks,
        find_source_block,
        remove_source_block,
    )

    settings = get_settings()
    if not settings.wiki_enabled:
        return {"status": "skipped", "reason": "wiki_disabled"}
    out: dict[str, Any] = {"status": "ok"}
    touched: set[str] = set()
    if reason == "content_changed":
        from .wiki.compile import compile_source

        res = compile_source(source_id, source_hash=new_hash or None)
        if not res.get("ok"):
            return {
                "status": "failed",
                "error": f"compile failed: {res.get('reason')}",
                "pages_compiled": [],
            }
        touched = set(res.get("pages") or [])
        out["pages_compiled"] = sorted(touched)
    # Sweep: pages that still list this source but were not refreshed by
    # this compile no longer have evidence for it — remove the source's
    # ### Source block, drop it from source_ids, flag stale when empty.
    # On delete every page listing the source is swept (no compile runs:
    # the row is gone, compile_source would return source_not_found).
    # Note: the delete path strips source_ids first (_strip_wiki_source_ids),
    # so on delete candidacy is decided by the block text, not source_ids.
    wiki = get_wiki_store()
    swept = 0
    blocks_removed = 0
    for row in wiki.list_pages(limit=500):
        ids = list(row.source_ids or [])
        md: str | None = None
        if source_id not in ids:
            if reason != "deleted":
                continue
            md = wiki.read_markdown(row.path) or ""
            if find_source_block(extract_evidence_blocks(md), source_id) < 0:
                continue
        if row.path in touched:
            continue
        if md is None:
            md = wiki.read_markdown(row.path) or ""
        new_md, removed = remove_source_block(md, source_id)
        new_ids = [x for x in (row.source_ids or []) if x != source_id]
        flags = list(row.flags or [])
        if not new_ids and "stale" not in flags:
            flags.append("stale")
        wiki.upsert_page(
            path=row.path,
            kind=row.kind,
            title=row.title or "",
            norm_key=row.norm_key or "",
            entity_id=row.entity_id,
            markdown=new_md if new_md.strip() else md,
            source_ids=new_ids,
            flags=flags,
            replace_source_ids=True,
        )
        swept += 1
        blocks_removed += int(removed)
    out["pages_swept"] = swept
    out["blocks_removed"] = blocks_removed
    return out


def _layer_tree(source_id: str, reason: str, new_hash: str) -> dict[str, Any]:
    from ..db.toc_tree_store import get_toc_tree_store

    settings = get_settings()
    trees = get_toc_tree_store()
    if reason == "deleted":
        removed = int(trees.delete(source_id) or 0)
        gone = trees.get(source_id) is None
        return {
            "status": "ok" if gone else "failed",
            "trees_removed": removed,
            **({} if gone else {"error": "tree still present after delete"}),
        }
    if not (settings.pageindex_enabled and settings.pageindex_build_on_ingest):
        return {"status": "skipped", "reason": "pageindex build off"}
    from .pageindex.two_stage import build_tree_on_ingest

    build_tree_on_ingest(source_id)
    row = trees.get(source_id)
    if row is None:
        return {"status": "failed", "error": "no tree after rebuild"}
    return {"status": "ok", "page_count": int(row.page_count or 0)}
