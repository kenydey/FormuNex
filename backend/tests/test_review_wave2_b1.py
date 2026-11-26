"""FormuNex 新一轮全面审查（2026-11-26）修复第二波 · B1 知识库域定向测试。

覆盖报告 §4 第 1 批 + 第 2 批（P2，不依赖决策）：
- B3 页码 provenance：Chunk.page_no_source、meta 持久化、树双口径、
  引用"约 p.N"、存量回填；
- B5 专利号/IPC 拒绝规则（chem_extract + entity_linker）；
- B4 metric 字符集 + _METRIC_ALIASES；
- B7 L2 审计确定性抽样 + 跨语言通道 + 归档源排除（L1/L2）；
- B6 重嵌收尾失效信号（bump generation + kb_ann.invalidate() +
  清空 L2 + ann_invalidated）；
- B10 删源/lint 分页循环 + lint 健康统计；
- B9 健康检查暴露 Wiki 治理三开关（/api/kb/stats）。

跳过项见汇报：B5 存量清理（§11.4 #3）、lint beat 周期（§11.4 #6）、
log.md 来源前缀（wiki_store.py，不在 B1 归属内）。
"""
from __future__ import annotations

import uuid

import numpy as np
import pytest

from app.config import get_settings


# ── 隔离夹具（复用 test_batch6_kb_invalidation 的建店方式） ──────────────


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    # 进程内全局状态复位，避免漏到相邻测试。
    from app.services import kb_dedup
    from app.services.wiki import lint as lint_mod

    kb_dedup._L2_MAT_CACHE.clear()
    lint_mod._LAST_LINT = None


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
    engine = make_engine(f"sqlite:///{tmp_path}/kb-b1.db")
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


def _refresh_settings(monkeypatch, **env_vars):
    for k, v in env_vars.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


def _seed_chunk(factory, source_id, text, *, lang="en", vec=None, chunk_id=None, meta=None, ord_no=0):
    """Direct DocumentChunk seed (orphan unless a SourceDocument row exists)."""
    from app.db.models import DocumentChunk
    from app.services.kb_dedup import chunk_dedup_key

    blob = None
    if vec is not None:
        blob = np.asarray(vec, dtype="<f4").tobytes()
    cid = chunk_id or str(uuid.uuid4())
    with factory() as s:
        s.add(
            DocumentChunk(
                id=cid,
                source_id=source_id,
                ord=ord_no,
                text=text,
                lang=lang,
                embedding_blob=blob,
                dedup_key=chunk_dedup_key(text, "", None),
                meta=meta,
            )
        )
        s.commit()
    return cid


def _seed_source(factory, source_id, *, kind="local", archived=False):
    from app.db.models import SourceDocument

    with factory() as s:
        s.add(
            SourceDocument(
                id=source_id,
                filename=f"{source_id}.txt",
                title=source_id,
                source_kind=kind,
                content_hash=f"hash-{source_id}",
                archived=archived,
            )
        )
        s.commit()


# ── B3：页码 provenance ────────────────────────────────────────────────────


def test_b3_real_marker_stamps_real_and_approx_marker_stamps_approx():
    from app.services.chunking import chunk_markdown

    body = "zinc phosphate coating " * 12  # >30 chars per chunk
    real = chunk_markdown(f"<!-- page:1 -->\n\n{body}\n\n<!-- page:2 -->\n\n{body}")
    assert real
    assert all(c.page_no is not None for c in real)
    assert {c.page_no_source for c in real} == {"real"}

    approx = chunk_markdown(f"<!-- page:1 approx -->\n\n{body}")
    assert approx
    assert {c.page_no_source for c in approx} == {"approx"}
    assert {c.page_no for c in approx} == {1}


def test_b3_assign_approximate_marks_approx():
    from app.services.chunking import (
        assign_approximate_page_numbers,
        chunk_markdown,
    )

    chunks = chunk_markdown("plain anticorrosion text. " * 400)
    assert chunks and all(c.page_no is None for c in chunks)
    assign_approximate_page_numbers(chunks)
    assert all(c.page_no is not None for c in chunks)
    assert {c.page_no_source for c in chunks} == {"approx"}


