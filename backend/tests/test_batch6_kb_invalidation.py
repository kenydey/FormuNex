"""批 6：统一失效协议——一次源变化，四层同步失效/更新。

开关默认关；以下测试显式开开关验证新行为，另有开关关时行为不变的
对照。0 LLM 调用；失效只在入库/删除路径跑，不占问答延迟。

一致性探针（test_consistency_probe_change_then_delete）：
入库 v1 → 改内容 v2 → 删源，断言四层同步：chunk 更新/清空、KG 提及
与边删除/重建、Wiki 页刷新或 stale、树重建或删除；log.md 记变更历史。
KG 提及/边删 0 行也是可断言的行为（test_delete_source_with_no_kg_rows）。
"""
from __future__ import annotations

import hashlib

import pytest

from app.config import get_settings
from app.services.kb_invalidation import invalidate_source, source_content_hash


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_KG_ENABLED", "true")
    monkeypatch.setenv("FORMUNEX_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUNEX_WIKI_DRIFT_REFRESH_ENABLED", "true")
    monkeypatch.setenv("FORMUNEX_KB_INVALIDATION_ENABLED", "true")
    monkeypatch.setenv("FORMUNEX_KG_CHAT_ENTITY_REFS_ON_EVIDENCE", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated Source + Chunk + Tree + KG + Product + Wiki stores, one temp SQLite."""
    import app.db.chunk_store as chunk_store_mod
    import app.db.database as database_mod
    import app.db.entity_store as entity_store_mod
    import app.db.product_store as product_store_mod
    import app.db.source_store as source_store_mod
    import app.db.toc_tree_store as toc_store_mod
    import app.db.wiki_store as wiki_store_mod
    from app.db.chunk_store import ChunkStore
    from app.db.database import Base, make_engine, make_session_factory
    from app.db.entity_store import EntityStore
    from app.db.product_store import ProductStore
    from app.db.source_store import SourceStore
    from app.db.toc_tree_store import TocTreeStore
    from app.db.wiki_store import WikiStore

    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{tmp_path}/kb6.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    sources = SourceStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", sources)
    monkeypatch.setattr(chunk_store_mod, "_store", ChunkStore(factory))
    monkeypatch.setattr(toc_store_mod, "_store", TocTreeStore(factory))
    monkeypatch.setattr(entity_store_mod, "_store", EntityStore(factory))
    monkeypatch.setattr(product_store_mod, "_store", ProductStore(factory))
    monkeypatch.setattr(wiki_store_mod, "_store", WikiStore(factory, root=wiki_root))
    monkeypatch.setattr(database_mod, "default_session_factory", lambda: factory)
    get_settings.cache_clear()
    yield {"sources": sources, "wiki_root": wiki_root, "factory": factory}
    get_settings.cache_clear()


def _para(grade: str, cas: str) -> str:
    return (
        f"PVAM {grade} is a polyvinyl alcohol grade supplied by Kuraray, "
        f"CAS {cas}, used as a film former in waterborne coatings. "
        "The grade provides good adhesion and water resistance. "
    )


TEXT_V1 = "\n\n".join([_para("0595B", "9002-89-5")] * 30)
TEXT_V2 = "\n\n".join([_para("0595C", "25068-38-6")] * 60)
PLAIN_TEXT = "\n\n".join(
    ["A short technical note about drying ovens and airflow control."] * 10
)


def _ingest(env, title: str, text: str) -> str:
    from app.services import kb_index

    sid = env["sources"].create(
        filename=f"{title}.txt",
        title=title,
        source_kind="patent",
        full_text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    assert kb_index.index_source(sid, text, embed=False) > 0
    return sid


def _counts(sid: str) -> dict:
    from app.db.chunk_store import get_chunk_store
    from app.db.entity_store import get_entity_store
    from app.db.toc_tree_store import get_toc_tree_store

    stats = get_entity_store().stats()
    tree = get_toc_tree_store().get(sid)
    return {
        "chunks": len(get_chunk_store().get_by_source(sid)),
        "mentions": int(stats["mentions"]),
        "links": int(stats["links"]),
        "tree_page_count": int(tree.page_count) if tree is not None else 0,
        "tree_exists": tree is not None,
    }


def _wiki_page(path: str):
    from app.db.wiki_store import get_wiki_store

    wiki = get_wiki_store()
    row = wiki.get_by_path(path)
    md = wiki.read_markdown(path) if row is not None else None
    return row, md


def _log_lines(env) -> list[str]:
    log_path = env["wiki_root"] / "log.md"
    if not log_path.exists():
        return []
    return [
        ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")
    ]


# ── 开关与传播键 ─────────────────────────────────────────────────────────


def test_invalidation_disabled_is_noop(env, monkeypatch):
    monkeypatch.setenv("FORMUNEX_KB_INVALIDATION_ENABLED", "false")
    get_settings.cache_clear()
    res = invalidate_source("whatever", reason="content_changed")
    assert res["ok"] is False
    assert res["reason"] == "disabled"
    assert res["layers"] == {}


def test_invalidate_unchanged_short_circuit(env):
    sid = _ingest(env, "PVAM note", TEXT_V1)
    res = invalidate_source(
        sid,
        reason="content_changed",
        old_content_hash=source_content_hash(TEXT_V1),
        new_content_hash=source_content_hash(TEXT_V1),
        prior_chunk_count=2,
    )
    assert res["ok"] is True
    assert res["reason"] == "unchanged"
    assert res["layers"] == {}
    # force 绕过短路：仍跑四层
    forced = invalidate_source(
        sid,
        reason="content_changed",
        old_content_hash=source_content_hash(TEXT_V1),
        new_content_hash=source_content_hash(TEXT_V1),
        prior_chunk_count=2,
        force=True,
    )
    assert forced["ok"] is True
    assert forced["reason"] == "content_changed"
    assert forced["layers"]["chunk"]["status"] == "ok"


def test_remove_source_block_unit():
    from app.services.wiki.schema import remove_source_block

    body = (
        "# PVAM\n\n## Summary\nSome summary.\n\n## Evidence\n"
        "### Source `sid-a`\n- document: A\n- source_hash: aaa\n"
        "### Source `sid-b`\n- document: B\n- source_hash: bbb\n"
    )
    new_body, removed = remove_source_block(body, "sid-a")
    assert removed is True
    assert "### Source `sid-a`" not in new_body
    assert "### Source `sid-b`" in new_body
    assert "Some summary." in new_body
    same, removed2 = remove_source_block(body, "sid-missing")
    assert removed2 is False
    assert same == body


# ── 一致性探针：入库 → 改内容 → 删源 ────────────────────────────────────


def test_consistency_probe_change_then_delete(env):
    from app.services import kb_index
    from app.services.kb_delete import delete_kb_source

    # 1. 入库 v1
    sid = _ingest(env, "PVAM note", TEXT_V1)
    c1 = _counts(sid)
    assert c1["chunks"] == 2
    assert c1["mentions"] == 4  # CAS 实体 + PVAM 牌号实体，各 2 chunk
    assert c1["links"] == 0  # 规则关系模板在本语料上无边（批 1-2 结论）
    assert c1["tree_exists"] and c1["tree_page_count"] == 2
    row, md = _wiki_page("materials/pvam.md")
    assert row is not None and "0595B" in (md or "")
    assert f"### Source `{sid}`" in (md or "")
    chem_row, _ = _wiki_page("chemicals/9002-89-5.md")
    assert chem_row is not None
    assert len(_log_lines(env)) == 1
    assert "content_changed" in _log_lines(env)[0]

    # 2. 改内容 v2（同源重入库：牌号 0595B→0595C，CAS 换号，篇幅翻倍）
    assert kb_index.index_source(sid, TEXT_V2, embed=False) > 0
    c2 = _counts(sid)
    assert c2["chunks"] > c1["chunks"]
    assert c2["tree_exists"] and c2["tree_page_count"] > c1["tree_page_count"]
    from app.db.chunk_store import get_chunk_store
    from app.db.entity_store import get_entity_store

    texts = " ".join(c.text or "" for c in get_chunk_store().get_by_source(sid))
    assert "0595C" in texts and "0595B" not in texts
    assert "25068-38-6" in texts and "9002-89-5" not in texts
    # KG：旧 CAS 提及清掉、新 CAS 提及建起（link_source 先删后建）
    mentions = get_entity_store().stats()
    assert mentions["mentions"] == 2 * c2["chunks"]
    # Wiki 材料页刷新：Summary 与 Source 块 hash 跟新内容走
    row, md = _wiki_page("materials/pvam.md")
    assert row is not None and "0595C" in (md or "") and "0595B" not in (md or "")
    assert f"- source_hash: {source_content_hash(TEXT_V2)}" in (md or "")
    # 旧 CAS 页被横扫：块移除、source_ids 清空、stale
    old_chem, old_md = _wiki_page("chemicals/9002-89-5.md")
    assert old_chem is not None
    assert list(old_chem.source_ids or []) == []
    assert "stale" in (old_chem.flags or [])
    assert f"### Source `{sid}`" not in (old_md or "")
    # 新 CAS 页建起
    new_chem, _ = _wiki_page("chemicals/25068-38-6.md")
    assert new_chem is not None and sid in (new_chem.source_ids or [])
    assert len(_log_lines(env)) == 2

    # 3. 删源
    res = delete_kb_source(sid)
    assert res["chunks_removed"] == c2["chunks"]
    assert res["mentions_removed"] == 2 * c2["chunks"]
    assert res["links_removed"] == 0
    c3 = _counts(sid)
    assert c3["chunks"] == 0
    assert c3["mentions"] == 0
    assert c3["links"] == 0
    assert c3["tree_exists"] is False
    row, md = _wiki_page("materials/pvam.md")
    assert row is not None  # 页保留，不删页
    assert list(row.source_ids or []) == []
    assert "stale" in (row.flags or [])
    assert f"### Source `{sid}`" not in (md or "")
    for path in ("chemicals/9002-89-5.md", "chemicals/25068-38-6.md"):
        r2, m2 = _wiki_page(path)
        assert r2 is not None and list(r2.source_ids or []) == []
        assert "stale" in (r2.flags or [])
        assert f"### Source `{sid}`" not in (m2 or "")
    log = _log_lines(env)
    assert len(log) == 3
    assert "deleted" in log[-1]


def test_delete_source_with_no_kg_rows(env):
    """KG 提及/边删 0 行也是可断言的行为（当前 kb_entity_links 0 行的现状）。"""
    from app.services.kb_delete import delete_kb_source

    sid = _ingest(env, "Plain note", PLAIN_TEXT)
    c1 = _counts(sid)
    assert c1["chunks"] > 0
    assert c1["mentions"] == 0
    assert c1["links"] == 0
    assert c1["tree_exists"] is True
    res = delete_kb_source(sid)
    assert res["chunks_removed"] == c1["chunks"]
    assert res["mentions_removed"] == 0
    assert res["links_removed"] == 0
    c2 = _counts(sid)
    assert c2["chunks"] == 0 and c2["tree_exists"] is False
    assert "deleted" in _log_lines(env)[-1]


# ── fail-open：任一层失败不阻断其他层、不抛错 ───────────────────────────


def test_layer_failure_fails_open(env, monkeypatch):
    sid = _ingest(env, "PVAM note", TEXT_V1)

    import app.services.kg.entity_linker as linker_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("boom-kg")

    monkeypatch.setattr(linker_mod, "link_source", _boom)
    res = invalidate_source(
        sid,
        reason="content_changed",
        old_content_hash=source_content_hash(TEXT_V1),
        new_content_hash=source_content_hash(TEXT_V2),
        prior_chunk_count=2,
    )
    assert res["ok"] is False  # 有一层失败，整体 ok 为 False
    assert res["layers"]["kg"]["status"] == "failed"
    assert "boom-kg" in res["layers"]["kg"]["error"]
    assert res["layers"]["chunk"]["status"] == "ok"
    assert res["layers"]["tree"]["status"] == "ok"
    assert res["layers"]["wiki"]["status"] == "ok"
    assert res["notices"], "失败层须写 notice"


# ── 开关关时旧行为不变 ───────────────────────────────────────────────────


def test_switch_off_index_source_keeps_old_hooks(env, monkeypatch):
    monkeypatch.setenv("FORMUNEX_KB_INVALIDATION_ENABLED", "false")
    get_settings.cache_clear()
    sid = _ingest(env, "PVAM note", TEXT_V1)
    c = _counts(sid)
    assert c["chunks"] == 2
    assert c["mentions"] == 4  # 旧 KG 钩子仍跑
    assert c["tree_exists"] and c["tree_page_count"] == 2
    # 旧路径 Wiki 靠异步分发（测试环境 celery_eager=true 时后台线程仍可能
    # 编译建页，时序不确定，此处不断言页是否存在）；失效协议本身未启用，
    # 故 log.md 无失效行是确定性的。
    assert _log_lines(env) == []
