"""PageIndex ToC tree builder."""
from __future__ import annotations

from app.services.pageindex.table_links import classify_table, link_recipe_performance
from app.services.pageindex.tree_builder import build_toc_tree, index_nodes, outline
from tests.pageindex_fixtures import patent_cn_basic, patent_cn_renumbered, patent_en


def _by_label(tree):
    return {n["label"]: n for n in index_nodes(tree).values() if n.get("label")}


def test_patent_sections_examples_and_tables():
    p = patent_cn_basic()
    built = build_toc_tree(p.chunks(), title=p.title)
    tree = built["tree"]
    titles = [c["title"] for c in tree["children"]]
    assert titles == ["权利要求书", "技术领域", "背景技术", "发明内容", "附图说明", "具体实施方式"]
    labels = _by_label(tree)
    assert labels["table:1"]["page_range"] == [10, 10]
    assert labels["table:2"]["page_range"] == [18, 18]
    assert labels["example:1"]["page_range"] == [9, 9]
    assert built["doc_kind"] == "patent"
    assert built["stats"]["page_count"] == 24 and built["stats"]["table_nodes"] == 2
    # Every chunk lands in exactly one node.
    assigned = [cid for n in index_nodes(tree).values() for cid in n["chunk_ids"]]
    assert sorted(assigned) == sorted(c["id"] for c in p.chunks())


def test_summary_table_hangs_off_the_section_not_the_last_example():
    tree = build_toc_tree(patent_cn_basic().chunks(), title="x")["tree"]
    nodes = index_nodes(tree)
    section = next(n for n in nodes.values() if n["title"] == "具体实施方式")
    assert "table:2" in {c.get("label") for c in section["children"]}


def test_cross_references_become_edges():
    tree = build_toc_tree(patent_cn_basic().chunks(), title="x")["tree"]
    labels = _by_label(tree)
    assert {r["label"] for r in labels["example:1"]["refs"]} == {"table:1"}
    assert {r["label"] for r in labels["table:2"]["refs"]} == {"example:1", "comparative:1"}


def test_table_rows_starting_with_example_names_are_not_headings():
    tree = build_toc_tree(patent_cn_renumbered().chunks(), title="x")["tree"]
    labels = _by_label(tree)
    assert labels["table:5"]["page_range"] == [21, 22]
    assert {r["label"] for r in labels["table:5"]["refs"]} == {"example:1", "example:2"}
    assert labels["example:2"]["title"] == "实施例二"  # Chinese numerals


def test_prose_starting_with_a_table_mention_is_not_a_caption():
    chunks = [
        {"id": "a", "ord": 0, "text": "表1 涂料配方", "page_no": 1, "block_type": "table"},
        {"id": "b", "ord": 1, "text": "表1中的数据表明涂层耐蚀性良好。", "page_no": 2},
        {"id": "c", "ord": 2, "text": "Table 1 shows the adhesion results.", "page_no": 3},
    ]
    tree = build_toc_tree(chunks, title="x")["tree"]
    tables = [n for n in index_nodes(tree).values() if n["kind"] == "table"]
    assert [t["title"] for t in tables] == ["表1 涂料配方"]
    assert tables[0]["chunk_ids"] == ["a"]


def test_markdown_heading_paths_build_the_hierarchy():
    chunks = [
        {"id": "1", "ord": 0, "text": "intro", "heading_path": "防腐底漆", "page_no": 1},
        {"id": "2", "ord": 1, "text": "环氧树脂…", "heading_path": "防腐底漆 > 实施例 1", "page_no": 2},
        {"id": "3", "ord": 2, "text": "表 2 盐雾结果\n720 h", "heading_path": "防腐底漆 > 实施例 1",
         "page_no": 3, "block_type": "table"},
        {"id": "4", "ord": 3, "text": "聚酰胺…", "heading_path": "防腐底漆 > 实施例 2", "page_no": 4},
    ]
    tree = build_toc_tree(chunks, title="doc")["tree"]
    top = tree["children"][0]
    assert top["title"] == "防腐底漆" and top["page_range"] == [1, 4]
    ex1, ex2 = top["children"]
    assert (ex1["label"], ex2["label"]) == ("example:1", "example:2")
    assert ex1["children"][0]["label"] == "table:2" and ex1["children"][0]["chunk_ids"] == ["3"]


def test_parser_table_sidecar_attaches_by_caption_or_page():
    chunks = [
        {"id": "1", "ord": 0, "text": "实施例1\n见表1。", "page_no": 3},
        {"id": "2", "ord": 1, "text": "表1 配方组成", "page_no": 4, "block_type": "table"},
    ]
    tables = [
        {"table_id": "src#p04-01", "page_no": 4, "caption": "表1 配方组成"},
        {"table_id": "src#p03-02", "page_no": 3, "caption": ""},
    ]
    tree = build_toc_tree(chunks, title="x", tables=tables)["tree"]
    labels = _by_label(tree)
    assert labels["table:1"]["table_ids"] == ["src#p04-01"]
    uncaptioned = next(n for n in index_nodes(tree).values() if n["table_ids"] == ["src#p03-02"])
    assert uncaptioned["title"] == "Table p.3"


def test_english_patent_and_outline():
    built = build_toc_tree(patent_en().chunks(), title="Silane coating")
    assert built["doc_kind"] == "patent"
    text = outline(built["tree"])
    assert "<table:1>" in text and "refs:example:1" in text
    assert "Precursor C is defined" not in text  # outlines carry structure, never body text


def test_doc_kind_for_standards_and_papers():
    assert build_toc_tree([{"id": "1", "ord": 0, "text": "1 范围"}], title="GB/T 1771-2007 色漆和清漆 耐中性盐雾性能的测定")[
        "doc_kind"] == "standard"
    assert build_toc_tree([{"id": "1", "ord": 0, "text": "Abstract\nWe study…\nReferences"}], title="x")[
        "doc_kind"] == "paper"


def test_empty_document():
    built = build_toc_tree([], title="empty")
    assert built["stats"] == {"node_count": 1, "max_depth": 1, "page_count": 0, "table_nodes": 0, "ref_edges": 0}


def test_recipe_performance_links():
    assert classify_table("表1 实施例1-3的配方组成（质量份）") == "recipe"
    assert classify_table("表5 处理后铝合金的耐蚀性能") == "performance"
    assert classify_table("表9 符号说明") == "other"
    for make, recipe, perf in ((patent_cn_basic, "table:1", "table:2"), (patent_cn_renumbered, "table:3", "table:5"),
                               (patent_en, "table:1", "table:2")):
        tree = build_toc_tree(make().chunks(), title="x")["tree"]
        labels = _by_label(tree)
        links = link_recipe_performance(tree)
        assert [(lk["recipe"]["node_id"], lk["performance"]["node_id"]) for lk in links] == [
            (labels[recipe]["node_id"], labels[perf]["node_id"])
        ]
        assert links[0]["reasons"]