def test_b3_prepare_chunk_rows_persists_approx_in_meta(env):
    from app.services.kb_index import prepare_chunk_rows

    approx_rows = prepare_chunk_rows(
        " ".join(
            f"Anticorrosion coating paragraph {i} discusses zinc phosphate "
            f"primer performance under salt spray exposure."
            for i in range(120)
        ),
        "src-approx",
        embed=False,
    )
    assert approx_rows
    assert all(r["page_no_source"] == "approx" for r in approx_rows)
    assert all((r["meta"] or {}).get("page_no_source") == "approx" for r in approx_rows)

    body = "zinc phosphate coating primer " * 8
    real_rows = prepare_chunk_rows(
        f"## Section\n\n<!-- page:1 -->\n\n{body}\n\n<!-- page:2 -->\n\n{body}",
        "src-real",
        embed=False,
    )
    assert real_rows
    assert all(r["page_no_source"] == "real" for r in real_rows)
    assert all("page_no_source" not in (r["meta"] or {}) for r in real_rows)


def test_b3_citation_renders_approx_page(env):
    from types import SimpleNamespace

    from app.services.kb_index import _chunk_to_evidence, format_inline_citation

    def _chunk(page_no, approx):
        return SimpleNamespace(
            source_id="src-1",
            heading_path="",
            page_no=page_no,
            page_end=None,
            paragraph_idx=0,
            block_type="text",
            text="zinc phosphate primer coating text",
            ord=0,
            meta={"page_no_source": "approx"} if approx else {},
        )

    ev_approx = _chunk_to_evidence(_chunk(3, True), {"src-1": {"title": "Doc"}}, 0.9)
    assert "约P3" in ev_approx.title
    assert format_inline_citation(ev_approx, 1) == "[1] Doc · 约P3 · 约第3页"

    ev_real = _chunk_to_evidence(_chunk(3, False), {"src-1": {"title": "Doc"}}, 0.9)
    assert "约P" not in ev_real.title and "P3" in ev_real.title
    assert format_inline_citation(ev_real, 1) == "[1] Doc · P3 · 第3页"


def test_b3_tree_stats_dual_caliber():
    from app.services.pageindex.tree_builder import build_toc_tree

    def _c(cid, page, approx):
        return {
            "id": cid,
            "ord": int(cid[-1]),
            "text": f"chunk {cid} text",
            "heading_path": "",
            "page_no": page,
            "page_end": None,
            "block_type": "text",
            "meta": {"page_no_source": "approx"} if approx else {},
        }

    built = build_toc_tree(
        [_c("c1", 1, True), _c("c2", 2, True), _c("c3", 2, True), _c("c4", 5, True)],
        title="Patent",
    )
    assert built["stats"]["page_count"] == 5  # 跨度口径不变（max-min+1）
    assert built["stats"]["page_count_distinct"] == 3
    assert built["stats"]["page_count_approx"] is True

    built_real = build_toc_tree([_c("c1", 1, False), _c("c2", 2, False)], title="Doc")
    assert built_real["stats"]["page_count_distinct"] == 2
    assert built_real["stats"]["page_count_approx"] is False


def test_b3_backfill_marks_stock_chunks(env):
    from app.services.kb_index import backfill_page_no_source

    factory = env["factory"]
    _seed_source(factory, "src-normal", kind="local")
    _seed_source(factory, "src-wiki", kind="wiki")
    _seed_chunk(factory, "src-normal", "normal chunk one", chunk_id="n1", ord_no=0)
    _seed_chunk(factory, "src-normal", "normal chunk two", chunk_id="n2", ord_no=1)
    _seed_chunk(factory, "src-wiki", "wiki shadow chunk", chunk_id="w1", ord_no=0)
    _seed_chunk(
        factory, "src-normal", "already marked", chunk_id="n3", ord_no=2,
        meta={"page_no_source": "real"},
    )

    dry = backfill_page_no_source(dry_run=True)
    assert dry == {
        "scanned": 4,
        "marked_approx": 2,
        "marked_real_wiki": 1,
        "already_marked": 1,
    }

    counts = backfill_page_no_source()
    assert counts == dry
    from app.db.models import DocumentChunk

    with factory() as s:
        metas = {
            c.id: (c.meta or {}).get("page_no_source")
            for c in s.query(DocumentChunk).all()
        }
    assert metas == {
        "n1": "approx",
        "n2": "approx",
        "w1": "real",
        "n3": "real",
    }
    # 幂等：第二遍全部已标记
    assert backfill_page_no_source()["already_marked"] == 4


