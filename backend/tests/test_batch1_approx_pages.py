"""批 1-1：无页码 marker 文本的近似页码回填（写入侧）。

覆盖：
- ``assign_approximate_page_numbers``：全空才补、带真实 marker 的不动、
  单页 chunk ``page_end`` 保持 None、跨页 chunk 记 ``page_end``、
  无偏移时退化为累计字符游标。
- ``index_source``：无 marker 长文入库后 chunk 带近似页码且树 page_count>0；
  带 marker 的文本页码原样保留。
- ``embed_wiki_page``：Wiki 影子 chunk 页码恒为 1。
"""
from __future__ import annotations

import hashlib

import pytest

from app.services.chunking import (
    APPROX_CHARS_PER_PAGE,
    Chunk,
    assign_approximate_page_numbers,
    chunk_markdown,
)


def _mk(text: str, page_no=None, start=None, end=None) -> Chunk:
    return Chunk(
        text,
        "",
        page_no,
        paragraph_idx=0,
        offset_start=start,
        offset_end=end,
    )


def test_approx_pages_from_offsets():
    chunks = [
        _mk("a" * 100, start=0, end=100),
        _mk("b" * 100, start=3000, end=3100),
        _mk("c" * 100, start=5900, end=6100),
    ]
    assign_approximate_page_numbers(chunks)
    assert [c.page_no for c in chunks] == [1, 2, 2]
    # 第 3 个 chunk 从 5900 到 6100，跨第 2–3 页，故 page_end=3。
    assert [c.page_end for c in chunks] == [None, None, 3]


def test_approx_pages_cross_page_chunk_gets_page_end():
    chunks = [_mk("x" * 100, start=2900, end=3100)]
    assign_approximate_page_numbers(chunks)
    assert chunks[0].page_no == 1
    assert chunks[0].page_end == 2


def test_approx_pages_never_overwrite_real_markers():
    chunks = [
        _mk("a" * 100, page_no=7, start=0, end=100),
        _mk("b" * 100, start=3000, end=3100),
    ]
    assign_approximate_page_numbers(chunks)
    assert chunks[0].page_no == 7
    assert chunks[1].page_no is None


def test_approx_pages_fall_back_to_cursor_without_offsets():
    chunks = [_mk("a" * 3000), _mk("b" * 100)]
    assign_approximate_page_numbers(chunks)
    assert [c.page_no for c in chunks] == [1, 2]


def test_approx_pages_single_short_chunk_is_page_one():
    chunks = [_mk("short summary")]
    assign_approximate_page_numbers(chunks)
    assert chunks[0].page_no == 1
    assert chunks[0].page_end is None


def test_approx_chars_per_page_constant():
    assert APPROX_CHARS_PER_PAGE == 3000


# ------------------------------------------------------------------ index_source


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    """Isolated KB (same shape as test_pageindex_two_stage_rag.kb)."""
    import app.db.chunk_store as chunk_store_mod
    import app.db.source_store as source_store_mod
    import app.db.toc_tree_store as toc_store_mod
    from app.config import get_settings
    from app.db.chunk_store import ChunkStore
    from app.db.database import Base, make_engine, make_session_factory
    from app.db.source_store import SourceStore
    from app.db.toc_tree_store import TocTreeStore

    for key, value in {
        "FORMUNEX_API_AUTH_ENABLED": "false",
        "FORMUNEX_KG_ENABLED": "false",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/kb.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    sources = SourceStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", sources)
    monkeypatch.setattr(chunk_store_mod, "_store", ChunkStore(factory))
    trees = TocTreeStore(factory)
    monkeypatch.setattr(toc_store_mod, "_store", trees)

    def ingest(title: str, text: str) -> str:
        from app.services import kb_index

        sid = sources.create(
            filename=f"{title}.txt",
            title=title,
            source_kind="patent",
            full_text=text,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        assert kb_index.index_source(sid, text, embed=False) > 0
        return sid

    yield {"ingest": ingest, "trees": trees}
    get_settings.cache_clear()


def test_index_source_markerless_long_text_gets_pages_and_tree_page_count(kb):
    # ~7500 字符无 marker 长文：近似页码应覆盖到第 3 页，树 page_count>0。
    para = "环氧树脂与聚酰胺固化剂在金属表面形成致密防腐涂层，耐盐雾性能优异。"
    text = "\n\n".join([para] * 200)
    assert len(text) > 2 * APPROX_CHARS_PER_PAGE
    sid = kb["ingest"]("无页码长文", text)
    from app.db.chunk_store import get_chunk_store

    chunks = get_chunk_store().get_by_source(sid)
    assert chunks
    assert all(c.page_no is not None for c in chunks)
    assert max(c.page_no for c in chunks) >= 2
    row = kb["trees"].get(sid)
    assert row is not None
    assert row.page_count > 0


def test_index_source_real_markers_untouched(kb):
    text = "<!-- page:5 -->\n\n" + "实测页码文本。" * 100
    sid = kb["ingest"]("带页码短文", text)
    from app.db.chunk_store import get_chunk_store

    chunks = get_chunk_store().get_by_source(sid)
    assert chunks
    assert {c.page_no for c in chunks} == {5}


# ------------------------------------------------------------------ wiki embed


@pytest.fixture()
def wiki_env(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.database as database_mod
    import app.db.source_store as source_store_mod
    import app.db.wiki_store as wiki_store_mod
    from app.config import get_settings
    from app.db.chunk_store import ChunkStore
    from app.db.database import Base, make_engine, make_session_factory
    from app.db.source_store import SourceStore
    from app.db.wiki_store import WikiStore

    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_WIKI_ENABLED", "true")
    monkeypatch.setenv("FORMUNEX_WIKI_EMBED_ENABLED", "true")
    get_settings.cache_clear()
    wiki_root = tmp_path / "wiki"
    wiki_root.mkdir()
    engine = make_engine(f"sqlite:///{tmp_path}/wiki.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    wiki = WikiStore(factory, root=wiki_root)
    monkeypatch.setattr(wiki_store_mod, "_store", wiki)
    chunks = ChunkStore(factory)
    monkeypatch.setattr(chunk_store_mod, "_store", chunks)
    monkeypatch.setattr(source_store_mod, "_store", SourceStore(factory))
    monkeypatch.setattr(database_mod, "default_session_factory", lambda: factory)
    get_settings.cache_clear()
    yield wiki, chunks
    get_settings.cache_clear()


def test_wiki_shadow_chunk_is_page_one(wiki_env):
    from app.services.wiki.embed import embed_wiki_page, wiki_source_id
    from app.services.wiki.schema import dump_page, system_path

    wiki, chunks = wiki_env
    path = system_path("epoxy-cure")
    wiki.upsert_page(
        path=path,
        kind="system",
        title="环氧固化体系",
        norm_key="epoxy-cure",
        entity_id="system:epoxy-cure",
        markdown=dump_page(
            kind="system",
            title="环氧固化体系",
            entity_id="system:epoxy-cure",
            norm_key="epoxy-cure",
            source_ids=["s1"],
            summary="胺固化环氧体系，注意催化剂窗口与暴聚风险。",
            evidence_blocks=["### Source `s1`", "催化剂建议 ≤1.5 wt%。"],
        ),
        source_ids=["s1"],
        replace_source_ids=True,
    )
    out = embed_wiki_page(path)
    assert out.get("ok") is True
    rows = chunks.get_by_source(wiki_source_id(path))
    assert len(rows) == 1
    assert rows[0].page_no == 1
