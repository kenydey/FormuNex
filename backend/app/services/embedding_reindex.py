"""v29: 通用 embedding 模型重建索引服务。

从 scripts/migrate_to_bge_m3.py 泛化而来，支持任意 catalog 模型的切换。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# C2（2026-11-26 全面审查）：重嵌前 chunk 文本截断上限。超长 chunk 只取
# 前 N 字符嵌向量（向量内容本身不变），截断事实必须可观测：逐 chunk 记
# 入 chunk meta（`embedding_truncated` / `embedding_original_chars`，
# 与 B3 的 `meta.page_no_source="approx"` 同口径），整轮汇总进返回 dict
# 并记一条 warning 日志；未超长行不写任何标记。
EMBEDDING_MAX_CHARS = 8000

# 模型维度映射（API 层同样引用这一份）
MODEL_DIMS = {
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "BAAI/bge-small-zh-v1.5": 512,
    "BAAI/bge-m3": 1024,
    "Qwen/Qwen3-Embedding-0.6B": 1024,
    "moka-ai/m3e-base": 768,
}


def _configured_db_path() -> Path:
    """The SQLite file the app is actually using (``FORMUNEX_DB_URL`` / ``Settings.db_url``)."""
    import os

    from ..config import get_settings

    url = os.environ.get("FORMUNEX_DB_URL") or get_settings().db_url
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise RuntimeError(f"嵌入模型重建只支持 SQLite 数据库，当前: {url.split(':', 1)[0]}")
    return Path(url[len(prefix):]).resolve()


def _finalize_reindex(
    *,
    total: int,
    migrated: int,
    elapsed_s: float,
    backup: str,
    truncated_chunks: int = 0,
    truncated_chars: int = 0,
    max_original_chars: int = 0,
) -> dict:
    """B6（2026-11-26 全面审查）：重嵌收尾的失效信号。

    重嵌用原始 sqlite 直连改 ``document_chunks``，绕过了 chunk 写入路径，
    此前不推进任何失效信号：同模型重嵌后 kb_ann 指纹（count /
    max_created_at）不变、继续服务旧向量桶；L2 语料矩阵缓存键与向量
    内容无关、继续用旧空间算余弦。收尾依次做：

    ① ``chunk_store.bump_generation()`` —— L2 缓存键失效；
    ② ``kb_ann.invalidate()`` —— 内存索引桶丢弃，下次重建；
    ③ 清空 ``kb_dedup`` 的 L2 矩阵缓存；
    ④ 返回 dict 带 ``ann_invalidated: true``。

    每步 fail-open（记 warning，不推翻已完成的重嵌），返回 dict 如实
    记录每步是否成功。由 ``reindex_for_model`` 收尾调用；单测直接调
    本函数（全量重嵌本身须 sentence_transformers 环境）。
    """
    from ..db.chunk_store import get_chunk_store
    from . import kb_ann, kb_dedup

    generation_bumped = False
    try:
        get_chunk_store().bump_generation()
        generation_bumped = True
    except Exception as exc:  # noqa: BLE001 - fail-open, 见 docstring
        logger.warning("重建收尾: bump_generation 失败: %s", exc)
    ann_invalidated = False
    try:
        kb_ann.invalidate()
        ann_invalidated = True
    except Exception as exc:  # noqa: BLE001 - fail-open, 见 docstring
        logger.warning("重建收尾: kb_ann.invalidate() 失败: %s", exc)
    l2_cleared = False
    try:
        kb_dedup.clear_l2_cache()
        l2_cleared = True
    except Exception as exc:  # noqa: BLE001 - fail-open, 见 docstring
        logger.warning("重建收尾: 清空 L2 缓存失败: %s", exc)
    logger.info(
        "重建收尾失效信号: generation_bumped=%s ann_invalidated=%s l2_cleared=%s",
        generation_bumped, ann_invalidated, l2_cleared,
    )
    if truncated_chunks:
        logger.warning(
            "重建截断: %d 个 chunk 文本超 %d 字符，仅前 %d 字符参与嵌向量"
            "（共丢弃 %d 字符，最长原文 %d 字符）；截断行已在 chunk meta 记"
            " embedding_truncated，详见返回 dict",
            truncated_chunks, EMBEDDING_MAX_CHARS, EMBEDDING_MAX_CHARS,
            truncated_chars, max_original_chars,
        )
    return {
        "total": total,
        "migrated": migrated,
        "elapsed_s": round(elapsed_s, 1),
        "backup": backup,
        "ann_invalidated": ann_invalidated,
        "generation_bumped": generation_bumped,
        "l2_cache_cleared": l2_cleared,
        # C2：本轮因超长被截断后嵌向量的 chunk 数 / 共丢弃字符数 /
        # 最长原文字符数；无截断时三项均为 0（不标记）。
        "truncated_chunks": truncated_chunks,
        "truncated_chars": truncated_chars,
        "max_original_chars": max_original_chars,
    }


def reindex_for_model(
    model_id: str,
    db_path: str | Path | None = None,
    batch: int = 32,
    progress_cb: Callable[[float], None] | None = None,
) -> dict:
    """为指定模型重建全库索引。

    流程：备份 → 加载模型 → 逐批重嵌 → 更新 embedding_model → 校验。
    返回统计 dict。超长 chunk 文本截断至 ``EMBEDDING_MAX_CHARS`` 字符后
    嵌向量（向量内容本身不变）；截断事实逐 chunk 记入 chunk meta
    （``embedding_truncated`` / ``embedding_original_chars``，C2），整轮
    汇总为返回 dict 的 ``truncated_chunks`` / ``truncated_chars`` /
    ``max_original_chars``，无截断时三项均为 0。
    """
    db_path = Path(db_path) if db_path is not None else _configured_db_path()
    if not db_path.exists():
        raise FileNotFoundError(f"DB 不存在: {db_path}")

    target_dim = MODEL_DIMS.get(model_id)
    if not target_dim:
        raise ValueError(f"未知模型维度: {model_id}")

    # 1. 备份
    bak = db_path.with_suffix(f".db.bak.{int(time.time())}")
    # sqlite3's online-backup API: a plain file copy of a live WAL-mode database can miss pages still in the -wal file.
    src = sqlite3.connect(str(db_path), timeout=60)
    dst = sqlite3.connect(str(bak))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    logger.info("已备份 DB: %s", bak)
    # C P2-3: 备份 retention —— 只保留最近 3 个，避免磁盘堆积。
    try:
        _baks = sorted(
            db_path.parent.glob(db_path.name + ".bak.*"),
            key=lambda p: p.stat().st_mtime,
        )
        for _old in _baks[:-3]:
            _old.unlink()
            logger.info("清理旧备份: %s", _old)
    except Exception:
        pass

    # 2. 加载模型：先只用本地缓存，缺失再联网（不改进程级 HF_HUB_OFFLINE）
    print(f"加载模型 {model_id} ...")
    from sentence_transformers import SentenceTransformer

    try:
        model = SentenceTransformer(model_id, local_files_only=True)
    except Exception:
        model = SentenceTransformer(model_id)
    dim = model.get_sentence_embedding_dimension()
    if dim != target_dim:
        # 在动任何数据之前拒绝：否则整库会写入维度不符的向量，直到最后的抽查才发现。
        raise RuntimeError(f"模型维度 {dim} 与预期 {target_dim} 不符: {model_id}")

    # 3. 逐批重嵌
    from .kb_ann import vector_to_blob

    con = sqlite3.connect(str(db_path), timeout=60)  # the app keeps writing while we re-embed: wait, don't fail
    try:
        total = con.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
        done = 0
        last_id = ""
        t0 = time.time()
        # C2：meta 列是否存在决定能否写逐 chunk 截断标记（最小 schema 的
        # 测试库可能无 meta 列；汇总计数与日志不受影响）。
        has_meta = any(
            row[1] == "meta"
            for row in con.execute("PRAGMA table_info(document_chunks)").fetchall()
        )
        truncated_chunks = 0
        truncated_chars = 0
        max_original_chars = 0
        # C P2-4: 分页读取 —— 全表 fetchall 在 10 万 chunk 级内存爆炸。
        # 按 id 做 keyset 分页：不持有未读完的 SELECT 游标，逐批 UPDATE/COMMIT 不会打断读取。
        while True:
            batch_rows = con.execute(
                "SELECT id, text FROM document_chunks WHERE id > ? ORDER BY id LIMIT ?",
                (last_id, batch),
            ).fetchall()
            if not batch_rows:
                break
            # C2：超长文本只取前 EMBEDDING_MAX_CHARS 字符嵌向量，并记截断。
            texts: list[str] = []
            truncated_rows: list[tuple[str, int]] = []
            for cid, raw_text in batch_rows:
                text = raw_text or ""
                if len(text) > EMBEDDING_MAX_CHARS:
                    truncated_rows.append((cid, len(text)))
                    truncated_chunks += 1
                    truncated_chars += len(text) - EMBEDDING_MAX_CHARS
                    max_original_chars = max(max_original_chars, len(text))
                texts.append(text[:EMBEDDING_MAX_CHARS])
            vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            con.executemany(
                """UPDATE document_chunks
                   SET embedding_blob=?, embedding_model=?, embedding=NULL
                   WHERE id=?""",
                [
                    (vector_to_blob(vec.astype("float32")), model_id, cid)
                    for (cid, _), vec in zip(batch_rows, vecs)
                ],
            )
            if truncated_rows and has_meta:
                # 逐 chunk 截断标记写进 meta（与 B3 的 meta.page_no_source
                # 同口径）；只动被截断的行，未截断行不写任何标记。meta
                # 解析失败时跳过该行（汇总计数与日志仍保留事实），不
                # 推翻已完成的重嵌。
                for cid, orig_len in truncated_rows:
                    try:
                        raw_meta = con.execute(
                            "SELECT meta FROM document_chunks WHERE id=?", (cid,)
                        ).fetchone()[0]
                        meta = json.loads(raw_meta) if raw_meta else {}
                        if not isinstance(meta, dict):
                            continue
                        meta = {
                            **meta,
                            "embedding_truncated": True,
                            "embedding_original_chars": orig_len,
                        }
                        con.execute(
                            "UPDATE document_chunks SET meta=? WHERE id=?",
                            (json.dumps(meta, ensure_ascii=False), cid),
                        )
                    except Exception as exc:  # noqa: BLE001 - fail-open，见上
                        logger.warning("重建截断: chunk %s 的 meta 标记写入失败: %s", cid, exc)
            con.commit()
            last_id = batch_rows[-1][0]
            done += len(batch_rows)
            if progress_cb:
                progress_cb(done / total if total else 1.0)
            if done % 320 == 0:
                logger.info("重建进度 %d/%d", done, total)

        # 4. 校验
        migrated = con.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE embedding_model=?", (model_id,)
        ).fetchone()[0]
        null_blob = con.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE embedding_blob IS NULL"
        ).fetchone()[0]
        row = con.execute("SELECT embedding_blob FROM document_chunks LIMIT 1").fetchone()
        if row and row[0]:
            n_dims = len(row[0]) // 4  # float32 little-endian, see vector_to_blob
            if n_dims != target_dim:
                raise RuntimeError(f"维度校验失败: {n_dims} != {target_dim}")
    finally:
        con.close()

    if null_blob != 0:
        raise RuntimeError(f"仍有 {null_blob} 个 NULL blob")
    if migrated != total:
        raise RuntimeError(f"迁移数量不符: {migrated} != {total}")

    el = time.time() - t0
    logger.info("重建完成: %d chunks, %.0fs, 备份 %s", total, el, bak)
    # B6：收尾推进失效信号（bump generation + kb_ann.invalidate() +
    # 清空 L2 缓存），否则同模型重嵌后检索/去重继续服务旧向量。
    # C2：截断汇总随返回 dict 带出（无截断时三项均为 0）。
    return _finalize_reindex(
        total=total,
        migrated=migrated,
        elapsed_s=el,
        backup=str(bak),
        truncated_chunks=truncated_chunks,
        truncated_chars=truncated_chars,
        max_original_chars=max_original_chars,
    )
