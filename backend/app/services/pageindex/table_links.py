"""Pair recipe tables with the performance tables that report on them.

In patents the composition table (表 1 配方) and the test-result table (表 2 盐雾 /
附着力) are often pages apart and tied together only by example numbers or an
explicit "见表 1". The ToC tree already holds those ties as ``refs`` and as the
example node a table sits under; this module turns them into reviewable pairs
for embodiment drafts.
"""
from __future__ import annotations

import re
from typing import Any

from .tree_builder import index_nodes, parent_map

_RECIPE_RE = re.compile(r"配方|组成|组分|原料|用量|配比|composition|formulation|ingredient|recipe", re.I)
_PERF_RE = re.compile(
    r"性能|测试|结果|检测|盐雾|附着力|硬度|耐|评价|performance|properties|results?|test|evaluation|corrosion",
    re.I,
)


def classify_table(title: str) -> str:
    recipe = bool(_RECIPE_RE.search(title or ""))
    perf = bool(_PERF_RE.search(title or ""))
    if recipe and not perf:
        return "recipe"
    if perf and not recipe:
        return "performance"
    return "other"


def _example_scope(node_id: str, nodes: dict, parents: dict) -> set[str]:
    """Example labels a table belongs to (its ancestor example) or cites."""
    labels: set[str] = set()
    cur = parents.get(node_id)
    while cur is not None:
        if (nodes[cur].get("label") or "").startswith(("example:", "comparative:")):
            labels.add(nodes[cur]["label"])
        cur = parents.get(cur)
    for r in nodes[node_id].get("refs") or []:
        if r["label"].startswith(("example:", "comparative:")):
            labels.add(r["label"])
    return labels


def link_recipe_performance(tree: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = index_nodes(tree)
    parents = parent_map(tree)
    tables = {nid: n for nid, n in nodes.items() if n.get("kind") == "table"}
    kinds = {nid: classify_table(n.get("title") or "") for nid, n in tables.items()}
    links: list[dict[str, Any]] = []
    for rid, recipe in tables.items():
        if kinds[rid] != "recipe":
            continue
        r_refs = {r["to"] for r in recipe.get("refs") or []}
        r_scope = _example_scope(rid, nodes, parents)
        for pid, perf in tables.items():
            if kinds[pid] != "performance":
                continue
            reasons = []
            if pid in r_refs:
                reasons.append(f"{recipe['title']} 引用 {perf['title']}")
            if rid in {r["to"] for r in perf.get("refs") or []}:
                reasons.append(f"{perf['title']} 引用 {recipe['title']}")
            shared = sorted(r_scope & _example_scope(pid, nodes, parents))
            if shared:
                reasons.append("共同实施例 " + ", ".join(shared))
            if reasons:
                links.append({
                    "recipe": {"node_id": rid, "title": recipe["title"], "page_range": recipe.get("page_range"),
                               "table_ids": recipe.get("table_ids") or []},
                    "performance": {"node_id": pid, "title": perf["title"], "page_range": perf.get("page_range"),
                                    "table_ids": perf.get("table_ids") or []},
                    "reasons": reasons,
                })
    return links
