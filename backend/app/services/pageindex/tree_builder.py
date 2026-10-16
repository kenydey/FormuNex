"""Build a PageIndex table-of-contents tree for one ingested document.

No LLM is involved: the tree comes from what ingestion already recorded on
each chunk (``heading_path``, ``page_no`` / ``page_end``, ``block_type``), from
the parser's table sidecar (``TableAsset``: ``table_id``, ``page_no``,
``caption``), and from the textual markers patents and standards use instead
of real headings (权利要求书 / 具体实施方式 / 实施例 3 / 对比例 1 / 表 2 ...).

The JSON shape follows the open-source PageIndex tree (``node_id``, ``title``,
``summary``, page range, ``children``) and adds what cross-section reasoning
needs: ``label`` (a normalised anchor such as ``table:2`` or ``example:3``),
``table_ids`` and ``refs`` — edges from a node to the nodes its text cites
("见表 2", "如实施例 1 所述"). Those edges are how stage 2 walks from a
precursor definition to the recipe table to the salt-spray results when they
sit pages apart.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

BUILDER_VERSION = "1"

_NUM = r"(\d{1,3}|[一二三四五六七八九十]{1,3})"
_CN_DIGITS = {c: i for i, c in enumerate("零一二三四五六七八九", start=0)}

# Section markers (patents / standards / papers). Order matters only for readability.
_SECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("claims", re.compile(r"^\s*(?:权\s*利\s*要\s*求\s*书?|What is claimed is|Claims?)\s*[:：]?\s*$", re.I)),
    ("field", re.compile(r"^\s*(?:技术领域|Technical Field|Field of the Invention)\s*$", re.I)),
    ("background", re.compile(r"^\s*(?:背景技术|Background(?: of the Invention| Art)?)\s*$", re.I)),
    ("summary", re.compile(r"^\s*(?:发明内容|Summary(?: of the Invention)?)\s*$", re.I)),
    ("drawings", re.compile(r"^\s*(?:附图说明|Brief Description of (?:the )?Drawings)\s*$", re.I)),
    ("embodiments", re.compile(
        r"^\s*(?:具体实施方式|Detailed Description(?: of (?:the )?(?:Preferred )?Embodiments?)?|Examples)\s*$",
        re.I)),
    ("scope", re.compile(r"^\s*\d?\s*(?:范围|Scope)\s*$", re.I)),
    ("terms", re.compile(r"^\s*\d?\s*(?:术语和定义|Terms and definitions)\s*$", re.I)),
)
_EXAMPLE_RE = re.compile(rf"^\s*(?P<kind>实施例|对比例|比较例|Comparative Example|Example)\s*{_NUM}\b", re.I)
_TABLE_CAPTION_RE = re.compile(rf"^\s*(?:#+\s*)?(?P<kind>表|Table)\s*{_NUM}(?P<rest>.*)$", re.I)
_REF_RE = re.compile(
    rf"(?P<kind>表|Table|实施例|对比例|比较例|Comparative Example|Example)\s*{_NUM}", re.I
)


_PROSE_RE = re.compile(r"所示|表明|可见|可知|列出|说明|中的|shows?\b|indicates?\b|lists?\b|summari[sz]es\b", re.I)


def _is_caption(line: str) -> bool:
    """A caption is a short title line ("表 2 盐雾测试结果"), not prose that starts with a table mention."""
    return len(line) <= 60 and not line.endswith(("。", ".", "；", ";")) and not _PROSE_RE.search(line)


def _is_example_heading(line: str, m: re.Match[str]) -> bool:
    """"实施例1" / "Example 3: Primer" are headings; "实施例1 中性盐雾 720 h" is not."""
    rest = line[m.end():].strip(" :：.、\t")
    return len(line) <= 40 and (not rest or (len(rest) <= 20 and not re.search(r"\d", rest)))


def _cites_other_examples(caption: str, own_label: str | None) -> bool:
    labels = {_label(m.group("kind"), m.group(2)) for m in _REF_RE.finditer(caption)}
    labels = {lab for lab in labels if lab and not lab.startswith("table:")}
    return bool(labels - {own_label}) or bool(re.search(r"实施例\s*\d+\s*[-~～至到]\s*\d+", caption))


def _num(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    total = 0
    if "十" in token:
        tens, _, ones = token.partition("十")
        total = (_CN_DIGITS.get(tens, 1) if tens else 1) * 10
        token = ones
    for ch in token:
        if ch not in _CN_DIGITS:
            return None
        total += _CN_DIGITS[ch]
    return total or None


def _label(kind: str, number: str) -> str | None:
    n = _num(number)
    if n is None:
        return None
    k = kind.lower()
    if k in ("表", "table"):
        return f"table:{n}"
    if k in ("对比例", "比较例", "comparative example"):
        return f"comparative:{n}"
    return f"example:{n}"


@dataclass
class TocNode:
    node_id: str
    title: str
    kind: str  # root | heading | section | example | table
    label: str | None = None
    summary: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    chunk_ids: list[str] = field(default_factory=list)
    table_ids: list[str] = field(default_factory=list)
    refs: list[dict] = field(default_factory=list)
    children: list[TocNode] = field(default_factory=list)

    def cover(self, start: int | None, end: int | None) -> None:
        if start is None:
            return
        end = end if end is not None else start
        self.page_start = start if self.page_start is None else min(self.page_start, start)
        self.page_end = end if self.page_end is None else max(self.page_end, end)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "title": self.title,
            "kind": self.kind,
            "label": self.label,
            "summary": self.summary,
            "page_range": [self.page_start, self.page_end] if self.page_start is not None else None,
            "chunk_ids": list(self.chunk_ids),
            "table_ids": list(self.table_ids),
            "refs": list(self.refs),
            "children": [c.to_dict() for c in self.children],
        }


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


class _Builder:
    def __init__(self, title: str) -> None:
        self._seq = 0
        self.root = self._new(title or "(untitled)", "root")
        self.by_label: dict[str, TocNode] = {}
        self.chunk_node: dict[str, TocNode] = {}
        self.chunk_text: dict[str, str] = {}

    def _new(self, title: str, kind: str, label: str | None = None) -> TocNode:
        node = TocNode(node_id=f"{self._seq:04d}", title=title.strip()[:200], kind=kind, label=label)
        self._seq += 1
        return node

    def child(self, parent: TocNode, title: str, kind: str, label: str | None = None) -> TocNode:
        # Re-use an existing child with the same title (a heading split over several chunks).
        for c in parent.children:
            if c.title == title.strip()[:200] and c.kind == kind:
                return c
        if label and label in self.by_label:
            return self.by_label[label]
        node = self._new(title, kind, label)
        parent.children.append(node)
        if label:
            self.by_label[label] = node
        return node


def build_toc_tree(
    chunks: Iterable[Any],
    *,
    title: str = "",
    tables: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Return ``{"tree": <root dict>, "stats": {...}, "doc_kind": ...}``.

    *chunks* are ``DocumentChunk`` rows or dicts with ``id``, ``ord``, ``text``,
    ``heading_path``, ``page_no``, ``page_end``, ``block_type``. *tables* are
    ``TableAsset`` objects or dicts with ``table_id``, ``page_no``, ``caption``.
    """
    rows = sorted(chunks, key=lambda c: (_get(c, "ord", 0) or 0))
    b = _Builder(title)
    section: TocNode | None = None
    example: TocNode | None = None
    table: TocNode | None = None
    full_text: list[str] = []

    for c in rows:
        cid = str(_get(c, "id"))
        text = _get(c, "text", "") or ""
        page = _get(c, "page_no")
        page_end = _get(c, "page_end") or page
        block_type = _get(c, "block_type") or ""
        b.chunk_text[cid] = text
        full_text.append(text[:400])

        heading_path = (_get(c, "heading_path", "") or "").strip()
        if heading_path:
            parts = [p.strip() for p in heading_path.split(" > ") if p.strip()]
            # The patent chunker promotes bare "实施例 N" lines to headings; those belong under
            # the textual section they appear in (具体实施方式), not at the document root.
            example_only = bool(parts) and bool(_EXAMPLE_RE.match(parts[0]))
            container = (section or b.root) if example_only else b.root
            for part in parts:
                kind, label = "heading", None
                m = _EXAMPLE_RE.match(part)
                if m:
                    kind, label = "example", _label(m.group("kind"), m.group(2))
                container = b.child(container, part, kind, label)
            if not example_only:
                section = None
            example = None
        else:
            container = example or section or b.root
        # A table body that runs on into this chunk stays with its caption.
        target = table if (table is not None and block_type == "table") else container

        # Textual markers inside the chunk start new structural nodes from that line on.
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or len(stripped) > 120:
                continue
            if not heading_path:
                if any(pat.match(stripped) for _k, pat in _SECTION_PATTERNS):
                    section = container = target = b.child(b.root, stripped, "section")
                    example = table = None
                    continue
                m = _EXAMPLE_RE.match(stripped)
                # Inside a table, "实施例一 中性盐雾 720 h" is a row, not a heading.
                if m and target.kind != "table" and _is_example_heading(stripped, m):
                    label = _label(m.group("kind"), m.group(2))
                    example = container = target = b.child(section or b.root, stripped, "example", label)
                    table = None
                    continue
            m = _TABLE_CAPTION_RE.match(stripped)
            if m and _is_caption(stripped):
                label = _label(m.group("kind"), m.group(2))
                if label:
                    # A caption naming other examples ("表2 实施例1-3及对比例1的性能") summarises
                    # several of them: it belongs to the section, not the example it follows.
                    host = container
                    if container.kind == "example" and _cites_other_examples(stripped, container.label):
                        host = section or b.root
                    table = target = b.child(host, stripped, "table", label)
        if target is not table:
            table = None

        target.chunk_ids.append(cid)
        b.chunk_node[cid] = target
        target.cover(page, page_end)

    # Parser table sidecar: attach each table to its caption node, or to the deepest node on its page.
    for t in tables or []:
        tid = str(_get(t, "table_id", "") or "")
        page = _get(t, "page_no")
        caption = (_get(t, "caption", "") or "").strip()
        m = _TABLE_CAPTION_RE.match(caption) if caption else None
        label = _label(m.group("kind"), m.group(2)) if m else None
        node = b.by_label.get(label) if label else None
        if node is None:
            host = _deepest_on_page(b.root, page) or b.root
            node = b.child(host, caption or f"Table p.{page}", "table", label)
        if tid and tid not in node.table_ids:
            node.table_ids.append(tid)
        node.cover(page, page)

    _propagate_pages(b.root)
    _resolve_refs(b)
    stats = _stats(b.root)
    return {"tree": b.root.to_dict(), "stats": stats, "doc_kind": _doc_kind(title, "\n".join(full_text))}