def test_b3_two_stage_gate_notes_approx(env, monkeypatch):
    from app.db.chunk_store import get_chunk_store
    from app.db.toc_tree_store import get_toc_tree_store
    from app.services.pageindex.tree_builder import BUILDER_VERSION
    from app.services.pageindex.two_stage import deepen_documents

    sid = "src-gate"
    get_chunk_store().replace_for_source(
        sid,
        [
            {
                "text": "zinc phosphate example text " * 6,
                "page_no": 5,
                "meta": {"page_no_source": "approx"},
            }
        ],
    )
    get_toc_tree_store().put(
        sid,
        {
            "tree": {"node_id": "0000", "title": "Doc", "kind": "root"},
            "stats": {"node_count": 1, "max_depth": 1, "page_count": 5},
            "doc_kind": "generic",
        },
        builder_version=BUILDER_VERSION,
    )
    (depth,) = deepen_documents("extract the example", [sid], use_llm=False)
    assert depth.mode == "stage1"
    assert "5 pages < 20" in depth.reason
    assert "(approx page count)" in depth.reason

    # 对照：真实页码不注明 approx
    sid2 = "src-gate-real"
    get_chunk_store().replace_for_source(
        sid2, [{"text": "zinc phosphate example text " * 6, "page_no": 5, "meta": {}}]
    )
    get_toc_tree_store().put(
        sid2,
        {
            "tree": {"node_id": "0000", "title": "Doc", "kind": "root"},
            "stats": {"node_count": 1, "max_depth": 1, "page_count": 5},
            "doc_kind": "generic",
        },
        builder_version=BUILDER_VERSION,
    )
    (depth2,) = deepen_documents("extract the example", [sid2], use_llm=False)
    assert "(approx page count)" not in depth2.reason


# ── B5：专利号/IPC 拒绝规则 ───────────────────────────────────────────────


def test_b5_patent_and_ipc_tokens_rejected_as_formulas():
    from app.services.chem_extract import extract_formulas, is_patent_or_ipc_token

    assert is_patent_or_ipc_token("CN100453576C")
    assert is_patent_or_ipc_token("CN102211430B")
    assert is_patent_or_ipc_token("US9982145B2")  # 数字游程 9982145（>3 位）
    assert is_patent_or_ipc_token("C08F")
    assert is_patent_or_ipc_token("C08K3")
    assert is_patent_or_ipc_token("C08K5")
    assert not is_patent_or_ipc_token("H2O")
    assert not is_patent_or_ipc_token("NaCl")
    assert not is_patent_or_ipc_token("C6H12O6")

    assert extract_formulas("CN100453576C C08F C08K3 US9982145B2") == []
    kept = extract_formulas("H2O and NaCl and Zn3(PO4)2 and C6H12O6 mixture")
    assert set(kept) == {"H2O", "NaCl", "Zn3(PO4)2", "C6H12O6"}


def test_b5_entity_linker_skips_patent_formula(env, monkeypatch):
    _refresh_settings(monkeypatch, FORMUNEX_KG_ENABLED="true")
    from app.db.chunk_store import get_chunk_store
    from app.db.entity_store import get_entity_store
    from app.db.models import KGMention
    from app.services.kg.entity_linker import link_source

    factory = env["factory"]
    sid = "src-link"
    get_chunk_store().replace_for_source(
        sid,
        [
            {
                "text": "patent reference table",
                "meta": {
                    "chem": [
                        {"type": "formula", "value": "CN100453576C"},
                        {"type": "formula", "value": "C08K3"},
                        {"type": "formula", "value": "H2O"},
                    ]
                },
            }
        ],
    )
    report = link_source(sid, force_entities=True)
    assert report is not None
    store = get_entity_store()
    assert store.get_entity("chem:formula:CN100453576C") is None
    assert store.get_entity("chem:formula:C08K3") is None
    assert store.get_entity("chem:formula:H2O") is not None
    with factory() as s:
        mentions = {
            m.entity_id for m in s.query(KGMention).filter(KGMention.source_id == sid).all()
        }
    assert mentions == {"chem:formula:H2O"}


