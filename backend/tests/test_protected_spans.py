"""WeKnora 第一批 2.6：受保护 span（反应式 / SMILES 独立行）与 parent_key 测试。"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.services.chunking import (
    Chunk,
    _is_protected_line,
    _split_blocks,
    chunk_markdown,
)

REACTION = "CH3COOH + NaOH -> CH3COONa + H2O"
SMILES = "CC(C)(C)c1ccc(CC(=O)O)cc1"  # 24 字符，含环数字与分支括号


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_protected_line_matchers():
    assert _is_protected_line(REACTION) is True
    assert _is_protected_line("2 H2 + O2 -> 2 H2O") is True
    assert _is_protected_line("Fe + CuSO4 => FeSO4 + Cu") is True
    assert _is_protected_line(SMILES) is True
    # 反例：纯文本箭头、短 token、带空白的普通句、URL。
    assert _is_protected_line("流程 → 下一步") is False
    assert _is_protected_line("A + B -> C + D") is False
    assert _is_protected_line("NaCl") is False
    assert _is_protected_line("H2O") is False
    assert _is_protected_line("这是一个很长的普通句子，包含中文标点，不应被保护。") is False
    assert _is_protected_line("see Figure 2(a) for details on the coating process") is False
    assert _is_protected_line("https://example.com/very/long/path12345") is False


def test_split_blocks_protection_only_when_enabled():
    body = f"前文行\n{REACTION}\n后文行"
    off = _split_blocks(body)
    assert off == [body]
    on = _split_blocks(body, protected_spans=True)
    assert on == ["前文行", REACTION, "后文行"]
    smiles_body = f"前文行\n{SMILES}\n后文行"
    assert _split_blocks(smiles_body, protected_spans=True) == [
        "前文行", SMILES, "后文行",
    ]


def test_ordinary_sentences_never_split():
    # 误判样本集：普通句（中 / 英 / URL / 纯文本箭头）无空白行分隔时
    # 仍为一整块，即误判率为 0（子方案护栏 < 1%）。
    sentences = [
        "这是一个很长的普通句子，包含中文标点，不应被保护。",
        "The coating process improves corrosion resistance significantly.",
        "see Figure 2(a) for details on the coating process",
        "流程 → 下一步",
        "https://example.com/very/long/path12345",
    ]
    body = "\n".join(sentences)
    assert _split_blocks(body, protected_spans=True) == [body]


def test_chunk_markdown_switch_off_matches_old_output():
    md = f"# 标题\n\n前文行\n{REACTION}\n后文行\n"
    chunks = chunk_markdown(md, protected_spans=False)
    assert len(chunks) == 1
    assert chunks[0].text == f"前文行\n{REACTION}\n后文行"
    assert chunks[0].parent_key is None


def test_chunk_markdown_protected_line_kept_whole():
    # 累积文本用 \n\n 连接、与原文单 \n 不符时 verbatim 回退按 block
    # 逐个发射——受保护行因此独立成块、整行不被切断。
    md = f"# 标题\n\n前文行\n{REACTION}\n后文行\n"
    chunks = chunk_markdown(md, max_chars=40, protected_spans=True)
    assert [c.text for c in chunks] == ["前文行", REACTION, "后文行"]


def test_long_protected_line_hard_cut_shares_parent_key():
    long_smiles = "CC(C)(C)c1ccc(CC(=O)O)cc1CC(C)(C)c1ccc(CC(=O)O)cc1"
    assert len(long_smiles) == 50
    md = f"# 标题\n\n{long_smiles}\n"
    chunks = chunk_markdown(md, max_chars=30, overlap=0, protected_spans=True)
    assert len(chunks) == 2
    assert "".join(c.text for c in chunks) == long_smiles
    assert chunks[0].parent_key == chunks[1].parent_key
    # 硬切前原子块在原文中的偏移区间：标题 "# 标题\n\n" 占 6 字符。
    assert chunks[0].parent_key == "6-56"


def test_chunk_default_parent_key_none_and_long_title_no_crash():
    assert Chunk("text").parent_key is None
    md = "# " + "长" * 150 + "\n\n正文内容\n"
    chunks = chunk_markdown(md)
    assert chunks
    assert len(chunks[0].heading_path) <= 120


def test_store_ignores_parent_key(tmp_path):
    from app.db.chunk_store import ChunkStore
    from app.db.database import Base, make_engine, make_session_factory
    from app.db.source_store import SourceStore

    engine = make_engine(f"sqlite:///{tmp_path}/store26.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    sid = src.create(filename="d.md", title="t", source_kind="local",
                     full_text="x", content_hash="h26")
    n = chk.replace_for_source(sid, [{
        "text": "CC(C)(C)c1ccc(CC(=O)O)cc1", "heading_path": "",
        "parent_key": "6-54", "block_type": "text",
    }])
    assert n == 1
    rows = chk.get_by_source(sid)
    assert len(rows) == 1