def _deepest_on_page(node: TocNode, page: int | None) -> TocNode | None:
    if page is None:
        return None
    best = None
    if node.page_start is not None and node.page_start <= page <= (node.page_end or node.page_start):
        best = node
    for c in node.children:
        hit = _deepest_on_page(c, page)
        if hit is not None and hit.kind != "table":
            best = hit
    return best


def _propagate_pages(node: TocNode) -> None:
    for c in node.children:
        _propagate_pages(c)
        node.cover(c.page_start, c.page_end)


def _iter(node: TocNode):
    yield node
    for c in node.children:
        yield from _iter(c)


def _resolve_refs(b: _Builder) -> None:
    for node in _iter(b.root):
        text = "\n".join(b.chunk_text.get(cid, "") for cid in node.chunk_ids)
        seen: set[str] = set()
        for m in _REF_RE.finditer(text):
            label = _label(m.group("kind"), m.group(2))
            target = b.by_label.get(label) if label else None
            if target is None or target is node or label in seen:
                continue
            seen.add(label)
            node.refs.append({"to": target.node_id, "label": label, "text": m.group(0)})


def _stats(root: TocNode) -> dict[str, int]:
    nodes = list(_iter(root))

    def depth(n: TocNode) -> int:
        return 1 + max((depth(c) for c in n.children), default=0)

    pages = [p for n in nodes for p in (n.page_start, n.page_end) if p is not None]
    return {
        "node_count": len(nodes),
        "max_depth": depth(root),
        "page_count": (max(pages) - min(pages) + 1) if pages else 0,
        "table_nodes": sum(1 for n in nodes if n.kind == "table"),
        "ref_edges": sum(len(n.refs) for n in nodes),
    }


