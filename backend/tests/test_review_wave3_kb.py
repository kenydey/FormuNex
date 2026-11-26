"""审查修复第三波 · 知识库域（C2 / C4）。

C2（报告 §4）：重建索引时 chunk 文本截断至 8000 字符后嵌向量，
此前无任何标记。本文件断言：超长 chunk 的截断事实逐 chunk 记入
chunk meta（``embedding_truncated`` / ``embedding_original_chars``），
整轮汇总进 ``reindex_for_model`` 返回 dict 并记 warning 日志；
未超长行不写任何标记；向量内容本身仍是前 8000 字符的嵌向量。

C4（报告 §4）：迁移测试 ``test_migration_0039_upgrade_downgrade``
偶发隔离失败 —— 见本文件末尾的隔离测试（复现组合与根因定位
结论写在测试 docstring 与汇报里）。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
import types

import numpy as np
import pytest

from app.services import embedding_reindex as er

MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # 384 dims in MODEL_DIMS


class _FakeModel:
    def __init__(self, dim: int):
        self.dim = dim
        self.seen_texts: list[str] = []

    def get_sentence_embedding_dimension(self) -> int:
        return self.dim

    def encode(self, texts, **_kw):
        self.seen_texts.extend(texts)
        return np.ones((len(texts), self.dim), dtype="float32")


def _install_fake(monkeypatch, dim: int = 384):
    model = _FakeModel(dim)

    def factory(name, **kw):
        return model

    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = factory
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)
    return model


def _db(tmp_path, rows: list[tuple[str, str, str | None]], *, with_meta: bool = True):
    """rows: (id, text, meta_json_or_None)."""
    path = tmp_path / "formunex.db"
    con = sqlite3.connect(path)
    meta_col = ", meta TEXT" if with_meta else ""
    con.execute(
        "CREATE TABLE document_chunks (id TEXT PRIMARY KEY, text TEXT, embedding TEXT,"
        f" embedding_blob BLOB, embedding_model TEXT{meta_col})"
    )
    if with_meta:
        con.executemany(
            "INSERT INTO document_chunks (id, text, embedding_model, meta) VALUES (?, ?, 'old', ?)",
            rows,
        )
    else:
        con.executemany(
            "INSERT INTO document_chunks (id, text, embedding_model) VALUES (?, ?, 'old')",
            [(cid, text) for cid, text, _ in rows],
        )
    con.commit()
    con.close()
    return path


def _meta_of(path, cid: str):
    con = sqlite3.connect(path)
    try:
        raw = con.execute(
            "SELECT meta FROM document_chunks WHERE id=?", (cid,)
        ).fetchone()[0]
    finally:
        con.close()
    return json.loads(raw) if raw else None


# ── C2：截断标记 ─────────────────────────────────────────────────────────


def test_c2_long_chunk_truncated_and_marked(tmp_path, monkeypatch, caplog):
    model = _install_fake(monkeypatch)
    long_text = "x" * 8001
    exact_text = "y" * 8000
    path = _db(
        tmp_path,
        [("c-long", long_text, None), ("c-exact", exact_text, None), ("c-short", "short", None)],
    )
    with caplog.at_level(logging.WARNING, logger="app.services.embedding_reindex"):
        out = er.reindex_for_model(MODEL, db_path=path, batch=2)

    # 整轮汇总：只有 1 个超长 chunk，丢弃 1 字符，最长原文 8001 字符。
    assert out["truncated_chunks"] == 1
    assert out["truncated_chars"] == 1
    assert out["max_original_chars"] == 8001
    assert out["total"] == out["migrated"] == 3
    # 向量内容本身不变：模型收到的是截断后的前 8000 字符
    #（按 id 排序：c-exact、c-long、c-short，故用集合断言，不依赖顺序）。
    assert "x" * 8000 in model.seen_texts
    assert "y" * 8000 in model.seen_texts
    assert all(len(t) <= 8000 for t in model.seen_texts)
    # 逐 chunk 标记：只有超长行带标记，且记原始长度。
    assert _meta_of(path, "c-long") == {
        "embedding_truncated": True,
        "embedding_original_chars": 8001,
    }
    # 未截断行（恰好 8000 字符与短文本）不写任何标记。
    assert _meta_of(path, "c-exact") is None
    assert _meta_of(path, "c-short") is None
    # 日志可观测：截断轮次记一条 warning。
    assert any("重建截断" in rec.message for rec in caplog.records)


def test_c2_no_truncation_marks_nothing(tmp_path, monkeypatch, caplog):
    _install_fake(monkeypatch)
    path = _db(tmp_path, [("c1", "a" * 8000, None), ("c2", "hello", None)])
    with caplog.at_level(logging.WARNING, logger="app.services.embedding_reindex"):
        out = er.reindex_for_model(MODEL, db_path=path, batch=32)
    assert out["truncated_chunks"] == 0
    assert out["truncated_chars"] == 0
    assert out["max_original_chars"] == 0
    assert _meta_of(path, "c1") is None
    assert _meta_of(path, "c2") is None
    assert not any("重建截断" in rec.message for rec in caplog.records)


def test_c2_existing_meta_keys_are_preserved(tmp_path, monkeypatch):
    _install_fake(monkeypatch)
    existing = json.dumps({"page_no_source": "approx", "chem": [{"value": "CAS 1-2-3"}]}, ensure_ascii=False)
    path = _db(tmp_path, [("c-long", "z" * 9000, existing)])
    out = er.reindex_for_model(MODEL, db_path=path, batch=32)
    assert out["truncated_chunks"] == 1
    assert out["truncated_chars"] == 1000
    assert out["max_original_chars"] == 9000
    meta = _meta_of(path, "c-long")
    assert meta["embedding_truncated"] is True
    assert meta["embedding_original_chars"] == 9000
    # 既有 meta 键原样保留，不被截断标记覆盖。
    assert meta["page_no_source"] == "approx"
    assert meta["chem"] == [{"value": "CAS 1-2-3"}]


def test_c2_truncation_counted_across_batches(tmp_path, monkeypatch):
    _install_fake(monkeypatch)
    path = _db(
        tmp_path,
        [("c1", "a" * 8500, None), ("c2", "b", None), ("c3", "c" * 8100, None)],
    )
    out = er.reindex_for_model(MODEL, db_path=path, batch=1)
    assert out["truncated_chunks"] == 2
    assert out["truncated_chars"] == 500 + 100
    assert out["max_original_chars"] == 8500
    assert _meta_of(path, "c1")["embedding_original_chars"] == 8500
    assert _meta_of(path, "c3")["embedding_original_chars"] == 8100
    assert _meta_of(path, "c2") is None


def test_c2_db_without_meta_column_still_reports_counts(tmp_path, monkeypatch):
    """最小 schema 库（无 meta 列）不写逐 chunk 标记，但汇总计数仍可观测。"""
    _install_fake(monkeypatch)
    path = _db(tmp_path, [("c1", "a" * 8005, None)], with_meta=False)
    out = er.reindex_for_model(MODEL, db_path=path, batch=32)
    assert out["truncated_chunks"] == 1
    assert out["truncated_chars"] == 5
    assert out["max_original_chars"] == 8005


def test_c2_finalize_defaults_to_zero_truncation():
    """直接调 _finalize_reindex 的旧调用方（无截断参数）默认三项为 0。"""
    result = er._finalize_reindex(total=7, migrated=7, elapsed_s=1.5, backup="/tmp/x.bak")
    assert result["truncated_chunks"] == 0
    assert result["truncated_chars"] == 0
    assert result["max_original_chars"] == 0


# ── C4：迁移测试隔离 ─────────────────────────────────────────────────────
# 报告 §4 C4：test_migration_0039_upgrade_downgrade 在
# test_batch3_pageindex_fusion + test_kb_ann 合跑时偶发 1 次失败
#（sqlalchemy…，完整 traceback 未留存），单跑与重跑全绿。
# 本轮二分复现（见汇报）：单测 ×10 全绿、两文件合跑 ×8 全绿，
# 未复现；根因未定位，故不改迁移测试、不加重试、不放宽断言。
# 以下测试把“合跑形态”钉成确定性隔离契约：同一进程内先有 KB
# chunk store 活动（模拟 test_kb_ann 前序测试留下的进程态），再对
# 全新 tmp DB 跑完整 0039 upgrade/downgrade/re-upgrade 序列，并换
# 第二个全新 DB 再跑一遍；同时断言迁移目标库与会话库、KB 库均不同。


def _run_0039_cycle(db_url: str, monkeypatch) -> None:
    from tests.alembic_helpers import run_downgrade, run_upgrade
    from tests.test_alembic_migrations import _column_names

    run_upgrade(db_url, monkeypatch, "0038")
    run_upgrade(db_url, monkeypatch, "head")
    assert "embedding_blob" in _column_names(db_url, "document_chunks")
    run_downgrade(db_url, monkeypatch, "0038")
    assert "embedding_blob" not in _column_names(db_url, "document_chunks")
    run_upgrade(db_url, monkeypatch, "head")
    run_upgrade(db_url, monkeypatch, "head")
    assert "embedding_blob" in _column_names(db_url, "document_chunks")


def test_c4_migration_0039_cycle_after_kb_store_activity(tmp_path, monkeypatch):
    import os

    import app.db.chunk_store as chunk_store_mod
    import app.db.database as db_mod
    from app.db.chunk_store import ChunkStore
    from app.db.database import Base, make_engine, make_session_factory

    session_db_url = os.environ.get("FORMUNEX_DB_URL")
    # 1. 进程内 KB 活动：独立 tmp 库上建 chunk store 并写入 chunk
    #（模拟 test_kb_ann 前序测试的进程态：store 单例 + session factory）。
    kb_engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(kb_engine)
    factory = make_session_factory(kb_engine)
    store = ChunkStore(factory)
    monkeypatch.setattr(chunk_store_mod, "_store", store)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    store.replace_for_source(
        "src-c4", [{"text": "c4 probe chunk", "embedding_model": "m"}]
    )
    assert len(store.get_by_source("src-c4")) == 1

    # 2. 同一进程内，对两个全新 DB 各跑一遍完整 0039 循环。
    for name in ("m39-first.db", "m39-second.db"):
        db_url = f"sqlite:///{tmp_path}/{name}"
        assert db_url != session_db_url
        assert db_url != f"sqlite:///{tmp_path}/kb.db"
        _run_0039_cycle(db_url, monkeypatch)

    # 3. 迁移序列未破坏先前 KB store 的进程态。
    assert len(store.get_by_source("src-c4")) == 1


def test_c2_switch_status_carries_truncation_summary(monkeypatch):
    """C2（wave 3，协调员补）：_switch_worker 把重嵌截断汇总透传进 switch_status。

    否则 C2 的截断标记只在日志里可见，模型切换的调用方查不到本轮截断了多少 chunk。
    """
    from app.api import embedding_models as em

    monkeypatch.setattr(
        "app.services.rag.set_runtime_embedding_model", lambda m: None
    )
    monkeypatch.setattr(
        "app.services.rag.embed_model_name", lambda: "old-model"
    )
    monkeypatch.setattr(
        "app.services.embedding_reindex.reindex_for_model",
        lambda model_id, progress_cb=None: {
            "truncated_chunks": 3,
            "truncated_chars": 500,
            "max_original_chars": 9000,
        },
    )
    try:
        em._switch_worker("new-model")
        status = em.switch_status()
        assert status["status"] == "done"
        assert status["truncated_chunks"] == 3
        assert status["truncated_chars"] == 500
        assert status["max_original_chars"] == 9000
    finally:
        # _SWITCH_TASK 是模块级全局状态，测试结束恢复 idle，避免影响其他测试。
        em._SWITCH_TASK.update(
            status="idle", progress=0.0, error=None,
            truncated_chunks=0, truncated_chars=0, max_original_chars=0,
        )
