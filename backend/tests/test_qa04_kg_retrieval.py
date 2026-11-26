"""QA-04（2026-11-26 审查第一波）：KG mention 标识规范化 + 分数归一化合并 + 去重键统一。

- mention 证据 identifier 改为规范 ``kb:{source_id}#c{ord}``（此前为
  ``kb:<uuid>``），与语义路同形，同一 chunk 两路只出一份。
- mention 分（0.7，trade +1.0、实施例 +0.5，最高 2.2）与语义分
  （约 0–1）各自按本轮最大值归一化后再加权合并（默认各 0.5），
  mention 不再恒压语义；``enum_part`` 阈值作用于归一化后分数。
- ``_apply_caps`` 去重键用规范 chunk 身份；最终输出按分数序。
- ``_chunk_id_from_identifier`` 对规范标识不再返回错值 ``"source#cOrd"``。
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.db.source_store import SourceStore
from app.domain.schemas import Evidence
from app.services import kb_index
from app.services.kg.entity_linker import link_source
from app.services.kg.retrieval import (
    _apply_caps,
    _chunk_id_from_identifier,
    _normalize_and_merge_pool,
    retrieve,
)

MD = """# 含锌专利

## 实施例

磷酸锌 fifteen parts，盐雾 720 h。CAS 7779-90-0。
"""


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_KG_ENABLED", "true")
    monkeypatch.setenv("FORMUNEX_KB_V2_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.entity_store as entity_store_mod
    import app.db.source_store as source_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/kg_qa04.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    ent = EntityStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(entity_store_mod, "_store", ent)

    sid = src.create(
        filename="zinc.md",
        title="含锌专利",
        source_kind="patent",
        full_text=MD,
        content_hash="hz",
    )
    kb_index.index_source(sid, MD, embed=False)
    link_source(sid)
    return sid


def _ev(ident, *, source="kb", rel=0.5, snippet="文本"):
    return Evidence(source=source, identifier=ident, title=f"T-{ident}",
                    snippet=snippet, relevance=rel)


def test_mention_identifier_normalized_and_deduped(corpus):
    """同一 chunk 的 mention 版与语义版只出一份，且标识为规范形。"""
    result = retrieve("列举含 7779-90-0 的实施例", mode="hybrid")
    assert result.evidence
    idents = [ev.identifier for ev in result.evidence]
    assert len(idents) == len(set(idents)), "同一 chunk 不得出两份"
    assert not any(
        i.startswith("kb:") and "#" not in i[3:] for i in idents
    ), "不得再产出 kb:<uuid> 形 mention 标识"
    assert any(i.startswith("kb:") and "#c" in i for i in idents)


def test_normalize_and_merge_no_channel_dominates():
    """单通道最优分归一化后相等；双通道高于单通道。"""
    mention_only = _ev("kb:s1#c0", rel=0.9)
    semantic_only = _ev("kb:s2#c0", rel=1.0)
    dual = _ev("kb:s3#c0", rel=1.0)
    entries = [
        ("mention", 2.2, mention_only, "uuid-1"),  # mention 本轮最高（trade+实施例）
        ("semantic", 0.3, mention_only, ""),  # 同 chunk 的语义版（低分）
        ("semantic", 1.0, semantic_only, ""),
        ("mention", 0.7, dual, "uuid-3"),
        ("semantic", 1.0, dual, ""),
    ]
    out = _normalize_and_merge_pool(entries, settings=get_settings())
    by_ident = {ev.identifier: s for s, ev, _ in out}
    assert len(out) == 3, "同一 chunk 两路应合并为一份"
    # mention-only 本轮最优：0.5*1.0 + 0.5*(0.3/1.0) = 0.65；
    # semantic-only 本轮最优：0.5*1.0 = 0.5；双通道最优：0.5*(0.7/2.2)+0.5*1.0 ≈ 0.66
    assert by_ident["kb:s1#c0"] == pytest.approx(0.65)
    assert by_ident["kb:s2#c0"] == pytest.approx(0.5)
    assert by_ident["kb:s3#c0"] == pytest.approx(0.5 * 0.7 / 2.2 + 0.5)
    # 旧行为下 mention 2.2 恒压语义 1.0；归一化后双通道才居首，单通道不再恒压。
    assert by_ident["kb:s3#c0"] > by_ident["kb:s1#c0"] > by_ident["kb:s2#c0"]


def test_normalize_and_merge_pre_pinned_on_top():
    pre = _ev("seed:1", source="seed", rel=0.5)
    sem = _ev("kb:s1#c0", rel=1.0)
    out = _normalize_and_merge_pool(
        [("pre", 1.0, pre, ""), ("semantic", 1.0, sem, "")],
        settings=get_settings(),
    )
    assert out[0][1].identifier == "seed:1"
    assert out[0][0] == 1.0


def test_apply_caps_dedupes_by_canonical_chunk_key():
    """同一 chunk 的 mention 版与语义版（同规范标识）只留一份。"""
    mention = _ev("kb:s1#c0", rel=0.9)
    semantic = _ev("kb:s1#c0", rel=0.8)
    other = _ev("kb:s2#c0", rel=0.7)
    final, _n, _t = _apply_caps(
        [(0.9, mention, "uuid-1"), (0.8, semantic, ""), (0.7, other, "uuid-2")],
        llm_cap=5, max_sources=5, trade_only=False, inherit_enabled=False,
    )
    assert [e.identifier for e, _ in final] == ["kb:s1#c0", "kb:s2#c0"]


def test_apply_caps_final_order_is_score_order():
    """轮询只决定取哪些，最终输出按分数序（旧轮询序 0.9/0.5/0.89 会被纠正）。"""
    a1 = _ev("kb:sA#c0", source="srcA", rel=0.9)
    a2 = _ev("kb:sA#c1", source="srcA", rel=0.89)
    b1 = _ev("kb:sB#c0", source="srcB", rel=0.5)
    final, _n, _t = _apply_caps(
        [(0.9, a1, "u1"), (0.89, a2, "u2"), (0.5, b1, "u3")],
        llm_cap=5, max_sources=5, trade_only=False, inherit_enabled=False,
    )
    assert [e.identifier for e, _ in final] == ["kb:sA#c0", "kb:sA#c1", "kb:sB#c0"]


def test_chunk_id_from_identifier_canonical_returns_empty():
    assert _chunk_id_from_identifier("kb:s1#c3") == ""
    assert _chunk_id_from_identifier("kb:some-uuid-value") == "some-uuid-value"
    assert _chunk_id_from_identifier("wiki:page/x") == ""
    assert _chunk_id_from_identifier("") == ""