def _doc_kind(title: str, sample: str) -> str:
    head = f"{title}\n{sample[:20000]}"
    if re.search(r"\bGB(?:/T|/Z)?\s*\d{3,5}|\bHG/T\s*\d|\bISO\s*\d{3,5}", title or ""):
        return "standard"
    if re.search(r"权利要求|What is claimed|实施例\s*\d|Comparative Example|专利", head) or (
        re.search(r"(?m)^\s*Claims?\s*$", head) and re.search(r"\bExample\s+\d", head)
    ):
        return "patent"
    if re.search(r"\bAbstract\b|摘\s*要|References|参考文献|\bDOI\b", head, re.I):
        return "paper"
    return "generic"


# --------------------------------------------------------------------------- tree helpers


def iter_nodes(tree: dict[str, Any]):
    """Depth-first iteration over a stored tree dict."""
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.get("children") or []))


def index_nodes(tree: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {n["node_id"]: n for n in iter_nodes(tree)}


def parent_map(tree: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for n in iter_nodes(tree):
        for c in n.get("children") or []:
            out[c["node_id"]] = n["node_id"]
    return out


def outline(tree: dict[str, Any], *, max_nodes: int = 400) -> str:
    """Compact indented outline (titles, pages, anchors, refs) for an LLM prompt — no body text."""
    lines: list[str] = []

    def walk(node: dict[str, Any], depth: int) -> None:
        if len(lines) >= max_nodes:
            return
        pr = node.get("page_range") or [None, None]
        pages = f"p{pr[0]}-{pr[1]}" if pr[0] is not None else "p?"
        refs = ",".join(r["label"] for r in node.get("refs") or [])
        bits = [f"[{node['node_id']}]", node.get("title") or "", f"({pages})"]
        if node.get("label"):
            bits.append(f"<{node['label']}>")
        if refs:
            bits.append(f"refs:{refs}")
        if node.get("summary"):
            bits.append(f"— {node['summary']}")
        lines.append("  " * depth + " ".join(bits))
        for c in node.get("children") or []:
            walk(c, depth + 1)

    walk(tree, 0)
    return "\n".join(lines)