# ── B4：metric 字符集 ─────────────────────────────────────────────────────


def test_b4_chinese_metrics_no_longer_collapse():
    from app.services.kg.entity_normalizer import (
        _METRIC_ALIASES,
        _metric_entity_id,
        resolve_metric_surface,
    )

    # 别名命中仍正常
    assert _metric_entity_id("耐盐雾性")[0] == "metric:salt_spray_nss"
    assert _metric_entity_id("耐水性")[0] == "metric:water_resistance"
    assert _metric_entity_id("耐冲击性")[0] == "metric:impact_resistance"
    assert _metric_entity_id("硬度")[0] == "metric:hardness"
    assert _metric_entity_id("光泽度")[0] == "metric:gloss"
    # 未收录中文指标：保留中文、互不相同（此前一律 metric:_）
    assert _metric_entity_id("耐碱性")[0] == "metric:耐碱性"
    assert _metric_entity_id("耐酸性")[0] == "metric:耐酸性"
    assert _metric_entity_id("耐碱性")[0] != _metric_entity_id("耐酸性")[0]
    assert _metric_entity_id("耐盐雾性")[0] != "metric:_"
    # 纯标点：unknown_metric:<sha1 前 8>，不同输入不同 id
    eid1, _ = _metric_entity_id("!!!")
    eid2, _ = _metric_entity_id("???")
    assert eid1.startswith("metric:unknown_metric:") and len(eid1.rsplit(":", 1)[1]) == 8
    assert eid1 != eid2
    # 别名表覆盖
    assert _METRIC_ALIASES["耐水性"] == "water_resistance"
    assert resolve_metric_surface("耐碱性").entity_id == "metric:耐碱性"


# ── B7：L2 审计确定性抽样 + 跨语言通道 + 归档排除 ────────────────────────


def test_b7_l2_audit_sampling_is_deterministic_stride(env, monkeypatch, caplog):
    """审计抽样：ORDER BY id 等步长取样，分母与步长记入日志。"""
    from app.services import kb_dedup
    from app.services.kb_dedup import dedupe_chunk_rows

    factory = env["factory"]
    # kb_near_dedup_audit_cap 不是 config 字段（getattr 默认 2000）；
    # 测试内以对象属性注入小 cap。
    settings = get_settings()
    object.__setattr__(settings, "kb_near_dedup_audit_cap", 3)
    v = [1.0, 0.0, 0.0, 0.0]
    for i in range(10):
        _seed_chunk(
            factory, "src-a", f"corpus chunk {i}", vec=v,
            chunk_id=f"seed-{i:02d}", ord_no=i,
        )
    with factory() as s, caplog.at_level("INFO", logger="app.services.kb_dedup"):
        kept = dedupe_chunk_rows(
            [{"text": "incoming", "heading_path": "", "lang": "en", "embedding": list(v)}],
            "src-b",
            s,
        )
    assert len(kept) == 1  # audit 模式只记日志，不丢弃
    assert any("stride=4 over 10 corpus rows" in r.message for r in caplog.records)
    cache_key = (0, "en", 3)
    assert cache_key in kb_dedup._L2_MAT_CACHE
    ids, _mat = kb_dedup._L2_MAT_CACHE[cache_key]
    assert ids == ["seed-00", "seed-04", "seed-08"]


def test_b7_l2_cross_lang_text_match_drops_in_enforce(env, monkeypatch):
    """中英同一归一化文本：enforce 下丢弃（向量通道只覆盖同语言）。"""
    _refresh_settings(monkeypatch, FORMUNEX_KB_NEAR_DEDUP_ENABLED="true")
    from app.services.kb_dedup import dedupe_chunk_rows

    factory = env["factory"]
    v = [1.0, 0.0, 0.0, 0.0]
    _seed_chunk(factory, "src-a", "Sodium chloride solution", lang="en", vec=v)
    with factory() as s:
        kept = dedupe_chunk_rows(
            [
                {
                    "text": "Sodium chloride solution",
                    # 标题不同 → L1 精确键不同，隔离出跨语言 L2 通道
                    "heading_path": "section",
                    "lang": "zh",
                    "embedding": list(v),
                },
                {
                    "text": "Totally different zh text",
                    "heading_path": "",
                    "lang": "zh",
                    "embedding": [0.0, 1.0, 0.0, 0.0],
                },
            ],
            "src-b",
            s,
        )
    assert [r["text"] for r in kept] == ["Totally different zh text"]


