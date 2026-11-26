"""Cascade-delete a persisted KB SourceDocument (Knowledge Hub H2).

Removes chunks, KG mentions/links, wiki source_id refs, then the source row.
Never raises to callers for optional wiki/product cleanup failures.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def delete_kb_source(source_id: str) -> dict[str, Any]:
    """Hard-delete one source and dependent index rows.

    Returns a summary dict. Raises ``KeyError`` if the source does not exist.
    """
    sid = (source_id or "").strip()
    if not sid:
        raise KeyError("missing source_id")

    from ..db.chunk_store import get_chunk_store
    from ..db.source_store import get_source_store

    store = get_source_store()
    doc = store.get(sid)
    if doc is None:
        raise KeyError(sid)

    chunks_removed = 0
    mentions_removed = 0
    links_removed = 0
    wiki_pages_touched = 0

    try:
        # v16 P1-7: 删除扣减覆盖率计数器（否则单调虚增）。
        # v17-5: 先统计语言分桶，再删除（delete_for_source 只返回 total/embedded）。
        from .kb_index import _bump_kb_coverage_for_rows, _count_source_chunks, _sync_source_fts
        from ..config import get_settings

        _old_counts = _count_source_chunks(sid)
        removed = get_chunk_store().delete_for_source(sid) or {}
        # v21-fix: 恢复 chunks_removed 赋值（v19-9 误删），仅用于响应字段。
        # v19-fix: 守卫与扣减同源（都用 _old_total），避免查询口径分裂。
        chunks_removed = int(removed.get("total", 0))
        if _old_counts[0] > 0:
            _bump_kb_coverage_for_rows([], _old_counts)
        # v17-5: 清空 FTS5 镜像 —— 否则已删文档仍可被 BM25 搜到。
        try:
            _sync_source_fts(sid, [], get_settings())
        except Exception:  # noqa: BLE001
            pass
        # PageIndex tree points at the deleted chunks.
        try:
            from ..db.toc_tree_store import get_toc_tree_store

            get_toc_tree_store().delete(sid)
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        logger.exception("kb delete: chunks failed for %s: %s", sid, exc)

    try:
        from ..db.entity_store import get_entity_store

        ents = get_entity_store()
        mentions_removed = int(ents.delete_mentions_for_source(sid) or 0)
        links_removed = int(ents.delete_links_for_source(sid) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb delete: kg cleanup skipped: %s", exc)

    try:
        wiki_pages_touched = _strip_wiki_source_ids(sid)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb delete: wiki cleanup skipped: %s", exc)

    try:
        _strip_product_source_ids(sid)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb delete: product cleanup skipped: %s", exc)

    store.delete(sid)

    # 融合升级批 6：统一失效协议（默认关）。删源后复核四层：chunk/KG/
    # 树幂等再删（0 行也是可断言的行为）、Wiki 页内该源的 ### Source
    # 证据块移除 + stale 标记、log.md 记一行。任一层失败只记 notice，
    # 不影响删除结果（失效函数本身永不抛错）。注意：方案 §3 批 6 写
    # "删源时不碰 kb_mentions/kb_entity_links"是旧结论——当前代码
    # 上方已删提及与边，本函数只是复核 + 补 Wiki 块移除与 log 两项缺口。
    try:
        from ..config import get_settings as _get_settings

        if _get_settings().kb_invalidation_enabled:
            from .kb_invalidation import invalidate_source

            _inv = invalidate_source(sid, reason="deleted")
            logger.info("kb delete: invalidation for %s: %s", sid, _inv)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb delete: invalidation skipped: %s", exc)

    return {
        "ok": True,
        "source_id": sid,
        "chunks_removed": chunks_removed,
        "mentions_removed": mentions_removed,
        "links_removed": links_removed,
        "wiki_pages_touched": wiki_pages_touched,
    }


def _strip_wiki_source_ids(source_id: str) -> int:
    from ..db.wiki_store import get_wiki_store

    wiki = get_wiki_store()
    touched = 0
    # B10（2026-11-26 全面审查）：此前单次 list_pages(limit=500)，超
    # 500 页后删源漏剔后半部分页面的 source_ids。改为分页循环直至扫完。
    # 先只读收集全部 (path, source_ids) 再逐页处理：upsert 会改
    # updated_at（list_pages 按 updated_at desc 排序），边读边写下
    # offset 分页会跳页；两趟分离后读侧稳定。
    _PAGE = 200
    _targets: list[tuple[str, list[str]]] = []
    _offset = 0
    while True:
        _rows = wiki.list_pages(limit=_PAGE, offset=_offset)
        if not _rows:
            break
        for _row in _rows:
            _targets.append((_row.path, list(_row.source_ids or [])))
        _offset += len(_rows)
        if len(_rows) < _PAGE:
            break
    for path, ids in _targets:
        if source_id not in ids:
            continue
        row = wiki.get_by_path(path)
        if row is None:
            continue
        new_ids = [x for x in ids if x != source_id]
        md = wiki.read_markdown(row.path) or ""
        if md.startswith("---"):
            end = md.find("\n---", 3)
            if end > 0:
                header = md[3:end]
                rest = md[end + 4 :]
                lines = []
                replaced = False
                src_list = ", ".join(f'"{s}"' for s in new_ids)
                for ln in header.splitlines():
                    if ln.strip().startswith("source_ids:"):
                        lines.append(f"source_ids: [{src_list}]")
                        replaced = True
                    else:
                        lines.append(ln)
                if not replaced:
                    lines.append(f"source_ids: [{src_list}]")
                flags = list(row.flags or [])
                if not new_ids and "stale" not in flags:
                    flags.append("stale")
                    flag_list = ", ".join(f'"{f}"' for f in flags)
                    lines2 = []
                    flag_done = False
                    for ln in lines:
                        if ln.strip().startswith("flags:"):
                            lines2.append(f"flags: [{flag_list}]")
                            flag_done = True
                        else:
                            lines2.append(ln)
                    if not flag_done:
                        lines2.append(f"flags: [{flag_list}]")
                    lines = lines2
                new_md = "---\n" + "\n".join(lines) + "\n---" + rest
                wiki.upsert_page(
                    path=row.path,
                    kind=row.kind,
                    title=row.title or "",
                    norm_key=row.norm_key or "",
                    entity_id=row.entity_id,
                    markdown=new_md,
                    source_ids=new_ids,
                    flags=flags,
                    replace_source_ids=True,
                )
                touched += 1
                continue
        # Fallback: update DB row only
        wiki.upsert_page(
            path=row.path,
            kind=row.kind,
            title=row.title or "",
            norm_key=row.norm_key or "",
            entity_id=row.entity_id,
            markdown=md or f"# {row.title}\n",
            source_ids=new_ids,
            flags=list(row.flags or []) + (["stale"] if not new_ids else []),
            replace_source_ids=True,
        )
        touched += 1
    return touched


def _strip_product_source_ids(source_id: str) -> None:
    from ..db.product_store import get_product_store
    from ..db.models import KBProduct
    from ..db.session_utils import commit_session
    from ..db.database import default_session_factory

    store = get_product_store()
    for prod in store.search("", limit=500):
        ids = list(prod.source_ids or [])
        if source_id not in ids:
            continue
        new_ids = [x for x in ids if x != source_id]
        with commit_session(default_session_factory()) as session:
            row = session.get(KBProduct, prod.id)
            if row is not None:
                row.source_ids = new_ids
