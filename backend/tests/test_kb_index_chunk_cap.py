"""B1（2026-11-26 全面审查）：每源 chunk 上限截断不再静默。

* 截断时计数（累计 chunks / sources），并入 /api/kb/stats 的
  ``quality_gate_drops.ingest.chunk_cap_truncated``；
* 截断时写一行 ``kb_ingest_audit``（action=skip，指被丢弃的 chunk 部分）；
* 本次调用的截断详情经 ``last_chunk_cap_warning()`` 暴露，供 ingest
  响应 warnings 读取；
* 默认上限为暂定值 2000，待 Cheng 定最终值。
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.models import KbIngestAudit
from app.services import kb_index
from app.services import kb_ingest_audit as audit_mod
from tests.alembic_helpers import run_upgrade


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_KB_MAX_CHUNKS_PER_SOURCE", "5")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    kb_index.reset_chunk_cap_truncation_stats()
    yield
    kb_index.reset_chunk_cap_truncation_stats()
    get_settings.cache_clear()


def _long_text(n_paras: int = 8) -> str:
    """n 节互不相同的短节：每节带标题，稳定切成 1 个 chunk（>30 字符、无重复）。"""
    sections = []
    for i in range(n_paras):
        sections.append(
            f"# 第{i}节\n\n"
            f"第{i}段：环氧树脂防腐底漆的耐盐雾性能与磷酸锌含量密切相关，"
            f"附着力划格法零级，固化剂异佛尔酮二胺用量二十四份，喷涂固化后测试。\n"
        )
    return "\n".join(sections)


def test_truncation_counts_audit_and_warning(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(
        audit_mod, "record_ingest_audit", lambda **kw: seen.append(kw)
    )

    rows = kb_index.prepare_chunk_rows(_long_text(8), "src-cap", embed=False)

    assert rows is not None
    assert len(rows) == 5, "超过上限 5 的尾部 chunk 应被截断"
    stats = kb_index.chunk_cap_truncation_stats()
    assert stats["truncated_chunks"] == 3
    assert stats["truncated_sources"] == 1
    warning = kb_index.last_chunk_cap_warning()
    assert warning is not None
    assert warning["code"] == "chunk_cap_truncated"
    assert warning["source_id"] == "src-cap"
    assert warning["chunks_before_cap"] == 8
    assert warning["cap"] == 5
    assert warning["truncated_chunks"] == 3
    assert "截断尾部 3 个 chunk" in warning["message"]
    assert len(seen) == 1
    assert seen[0]["action"] == "skip"
    assert seen[0]["source"] == "src-cap"
    assert seen[0]["reason"] == "chunk_cap_truncated:3/8@cap5"
    # stats 面可见：/api/kb/stats 的 quality_gate_drops.ingest 臂。
    drops = kb_index._quality_gate_drops()
    assert drops["ingest"]["chunk_cap_truncated"] == 3
    body = kb_index.kb_stats()
    assert body["quality_gate_drops"]["ingest"]["chunk_cap_truncated"] == 3


def test_no_truncation_leaves_counters_clear_and_clears_last_warning(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(
        audit_mod, "record_ingest_audit", lambda **kw: seen.append(kw)
    )

    rows = kb_index.prepare_chunk_rows(_long_text(8), "src-cap", embed=False)
    assert rows is not None and len(rows) == 5
    assert kb_index.last_chunk_cap_warning() is not None

    rows2 = kb_index.prepare_chunk_rows(_long_text(2), "src-short", embed=False)
    assert rows2 is not None and len(rows2) == 2
    # 累计计数不动；本次无截断时警告清零，不把上一次的警告误读为本次的。
    stats = kb_index.chunk_cap_truncation_stats()
    assert stats["truncated_chunks"] == 3
    assert stats["truncated_sources"] == 1
    assert kb_index.last_chunk_cap_warning() is None
    assert len(seen) == 1, "未触顶时不应写截断审计行"


def test_default_cap_is_2000_provisional(monkeypatch):
    # 本文件 autouse fixture 把上限压到 5，此处恢复默认再断言。
    monkeypatch.delenv("FORMUNEX_KB_MAX_CHUNKS_PER_SOURCE", raising=False)
    get_settings.cache_clear()
    # 暂定值 2000 待 Cheng 定最终值；改最终值时同步改此处与
    # test_kb_fulltext_budget.py::test_chunk_cap_allows_a_long_document。
    assert get_settings().kb_max_chunks_per_source == 2000


@pytest.fixture()
def db(tmp_path, monkeypatch):
    from app.db.database import default_session_factory

    db_url = f"sqlite:///{tmp_path}/cap_audit.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    get_settings.cache_clear()
    yield default_session_factory()
    get_settings.cache_clear()


def test_truncation_audit_row_reaches_sql_not_jsonl(db, monkeypatch):
    """截断审计行真实落库（非 JSONL fallback）。"""
    monkeypatch.setattr(
        audit_mod, "_append_jsonl",
        lambda row: (_ for _ in ()).throw(AssertionError("fell back to JSONL")),
    )
    rows = kb_index.prepare_chunk_rows(_long_text(8), "src-cap-sql", embed=False)
    assert rows is not None and len(rows) == 5
    with db() as s:
        stored = s.execute(select(KbIngestAudit)).scalars().all()
    assert len(stored) == 1
    assert stored[0].source == "src-cap-sql"
    assert stored[0].action == "skip"
    assert stored[0].reason == "chunk_cap_truncated:3/8@cap5"