def test_b7_l2_cross_lang_audit_only_logs(env, monkeypatch, caplog):
    """audit 模式：跨语言文本命中只记日志，不丢弃。"""
    from app.services.kb_dedup import dedupe_chunk_rows

    factory = env["factory"]
    v = [1.0, 0.0, 0.0, 0.0]
    _seed_chunk(factory, "src-a", "Sodium chloride solution", lang="en", vec=v)
    with factory() as s, caplog.at_level("INFO", logger="app.services.kb_dedup"):
        kept = dedupe_chunk_rows(
            [
                {
                    "text": "Sodium chloride solution",
                    "heading_path": "section",
                    "lang": "zh",
                    "embedding": list(v),
                }
            ],
            "src-b",
            s,
        )
    assert len(kept) == 1
    assert any("cross-lang" in r.message for r in caplog.records)


def test_b7_archived_corpus_excluded_from_l1_and_l2(env, monkeypatch):
    """归档源 chunk 不进 L1/L2 语料：相同文本不再被判重复。"""
    _refresh_settings(monkeypatch, FORMUNEX_KB_NEAR_DEDUP_ENABLED="true")
    from app.services.kb_dedup import dedupe_chunk_rows

    factory = env["factory"]
    _seed_source(factory, "src-arch", kind="local", archived=True)
    _seed_chunk(factory, "src-arch", "Archived duplicate text.", vec=[1.0, 0.0, 0.0, 0.0])
    with factory() as s:
        kept = dedupe_chunk_rows(
            [
                {
                    "text": "Archived duplicate text.",
                    "heading_path": "",
                    "lang": "en",
                    "embedding": [1.0, 0.0, 0.0, 0.0],
                }
            ],
            "src-new",
            s,
        )
    assert len(kept) == 1

    # 对照：未归档源的相同文本仍被 L1 丢弃
    _seed_source(factory, "src-live", kind="local", archived=False)
    _seed_chunk(factory, "src-live", "Live duplicate text.")
    with factory() as s:
        kept2 = dedupe_chunk_rows(
            [{"text": "Live duplicate text.", "heading_path": "", "lang": "en"}],
            "src-new2",
            s,
        )
    assert kept2 == []


# ── B6：重嵌收尾失效信号 ──────────────────────────────────────────────────


def test_b6_finalize_reindex_pushes_invalidation_signals(env, monkeypatch):
    from app.db.chunk_store import get_chunk_store
    from app.services import kb_ann, kb_dedup
    from app.services.embedding_reindex import _finalize_reindex

    kb_dedup._L2_MAT_CACHE[("gen", "en", None)] = ([], None)
    assert kb_dedup.clear_l2_cache() == 1
    kb_dedup._L2_MAT_CACHE[("gen", "en", None)] = ([], None)

    gen_before = kb_ann._generation
    store_gen_before = get_chunk_store().generation
    result = _finalize_reindex(total=7, migrated=7, elapsed_s=1.5, backup="/tmp/x.bak")
    assert result["ann_invalidated"] is True
    assert result["generation_bumped"] is True
    assert result["l2_cache_cleared"] is True
    assert result["total"] == 7 and result["migrated"] == 7
    assert kb_ann._generation == gen_before + 1
    assert get_chunk_store().generation == store_gen_before + 1
    assert kb_dedup._L2_MAT_CACHE == {}
    assert kb_ann._buckets == {}


def test_b6_reindex_for_model_rejects_unknown_model_and_missing_db(tmp_path):
    from app.services.embedding_reindex import reindex_for_model

    db = tmp_path / "empty.db"
    db.write_bytes(b"")
    with pytest.raises(ValueError, match="未知模型维度"):
        reindex_for_model("no/such-model", db_path=db)
    with pytest.raises(FileNotFoundError):
        reindex_for_model(
            "sentence-transformers/all-MiniLM-L6-v2",
            db_path=tmp_path / "missing.db",
        )


