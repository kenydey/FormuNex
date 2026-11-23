"""批 5：融合四 + Wiki 治理——wikilink 扩展 / OKF / 漂移刷新 / flagged 页处理。

开关默认全关；本文件只在显式开开关的用例里验证新行为，其余用例
验证开关关时行为不变。0 LLM 调用。
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.models import SourceDocument
from app.db.source_store import SourceStore
from app.db.wiki_store import WikiStore
from app.domain.schemas import ProductMention, SourceGuideSchema


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUNEX_KG_CHAT_ENTITY_REFS_ON_EVIDENCE", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    """Isolated Source + Chunk + Wiki stores on one temp SQLite + wiki root."""
    import app.db.chunk_store as chunk_store_mod
    import app.db.source_store as source_store_mod
    import app.db.wiki_store as wiki_store_mod

    db_path = tmp_path / "wiki5.db"
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    get_settings.cache_clear()
    return src, chk, wiki, wiki_root, factory


def _upsert(wiki, *, path, kind, title, body_extra="", flags=None, source_ids=("s1",)):
    from app.services.wiki.schema import dump_page

    key = path.rsplit("/", 1)[-1].replace(".md", "")
    md = dump_page(
        kind=kind,
        title=title,
        entity_id=f"{kind}:{key}",
        norm_key=key,
        source_ids=list(source_ids),
        summary=f"{title} summary.",
    )
    if body_extra:
        md = md.rstrip() + "\n\n## Links\n" + body_extra + "\n"
    return wiki.upsert_page(
        path=path,
        kind=kind,
        title=title,
        norm_key=key,
        entity_id=f"{kind}:{key}",
        markdown=md,
        source_ids=list(source_ids),
        flags=list(flags or []),
    )


def _chain(wiki):
    _upsert(wiki, path="materials/alpha.md", kind="material", title="Alpha Material",
            body_extra="See [[Beta Material]]")
    _upsert(wiki, path="materials/beta.md", kind="material", title="Beta Material",
            body_extra="See [[Gamma Material]]")
    _upsert(wiki, path="materials/gamma.md", kind="material", title="Gamma Material")


def _guide(trade="E-51", grade="E51"):
    return SourceGuideSchema(
        summary="环氧树脂配方摘要",
        key_entities=["E-51", "25068-38-6"],
        faqs=["固化条件？"],
        products=[
            ProductMention(trade_name=trade, grade=grade, supplier="本地", role="树脂"),
        ],
        status="verified",
    )


# ── 5-1 Wiki→Graph ───────────────────────────────────────────────────────────


def test_wikilink_neighbors_one_hop_only(wiki_env):
    from app.services.wiki.page_graph import wikilink_neighbors

    _, _, wiki, _, _ = wiki_env
    _chain(wiki)
    assert wikilink_neighbors(["materials/alpha.md"]) == {
        "materials/alpha.md": ["materials/beta.md"]
    }
    assert wikilink_neighbors(["materials/beta.md"]) == {
        "materials/beta.md": ["materials/alpha.md", "materials/gamma.md"]
    }
    assert wikilink_neighbors(["materials/gamma.md"]) == {
        "materials/gamma.md": ["materials/beta.md"]
    }
    assert wikilink_neighbors(["materials/nope.md"]) == {}


def test_wikilink_expansion_returns_neighbor_not_second_hop(wiki_env):
    from app.services.kg.retrieval import _wikilink_expansion

    _, _, wiki, _, _ = wiki_env
    _chain(wiki)
    evs, seeds = _wikilink_expansion("Alpha")
    assert seeds == 1
    got = {ev.identifier for _, ev, _ in evs}
    assert "wiki:materials/beta.md" in got
    assert "wiki:materials/gamma.md" not in got  # 1 跳上限：二跳不进
    assert "wiki:materials/alpha.md" not in got  # 种子本身不重复进池


def test_wikilink_expansion_no_seed_no_expansion(wiki_env):
    from app.services.kg.retrieval import _wikilink_expansion

    _, _, wiki, _, _ = wiki_env
    _chain(wiki)
    # 查询词刻意避开 fixture 正文里的子串（如 "_No evidence…" 中的 no）。
    evs, seeds = _wikilink_expansion("zzzqqq wwwwqqqq")
    assert (evs, seeds) == ([], 0)


def test_kg_retrieve_switch_off_no_wikilink(wiki_env, monkeypatch):
    import app.services.kb_bilingual as bilingual_mod
    import app.services.kg.retrieval as retrieval_mod
    from app.domain.kg_schemas import EntityResolveResponse
    from app.services.kg.retrieval import retrieve

    _, _, wiki, _, _ = wiki_env
    _chain(wiki)
    monkeypatch.setattr(
        retrieval_mod,
        "resolve_query",
        lambda q, settings=None: EntityResolveResponse(
            query=q, mode="semantic", expanded_entity_ids=[]
        ),
    )
    monkeypatch.setattr(bilingual_mod, "search", lambda *a, **k: [])
    monkeypatch.setenv("FORMUNEX_WIKI_GRAPH_EXPANSION_ENABLED", "false")
    get_settings.cache_clear()
    out = retrieve("Alpha")
    assert out.stats.wikilink_hits == 0
    assert out.stats.wikilink_seeds == 0
    assert all(ev.identifier != "wiki:materials/beta.md" for ev in out.evidence)


def test_kg_retrieve_switch_on_expands_wikilink(wiki_env, monkeypatch):
    import app.services.kb_bilingual as bilingual_mod
    import app.services.kg.retrieval as retrieval_mod
    from app.domain.kg_schemas import EntityResolveResponse
    from app.services.kg.retrieval import retrieve

    _, _, wiki, _, _ = wiki_env
    _chain(wiki)
    monkeypatch.setattr(
        retrieval_mod,
        "resolve_query",
        lambda q, settings=None: EntityResolveResponse(
            query=q, mode="semantic", expanded_entity_ids=[]
        ),
    )
    monkeypatch.setattr(bilingual_mod, "search", lambda *a, **k: [])
    monkeypatch.setenv("FORMUNEX_WIKI_GRAPH_EXPANSION_ENABLED", "true")
    get_settings.cache_clear()
    out = retrieve("Alpha")
    assert out.stats.wikilink_seeds == 1
    assert out.stats.wikilink_hits == 1
    assert out.stats.mention_hits == 0  # 实体侧仍为空，分开度量
    assert any(ev.identifier == "wiki:materials/beta.md" for ev in out.evidence)


# ── 5-2 OKF 对齐 ─────────────────────────────────────────────────────────────


def test_dump_page_okf_round_trip():
    from app.services.wiki.schema import dump_page, parse_front_matter

    md = dump_page(
        kind="material",
        title="PVAM",
        entity_id="material:pvam",
        norm_key="pvam",
        source_ids=["s1"],
        summary="s.",
        version=3,
    )
    meta, _ = parse_front_matter(md)
    assert meta["type"] == "material"
    assert meta["title"] == "PVAM"
    assert meta["owner"] == ""
    assert meta["tags"] == ["material", "pvam"]
    assert meta["version"] == 3 and isinstance(meta["version"], int)


def test_okf_example_field_shape():
    """与文章 OKF 示例页逐字段对照：type/title/owner/tags 四字段同形同值类型。"""
    from app.services.wiki.schema import dump_page, parse_front_matter

    # 文章第 8 页 OKF 示例：type: metric / title: Monthly Recurring Revenue
    # / owner: finance-team / tags: [revenue, saas, reporting]
    md = dump_page(
        kind="metric",
        title="Monthly Recurring Revenue",
        entity_id="metric:mrr",
        norm_key="mrr",
        source_ids=["s1"],
        summary="MRR is the sum of all active subscription revenue.",
        owner="finance-team",
        tags=["revenue", "saas", "reporting"],
    )
    meta, body = parse_front_matter(md)
    assert meta["type"] == "metric"
    assert meta["title"] == "Monthly Recurring Revenue"
    assert meta["owner"] == "finance-team"
    assert meta["tags"] == ["revenue", "saas", "reporting"]
    assert isinstance(meta["tags"], list)
    assert body.startswith("# Monthly Recurring Revenue")


def test_normalize_okf_preserves_body_and_owner():
    from app.services.wiki.schema import normalize_okf_front_matter, parse_front_matter

    md = (
        "---\nkind: material\ntitle: A\nowner: chem-team\n"
        "entity_id: material:a\nnorm_key: a\nsource_ids: []\nflags: []\n"
        "updated_at: 2026-01-01T00:00:00Z\n---\n\n# A\n\n## Summary\nkeep me.\n"
    )
    out = normalize_okf_front_matter(md, kind="material", version=2)
    meta, body = parse_front_matter(out)
    assert meta["owner"] == "chem-team"  # 已有 owner 保留，不覆盖
    assert meta["type"] == "material"
    assert meta["tags"] == ["material", "a"]
    assert meta["version"] == 2
    assert "keep me." in body
    out2 = normalize_okf_front_matter(out, kind="material", version=5)
    meta2, _ = parse_front_matter(out2)
    assert meta2["version"] == 5  # version 跟 DB revision 走
    assert meta2["owner"] == "chem-team"


def test_upsert_okf_version_tracks_revision(wiki_env, monkeypatch):
    _, _, wiki, _, _ = wiki_env
    monkeypatch.setenv("FORMUNEX_WIKI_OKF_ENABLED", "true")
    get_settings.cache_clear()
    row = _upsert(wiki, path="materials/a.md", kind="material", title="A")
    assert row.revision == 1
    meta_row = wiki.read_markdown("materials/a.md") or ""
    assert "version: 1" in meta_row
    # 内容不变重写：revision 不 bump（用落盘原文重写，避免 dump_page 的
    # updated_at 跨秒导致内容"变化"）。
    stored_md = wiki.read_markdown("materials/a.md") or ""
    row2 = wiki.upsert_page(
        path="materials/a.md", kind="material", title="A", norm_key="a",
        entity_id="material:a", markdown=stored_md, source_ids=["s1"],
    )
    assert row2.revision == 1
    # 内容变了：revision 与 version 同步 +1
    from app.services.wiki.schema import dump_page

    md = dump_page(
        kind="material", title="A", entity_id="material:a", norm_key="a",
        source_ids=["s1"], summary="changed summary.",
    )
    row3 = wiki.upsert_page(
        path="materials/a.md", kind="material", title="A", norm_key="a",
        entity_id="material:a", markdown=md, source_ids=["s1"],
    )
    assert row3.revision == 2
    assert "version: 2" in (wiki.read_markdown("materials/a.md") or "")


def test_upsert_okf_index_and_log(wiki_env, monkeypatch):
    _, _, wiki, root, _ = wiki_env
    monkeypatch.setenv("FORMUNEX_WIKI_OKF_ENABLED", "true")
    get_settings.cache_clear()
    _upsert(wiki, path="materials/a.md", kind="material", title="A")
    _upsert(wiki, path="materials/b.md", kind="material", title="B")
    assert (root / "index.md").is_file()
    assert (root / "materials" / "index.md").is_file()
    assert (root / "log.md").is_file()
    index = (root / "index.md").read_text(encoding="utf-8")
    assert "materials/a.md" in index and "materials/b.md" in index
    assert "revision 1" in index
    log_lines = [
        ln for ln in (root / "log.md").read_text(encoding="utf-8").splitlines()
        if ln.startswith("- ")
    ]
    assert len(log_lines) == 2
    assert all("created" in ln for ln in log_lines)


def test_upsert_okf_off_no_index_or_log(wiki_env):
    _, _, wiki, root, _ = wiki_env
    _upsert(wiki, path="materials/a.md", kind="material", title="A")
    assert not (root / "index.md").exists()
    assert not (root / "log.md").exists()
    assert "version:" not in (wiki.read_markdown("materials/a.md") or "")


# ── 5-3 内容漂移修复 ─────────────────────────────────────────────────────────


def test_upsert_source_block_unit():
    from app.services.wiki.schema import upsert_source_block

    blocks, refreshed = upsert_source_block([], "s1", ["### Source `s1`", "- x"], "h1",
                                             drift_enabled=False)
    assert len(blocks) == 1 and not refreshed
    assert "- source_hash: h1" in blocks[0]
    # drift 关：已有块即跳过
    blocks2, r2 = upsert_source_block(blocks, "s1", ["### Source `s1`", "- y"], "h2",
                                      drift_enabled=False)
    assert blocks2 == blocks and not r2
    # drift 开 + 同 hash：跳过
    blocks3, r3 = upsert_source_block(blocks, "s1", ["### Source `s1`", "- y"], "h1",
                                      drift_enabled=True)
    assert blocks3 == blocks and not r3
    # drift 开 + hash 变了：替换，仍只 1 块
    blocks4, r4 = upsert_source_block(blocks, "s1", ["### Source `s1`", "- y"], "h2",
                                      drift_enabled=True)
    assert r4 and len(blocks4) == 1 and "- y" in blocks4[0]
    assert "- source_hash: h2" in blocks4[0]
    # drift 开 + 旧块无 hash 行：视为未知新鲜度，替换一次
    blocks5, r5 = upsert_source_block(["### Source `s1`\n- old"], "s1",
                                      ["### Source `s1`", "- new"], "h3",
                                      drift_enabled=True)
    assert r5 and "- source_hash: h3" in blocks5[0]


def _compile_and_change_source(src, factory, sid, *, new_hash, new_grade):
    from app.services.wiki import compile as wiki_compile

    with factory() as session:
        doc = session.get(SourceDocument, sid)
        assert doc is not None
        doc.content_hash = new_hash
        session.commit()
    src.update_fields(sid, source_guide=_guide(grade=new_grade).model_dump(mode="json"))
    return wiki_compile.compile_source(sid)


def test_compile_drift_off_keeps_old_block_and_summary(wiki_env, monkeypatch):
    from app.services.wiki import compile as wiki_compile

    src, _, wiki, _, factory = wiki_env
    monkeypatch.setenv("FORMUNEX_WIKI_DRIFT_REFRESH_ENABLED", "false")
    get_settings.cache_clear()
    sid = src.create(
        filename="p.pdf", title="环氧专利", source_kind="patent",
        full_text="# body\n" + ("环氧树脂 " * 40),
        content_hash="h1", source_guide=_guide(grade="E51"),
    )
    wiki_compile.compile_source(sid)
    _compile_and_change_source(src, factory, sid, new_hash="h9", new_grade="E52")
    md = wiki.read_markdown("materials/e51.md") or ""
    assert md.count(f"### Source `{sid}`") == 1
    assert "- source_hash: h1" in md  # 旧块原样保留
    assert "Grade: E51." in md  # 旧 Summary 原样保留


def test_compile_drift_on_refreshes_block_and_summary(wiki_env, monkeypatch):
    from app.services.wiki import compile as wiki_compile

    src, _, wiki, _, factory = wiki_env
    monkeypatch.setenv("FORMUNEX_WIKI_DRIFT_REFRESH_ENABLED", "true")
    get_settings.cache_clear()
    sid = src.create(
        filename="p.pdf", title="环氧专利", source_kind="patent",
        full_text="# body\n" + ("环氧树脂 " * 40),
        content_hash="h1", source_guide=_guide(grade="E51"),
    )
    wiki_compile.compile_source(sid)
    _compile_and_change_source(src, factory, sid, new_hash="h9", new_grade="E52")
    md = wiki.read_markdown("materials/e51.md") or ""
    assert md.count(f"### Source `{sid}`") == 1  # 替换不是追加
    assert "- source_hash: h9" in md
    assert "Grade: E52." in md  # Summary 跟着刷新


def test_compile_drift_on_same_hash_no_refresh(wiki_env, monkeypatch):
    from app.services.wiki import compile as wiki_compile

    src, _, wiki, _, _ = wiki_env
    monkeypatch.setenv("FORMUNEX_WIKI_DRIFT_REFRESH_ENABLED", "true")
    get_settings.cache_clear()
    sid = src.create(
        filename="p.pdf", title="环氧专利", source_kind="patent",
        full_text="# body\n" + ("环氧树脂 " * 40),
        content_hash="h1", source_guide=_guide(grade="E51"),
    )
    wiki_compile.compile_source(sid)
    wiki_compile.compile_source(sid)
    md = wiki.read_markdown("materials/e51.md") or ""
    assert md.count(f"### Source `{sid}`") == 1
    assert "- source_hash: h1" in md


# ── 5-4 lint-flagged 页处理 ──────────────────────────────────────────────────


def _blend_pages(wiki):
    _upsert(wiki, path="materials/alpha.md", kind="material", title="Alpha Material")
    _upsert(wiki, path="materials/beta.md", kind="material", title="Beta Material")


def test_flagged_wiki_paths(wiki_env):
    from app.services.wiki.retrieve import flagged_wiki_paths

    _, _, wiki, _, _ = wiki_env
    _blend_pages(wiki)
    assert flagged_wiki_paths() == set()
    _upsert(wiki, path="materials/alpha.md", kind="material", title="Alpha Material",
            flags=["stale"])
    assert flagged_wiki_paths() == {"materials/alpha.md"}


def test_upsert_same_content_flag_only_update_persists(wiki_env):
    """回归：同内容仅改 flags/source_ids 时，库里必须跟着变（此前
    expunge 先于 flush，改动只留在返回对象里，库里还是旧值；同秒
    重写必现，跨秒因 updated_at 变化走更新分支而不现，故时隐时现）。"""
    from app.services.wiki.schema import dump_page

    _, _, wiki, _, _ = wiki_env
    md = dump_page(
        kind="material", title="Alpha Material", entity_id="material:alpha",
        norm_key="alpha", source_ids=["s1"], summary="Alpha Material summary.",
    )
    wiki.upsert_page(
        path="materials/alpha.md", kind="material", title="Alpha Material",
        norm_key="alpha", entity_id="material:alpha", markdown=md,
        source_ids=["s1"], flags=[],
    )
    row2 = wiki.upsert_page(
        path="materials/alpha.md", kind="material", title="Alpha Material",
        norm_key="alpha", entity_id="material:alpha", markdown=md,
        source_ids=["s1", "s2"], flags=["stale"],
    )
    assert row2.revision == 1  # 内容未变，revision 不 bump
    reread = wiki.get_by_path("materials/alpha.md")
    assert reread is not None
    assert list(reread.flags or []) == ["stale"]
    assert list(reread.source_ids or []) == ["s1", "s2"]


def test_blend_excludes_flagged(wiki_env, monkeypatch):
    from app.services.wiki.retrieve import blend_wiki_evidence

    _, _, wiki, _, _ = wiki_env
    _blend_pages(wiki)
    _upsert(wiki, path="materials/alpha.md", kind="material", title="Alpha Material",
            flags=["stale"])
    monkeypatch.setenv("FORMUNEX_WIKI_FLAGGED_MODE", "exclude")
    get_settings.cache_clear()
    merged, added = blend_wiki_evidence("Alpha Material", [])
    got = {ev.identifier for ev in merged}
    assert "wiki:materials/alpha.md" not in got
    assert "wiki:materials/beta.md" in got  # 未 flag 页不受影响
    assert added == 1


def test_blend_downweights_flagged(wiki_env, monkeypatch):
    from app.services.wiki.retrieve import blend_wiki_evidence

    _, _, wiki, _, _ = wiki_env
    _blend_pages(wiki)
    _upsert(wiki, path="materials/alpha.md", kind="material", title="Alpha Material",
            flags=["broken"])
    monkeypatch.setenv("FORMUNEX_WIKI_FLAGGED_MODE", "downweight")
    monkeypatch.setenv("FORMUNEX_WIKI_FLAGGED_WEIGHT", "0.5")
    get_settings.cache_clear()
    merged, _ = blend_wiki_evidence("Alpha Material", [])
    flagged = [ev for ev in merged if ev.identifier == "wiki:materials/alpha.md"]
    clean = [ev for ev in merged if ev.identifier == "wiki:materials/beta.md"]
    assert len(flagged) == 1 and len(clean) == 1
    # 降权后 flagged 页排在未 flag 页之后
    assert merged.index(clean[0]) < merged.index(flagged[0])
    assert float(flagged[0].relevance or 0) <= 0.5


def test_blend_off_keeps_flagged(wiki_env, monkeypatch):
    from app.services.wiki.retrieve import blend_wiki_evidence

    _, _, wiki, _, _ = wiki_env
    _blend_pages(wiki)
    _upsert(wiki, path="materials/alpha.md", kind="material", title="Alpha Material",
            flags=["stale"])
    monkeypatch.setenv("FORMUNEX_WIKI_FLAGGED_MODE", "off")
    get_settings.cache_clear()
    merged, _ = blend_wiki_evidence("Alpha Material", [])
    assert any(ev.identifier == "wiki:materials/alpha.md" for ev in merged)


def test_hybrid_excludes_flagged_chunks(wiki_env, monkeypatch):
    from app.services.wiki.embed import wiki_source_id
    from app.services.hybrid_search import hybrid_search_scored

    src, chk, wiki, _, _ = wiki_env
    _blend_pages(wiki)
    _upsert(wiki, path="materials/alpha.md", kind="material", title="Alpha Material",
            flags=["stale"])
    flagged_sid = wiki_source_id("materials/alpha.md")
    clean_sid = wiki_source_id("materials/beta.md")
    chk.replace_for_source(flagged_sid, [
        {"text": "Alpha Material summary. Alpha Material is a coating resin.",
         "meta": {"wiki": True, "wiki_path": "materials/alpha.md",
                  "wiki_kind": "material"}},
    ])
    chk.replace_for_source(clean_sid, [
        {"text": "Beta Material summary. Beta Material is a coating resin.",
         "meta": {"wiki": True, "wiki_path": "materials/beta.md",
                  "wiki_kind": "material"}},
    ])
    monkeypatch.setenv("FORMUNEX_WIKI_FLAGGED_MODE", "exclude")
    get_settings.cache_clear()
    got = hybrid_search_scored("Alpha Material", top_k=10)
    got_ids = {c.chunk.source_id for c in got}
    assert flagged_sid not in got_ids
    assert clean_sid in got_ids
    # 对照：开关关时 flagged 块正常返回
    monkeypatch.setenv("FORMUNEX_WIKI_FLAGGED_MODE", "off")
    get_settings.cache_clear()
    got2 = hybrid_search_scored("Alpha Material", top_k=10)
    assert flagged_sid in {c.chunk.source_id for c in got2}