# ── B10：删源/lint 分页循环 + 健康统计 ───────────────────────────────────


def test_b10_strip_wiki_source_ids_paginates_past_500(env):
    from app.db.wiki_store import get_wiki_store
    from app.services.kb_delete import _strip_wiki_source_ids

    wiki = get_wiki_store()
    sid = "src-doomed"
    # 目标页先建（最旧），再建 510 个填充页 → 目标页在 updated_at desc
    # 排序下落到 500 位之后；旧实现单次 limit=500 扫不到。
    for i in range(3):
        wiki.upsert_page(
            path=f"targets/t{i}.md",
            kind="page",
            title=f"Target {i}",
            norm_key=f"t{i}",
            entity_id=None,
            markdown=f"# Target {i}\n\nbody\n",
            source_ids=[sid, "other"],
        )
    for i in range(510):
        wiki.upsert_page(
            path=f"filler/f{i}.md",
            kind="page",
            title=f"Filler {i}",
            norm_key=f"f{i}",
            entity_id=None,
            markdown=f"# Filler {i}\n\nbody\n",
            source_ids=["other"],
        )
    # 把目标页的 updated_at 钉到过去：list_pages 按 updated_at desc 排序，
    # 同秒建页的时间戳并列会让偏移位置不确定；钉死后目标页必在 500 位之后。
    from datetime import datetime

    from app.db.models import WikiPage

    with env["factory"]() as s:
        s.query(WikiPage).filter(WikiPage.path.like("targets/%")).update(
            {WikiPage.updated_at: datetime(2020, 1, 1)}
        )
        s.commit()
    assert _strip_wiki_source_ids(sid) == 3
    for i in range(3):
        row = wiki.get_by_path(f"targets/t{i}.md")
        assert row is not None and list(row.source_ids or []) == ["other"]
        assert "stale" not in (row.flags or [])


def test_b10_run_lint_pass_scans_whole_wiki(env):
    from app.db.wiki_store import get_wiki_store
    from app.services.wiki.lint import last_lint_summary, run_lint_pass

    wiki = get_wiki_store()
    wiki.upsert_page(
        path="old/broken.md",
        kind="page",
        title="Old broken",
        norm_key="old-broken",
        entity_id=None,
        markdown="# Old\n\nSee [[missing-target-xyz]] for details.\n",
        source_ids=[],
    )
    for i in range(249):
        wiki.upsert_page(
            path=f"pages/p{i}.md",
            kind="page",
            title=f"Page {i}",
            norm_key=f"p{i}",
            entity_id=None,
            markdown=f"# Page {i}\n\nBody text here.\n",
            source_ids=[],
        )
    summary = run_lint_pass()
    assert summary["scanned"] == 250
    assert summary["total_pages"] == 250
    assert "broken" in summary["results"]["old/broken.md"]
    assert summary["orphan_count"] > 0
    assert last_lint_summary()["scanned"] == 250
    assert last_lint_summary()["total_pages"] == 250


def test_b9_stats_exposes_wiki_governance_and_lint(env):
    from app.api.kb import KBStats
    from app.db.wiki_store import get_wiki_store
    from app.services import kb_index
    from app.services.wiki.lint import run_lint_pass

    wiki = get_wiki_store()
    wiki.upsert_page(
        path="a/page.md",
        kind="page",
        title="A",
        norm_key="a",
        entity_id=None,
        markdown="# A\n\nBody.\n",
        source_ids=[],
    )
    run_lint_pass()
    stats = kb_index.kb_stats()
    assert stats["wiki_governance"] == {
        "wiki_drift_refresh_enabled": False,
        "wiki_okf_enabled": False,
        "kb_invalidation_enabled": False,
    }
    assert stats["wiki_lint"]["scanned"] == 1
    assert stats["wiki_lint"]["total_pages"] == 1
    # /api/kb/stats 的 response_model 接受新字段
    model = KBStats(**stats)
    assert model.wiki_governance["wiki_okf_enabled"] is False
    assert model.wiki_lint["scanned"] == 1
