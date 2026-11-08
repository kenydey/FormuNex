"""Structure-aware chunking — Markdown headings and tables survive splitting.

``chunk_markdown`` is the shared chunker for every ingest path.  Behaviour:

* the document is first sectioned on ATX headings (``#`` … ``######``) only:
  Setext headings (``===`` / ``---`` underlines) and blockquote lines (``>``)
  are NOT structure — they stay ordinary text (P3-B33, documented rather
  than implemented: no caller relies on Setext/blockquote sectioning); each
  chunk remembers its heading path (``实施例 > 实施例 3``) so retrieval and
  citations can show *where* in the document a passage lives;
* Markdown tables (and fenced code blocks) are atomic — they are never split
  mid-row, even when that makes a chunk oversized, because a half table is
  worthless for formulation extraction;
* display-math blocks (``$$…$$``, ``\\[…\\]``, ``\\begin{…}…\\end{…}``) are
  atomic too — chemistry PDFs parsed by Docling/MinerU carry reaction
  equations as LaTeX, and half an equation is as useless as half a table;
* a table keeps its caption: the short ``表 N …`` / ``Table N …`` paragraph right above it is part of
  the table's chunk. A table is its own chunk, so the caption used to become a separate chunk of a few
  words (dropped outright when under the length floor) and the table — numbers with no subject — was left
  to be retrieved without the words that say what it is;
* ``<!-- page:N -->`` markers (inserted by the PDF parsers between pages) are
  consumed into ``Chunk.page_no`` provenance and stripped from chunk text;
* plain text without headings degrades to the legacy recursive splitter
  (``\\n\\n`` → ``\\n`` → sentence), so non-Markdown parsers keep behaving
  exactly as before (page-marked plain text is split page-wise first).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_HEADING_OPEN_RE = re.compile(r"^ {0,3}(#{1,6})(\s+)")
_MAX_HEADING_PATH = 80


def _truncate_heading_path(full: str) -> str:
    """P3-B28: cut a heading path at a ``" > "`` boundary, not mid-word.

    A path longer than ``_MAX_HEADING_PATH`` used to be hard-sliced, which
    could leave a dangling half-word (or half CJK term) as the last segment.
    Now the cut backs up to the last level boundary inside the budget; only
    a single over-long title (no boundary inside the budget) still hard-cuts.
    """
    if len(full) <= _MAX_HEADING_PATH:
        return full
    cut = full[:_MAX_HEADING_PATH]
    idx = cut.rfind(" > ")
    return cut[:idx] if idx > 0 else cut


def _parse_heading(line: str) -> tuple[int, str] | None:
    r"""``## Title ##`` → ``(2, "Title")``; ``None`` when the line is not an ATX heading.

    The same answer as the regex ``^(#{1,6})\s+(.+?)\s*#*\s*$`` this replaces, in linear time: that pattern has two
    adjacent ``\s*`` around an optional ``#*`` and a lazy title in front, so a heading followed by a long run of
    spaces (padding from a layout-preserving PDF conversion) was retried from every position — cubic. 2000 trailing
    spaces took longer than 20 s; a differential test against the old pattern is in ``test_chunking_linear.py``.
    """
    opened = _HEADING_OPEN_RE.match(line)
    if not opened:
        return None
    level, gap = len(opened.group(1)), opened.group(2)
    rest = line[opened.end():]
    if not rest:
        # the old pattern's title needed a character, so it took the last whitespace character of the gap; one
        # whitespace character after the hashes therefore was not a heading, two or more were (with an empty title)
        return (level, "") if len(gap) >= 2 else None
    trimmed = rest.rstrip()
    # P2-B44: strip only an ATX closing sequence (whitespace + hashes).
    # The old rstrip("#") also ate element symbols: "C#" became "C".
    # The separator is Unicode whitespace (as in the old pattern's \s),
    # not just space/tab: a hash run after U+3000/NBSP is still a closing
    # sequence, while one glued to a letter is content.
    closing = trimmed.rstrip("#")
    if closing != trimmed and closing and closing[-1].isspace():
        title = closing.rstrip()
    elif closing == "":
        # Nothing but hashes ("# ###"): the old pattern kept the first
        # one as the title. (A multi-hash title from the closing branch,
        # e.g. "###" in "###\r###\xa0#", is genuine content, not this.)
        title = "#"
    else:
        title = trimmed
    if title:
        return level, title
    if trimmed:  # nothing but closing hashes: the first one stays as the title
        return level, trimmed[0]
    return level, ""

PAGE_MARKER_RE = re.compile(r"^\s*<!--\s*page:(\d+)\s*-->\s*$")
# v2 P0-9: 视觉解析占位符 —— vision 失败/跳过时的降级文本，入库即检索噪声。
# 保知识库质量：chunking 时直接丢弃，不进入索引。
# P2-B54: 半角括号与“失败”变体一并认（上游是否真发“失败”文案未确认，
# 但放宽只影响占位符丢弃，且 _is_vision_placeholder 仍要求全引用行，
# 故误伤面极小，照做）。
VISION_PLACEHOLDER_RE = re.compile(r"[（(]视觉解析(已跳过|异常|未返回结果|失败)")


def _is_vision_placeholder(block: str) -> bool:
    """True if the block is only vision-failure placeholder quote lines."""
    if not VISION_PLACEHOLDER_RE.search(block):
        return False
    # 占位符块只含引用行（> 开头）与空行，不含实质内容
    for line in block.split("\n"):
        s = line.strip()
        if not s:
            continue
        if not s.startswith(">"):
            return False
    return True
BLOCK_MARKER_RE = re.compile(
    r"^\s*<!--\s*block:(text|table|formula|figure|code|image)\s*-->\s*$"
)


def page_marker(page_no: int) -> str:
    """The canonical inter-page marker PDF parsers emit (chunker strips it)."""
    return f"<!-- page:{page_no} -->"


def block_marker(kind: str) -> str:
    """Block-kind marker layout-aware parsers emit (MinerU tier, Phase 1).

    Lets the chunker use the detected block type instead of the markdown
    heuristic. Stripped from chunk text like page markers.
    """
    return f"<!-- block:{kind} -->"


@dataclass
class Chunk:
    text: str
    heading_path: str = ""
    page_no: int | None = None
    # PageIndex 借鉴 A5: 跨页 chunk 的末页（None = 单页，与 page_no 相同）。
    # P3-B38 (reachability, verified): page_end is set only when ONE
    # emitted chunk covers a page range — a caption+table atom whose parts
    # sit on different pages, or an accumulated chunk that ends exactly at
    # a page marker (trailing-marker boundary, verbatim still holds). An
    # accumulation spanning a marker with content on BOTH sides never
    # reaches verbatim (the marker text sits in md but not in the chunk),
    # so it falls back to per-block emission where each block keeps its own
    # page and page_end is None by design — not a missing range.
    page_end: int | None = None
    # P3-B40: hard-cut segments of one block SHARE one paragraph_idx by
    # design. Downstream (kb_index persist, kb/hybrid_search citation
    # labels, models comment "orders multi-paragraph sections") uses it
    # only for ordering/display — never as an identity key (the dedup key
    # is text+heading_path+page_no, see chunk_dedup_key), so sharing is safe.
    paragraph_idx: int | None = None
    offset_start: int | None = None
    offset_end: int | None = None
    # Layout provenance (Phase 0): normalized page-fraction bbox, filled by
    # layout-aware parsers (MinerU, Phase 1); None for the text chain.
    bbox: list | None = None
    # Block kind: text | table | formula | figure | code. Heuristic on
    # markdown for the text chain; layout parsers overwrite it.
    block_type: str = "text"
    # WeKnora 第一批 2.6：硬切父子键。超长原子块被硬切成的各段共享
    # 同一键，值为硬切前原子块在原文中的偏移区间 "start-end"；
    # None = 非硬切段（旧调用路径默认值）。第一阶段只存在于 Chunk
    # 与 dict 透传层，不进 DB 列、不进 dedup_key（另案决定）。
    parent_key: str | None = None


# ── WeKnora 第一批 2.6：受保护 span 注册表 ──────────────────────────────
# 已有项（fence / math / html_table / md_table）仍走 _split_blocks 现有
# 状态机，此处只登记新增项的判定；登记的目的是让保护清单显式化、可测试。
# 保守优先，宁漏勿错：只保护独立行（整行即反应式 / SMILES），不保护
# 行内片段。

_ARROW_RE = re.compile(r"<=>|->|=>|→")
_CHEM_TOKEN_RE = re.compile(r"[A-Za-z0-9()\[\]+]{2,}")
_SMILES_CHARSET = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789[]()=#:/\\@+-.%")


def _is_chem_token(tok: str) -> bool:
    """A token that looks chemical, not a prose word.

    P3-B32: the old "any token of length >= 2" gate let "Step 1 -> Step 2"
    through ("Step" is a length-4 token). A token now qualifies if it
    carries a digit (H2O, C6H12O6), is an element symbol (Na, Cl), or has
    an uppercase letter past the first position (NaCl). Tradeoff, stated
    plainly: bare hydrocarbon names ("Benzene -> ...") no longer qualify —
    they usually travel with a formula on the same line, and the cost of a
    miss is only that the line joins the surrounding text block.
    """
    return (
        any(ch.isdigit() for ch in tok)
        or bool(re.fullmatch(r"[A-Z][a-z]?", tok))
        or bool(re.search(r"[A-Z]", tok[1:]))
    )


def _is_reaction_line(line: str) -> bool:
    """含反应箭头且箭头两侧均有化学 token 的独立行。

    纯文本箭头（如“流程 → 下一步”、“Step 1 -> Step 2”）两侧无化学
    token 形态 → 不保护。
    """
    m = _ARROW_RE.search(line)
    if not m:
        return False
    left, right = line[: m.start()], line[m.end():]
    return any(_is_chem_token(t) for t in _CHEM_TOKEN_RE.findall(left)) and any(
        _is_chem_token(t) for t in _CHEM_TOKEN_RE.findall(right)
    )


def _is_smiles_like(line: str, min_len: int = 20) -> bool:
    """SMILES 形态的独立行：单 token（无空白）、长度 ≥ min_len、
    字符集基本落在 SMILES 字符集内、含环闭合数字或分支括号。

    排除：含 "://" 的 URL（字符集会误判）、含空白的普通句子、
    短 token（NaCl / H2O 本来就不会被切断）。
    """
    s = line.strip()
    if not s or " " in s or "\t" in s or "://" in s:
        return False
    if len(s) < min_len:
        return False
    if any(ch not in _SMILES_CHARSET for ch in s):
        return False
    if any(ch.isdigit() for ch in s) or ("(" in s and ")" in s):
        return True
    # P3-B32: a long unbranched carbon chain ("C" x 20) has neither digits
    # nor branches, so the old rule missed it. Heuristic: length >= min_len,
    # contains carbon, and every character is an element-letter that can
    # appear in an organic skeleton (C/B/N/O/S/P/F/I plus the second
    # letters l/r for Cl/Br). A 20+ letter prose word almost never fits
    # that alphabet *and* contains C without a vowel outside the set.
    return "C" in s and all(ch.lower() in "cbnospfilr" for ch in s)


def _is_protected_line(line: str, smiles_min_len: int = 20) -> bool:
    try:
        return _is_reaction_line(line) or _is_smiles_like(line, smiles_min_len)
    except Exception:  # noqa: BLE001 - 匹配异常时该行按普通文本处理
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "protected-span match failed for line %r", line[:80], exc_info=True
        )
        return False


PROTECTED_SPANS: tuple[str, ...] = ("reaction_line", "smiles_like")


# ── legacy plain-text splitter (moved verbatim from ingestion) ───────────────


_DOT_ABBREV = frozenset({
    "e.g", "i.e", "etc", "vs", "fig", "eq", "no", "mr", "mrs", "ms",
    "dr", "prof", "inc", "ltd", "approx", "al",  # "al" = "et al."
    "u.s", "u.k",
})


def _split_sentence_sep(text: str, sep: str) -> list[str]:
    """Split on *sep* like ``str.split`` except for guarded cases.

    P3-B31: ". " does not split after a known abbreviation ("e.g. ",
    "Fig. ", "et al. ") or inside a spaced decimal ("3. 5"); full-width
    "．" does not split inside a full-width decimal ("3．5"). The guard
    only ever *withholds* a split, so chunk texts are unchanged apart from
    fewer, longer pieces at those points.
    """
    if sep not in (". ", "．"):
        return text.split(sep)
    parts: list[str] = []
    start = 0
    pat = re.compile(re.escape(sep))
    for m in pat.finditer(text):
        if sep == ". ":
            j = m.start()
            k = j
            # P3-B31 fix: the abbreviation run includes inner periods —
            # for "e.g. " the word before the final period is "e.g", not
            # just "g" (the first version scanned letters only and missed
            # every multi-period abbreviation).
            while k > 0 and (text[k - 1].isalpha() or text[k - 1] == "."):
                k -= 1
            if text[k:j].rstrip(".").lower() in _DOT_ABBREV:
                continue
            if (
                j > 0 and text[j - 1].isdigit()
                and m.end() < len(text) and text[m.end()].isdigit()
            ):
                continue
        else:  # "．": digit on both sides = decimal point, not a terminator
            if (
                m.start() > 0 and text[m.start() - 1].isdigit()
                and m.end() < len(text) and text[m.end()].isdigit()
            ):
                continue
        parts.append(text[start:m.start()])
        start = m.end()
    parts.append(text[start:])
    return parts


def _plain_hard_slices(
    text: str, max_chars: int, overlap: int
) -> list[tuple[str, int]]:
    """Hard slices with overlap (depth-cap / no-separator fallback)."""
    chunks: list[tuple[str, int]] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        raw = text[start:end]
        chunk = raw.strip()
        if chunk:
            chunks.append((chunk, start + (len(raw) - len(raw.lstrip()))))
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _plain_split(
    text: str, *, max_chars: int, overlap: int, max_depth: int, _depth: int = 0
) -> list[tuple[str, int]]:
    """Position-aware core of the legacy recursive splitter.

    Returns [(chunk, start)] with starts relative to the stripped input —
    the same chunk texts the old ``chunk_plain_text`` produced, plus the
    true position of each. Split/join round-trips exactly (``sep.join`` of
    ``split``), so piece starts are cumulative lengths, not guesses.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [(text, 0)]
    # P3-B31: Chinese sentence punctuation joins the separator ladder —
    # a long Chinese paragraph with only ！/？/； used to hard-cut at
    # max_chars because no separator matched.
    _PLAIN_SEPS = ("\n\n", "\n", "。", "！", "？", "；", "．", ". ")
    if _depth >= max_depth or not any(sep in text for sep in _PLAIN_SEPS):
        return _plain_hard_slices(text, max_chars, overlap)

    for sep in _PLAIN_SEPS:
        if sep not in text:
            continue
        parts = _split_sentence_sep(text, sep)
        starts: list[int] = []
        pos = 0
        for i, part in enumerate(parts):
            starts.append(pos)
            pos += len(part) + (len(sep) if i < len(parts) - 1 else 0)
        chunks: list[tuple[str, int]] = []
        current = ""
        current_start = 0
        # (stripped chunk, start, end) of the last emitted chunk — the
        # overlap source for a following recursive split (P2-B48).
        last_flushed: tuple[str, int, int] | None = None
        for i, part in enumerate(parts):
            piece = part if i == len(parts) - 1 else part + sep
            piece_start = starts[i]
            if len(current) + len(piece) <= max_chars:
                if not current:
                    current_start = piece_start
                current += piece
            else:
                if current.strip():
                    cs = current.strip()
                    cs_start = current_start + (len(current) - len(current.lstrip()))
                    cs_end = current_start + len(current.rstrip())
                    chunks.append((cs, cs_start))
                    last_flushed = (cs, cs_start, cs_end)
                else:
                    last_flushed = None
                if len(piece) > max_chars:
                    sub: list[tuple[str, int]] = []
                    if last_flushed is not None and overlap > 0:
                        # P2-B48: the recursive split starts with the tail
                        # of the previous chunk, so the boundary carries
                        # overlap like every other boundary in this splitter.
                        prev, _ps, prev_end = last_flushed
                        tail = prev[-overlap:]
                        combined = tail + piece
                        lead = len(combined) - len(combined.lstrip())
                        tail_start = prev_end - len(tail)
                        for sc, sp in _plain_split(
                            combined, max_chars=max_chars, overlap=overlap,
                            max_depth=max_depth, _depth=_depth + 1,
                        ):
                            s_pos = sp + lead
                            if s_pos < len(tail):
                                sub.append((sc, tail_start + s_pos))
                            else:
                                sub.append((sc, piece_start + (s_pos - len(tail))))
                    else:
                        sub = [
                            (sc, piece_start + sp)
                            for sc, sp in _plain_split(
                                piece, max_chars=max_chars, overlap=overlap,
                                max_depth=max_depth, _depth=_depth + 1,
                            )
                        ]
                    chunks.extend(sub)
                    if sub:
                        last_flushed = (
                            sub[-1][0], sub[-1][1], sub[-1][1] + len(sub[-1][0])
                        )
                    current = ""
                else:
                    current = piece
                    current_start = piece_start
        if current.strip():
            cs = current.strip()
            cs_start = current_start + (len(current) - len(current.lstrip()))
            chunks.append((cs, cs_start))
        if chunks:
            return chunks
    return _plain_hard_slices(text, max_chars, overlap)


def _validate_chunk_params(max_chars: int, overlap: int) -> int:
    """P3-B36: ``max_chars <= 0`` is a caller error (raise); ``overlap``
    is clamped into ``[0, max_chars)`` (a negative or >= max_chars overlap
    used to make the hard slicer crawl one char at a time). Returns the
    clamped overlap.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")
    if max_chars == 1:
        return 0
    return min(max(overlap, 0), max_chars - 1)


def chunk_plain_text_with_offsets(
    text: str,
    *,
    max_chars: int = 1600,
    overlap: int = 200,
    max_depth: int = 10,
) -> list[tuple[str, int]]:
    """P1-3: chunk_plain_text + each chunk's start offset in ``text``.

    P2-B49: offsets come from the splitter itself, not from ``str.find``
    guessing — repeated text used to resolve to the first occurrence and
    drift every later offset. The splitter strips its input, so the
    leading whitespace of the original text is added back here.

    P3-B35 (offset convention, stated once): offsets index the stripped
    segment text, and ``text[offset_start:offset_end] == chunk text`` for
    plain-path chunks. The structured path keeps the same contract via
    verbatim emission; when a page/block marker sits between blocks,
    verbatim fails and emission falls back to per-block chunks, each still
    carrying its true position.
    """
    overlap = _validate_chunk_params(max_chars, overlap)
    lead = len(text) - len(text.lstrip())
    stripped = text.strip()
    return [
        (c, lead + s)
        for c, s in _plain_split(
            stripped, max_chars=max_chars, overlap=overlap, max_depth=max_depth
        )
    ]


def chunk_plain_text(
    text: str,
    *,
    max_chars: int = 1600,
    overlap: int = 200,
    max_depth: int = 10,
    _depth: int = 0,
) -> list[str]:
    """纯文本递归切分：按换行 > 句末标点逐级切分，控制 chunk 尺寸。

    P2-B49 后与旧版逐项一致，唯一有意差异：递归边界处把上一 chunk
    的尾部 overlap 拼入 combined 再递归，子偏移按 tail/piece 两段
    映射回原文，不再用 find 猜测。
    """
    overlap = _validate_chunk_params(max_chars, overlap)
    return [
        c
        for c, _ in _plain_split(
            text, max_chars=max_chars, overlap=overlap,
            max_depth=max_depth, _depth=_depth,
        )
    ]


# ── markdown structure parsing ───────────────────────────────────────────────


def _split_sections(md: str) -> list[tuple[str, str]]:
    """Split on ATX headings → [(heading_path, body)]. Empty path = preamble.

    P3-B33: ATX only — a Setext underline or a ``>`` quote line never opens
    a section; both stay body text of the current section.
    """
    lines = md.split("\n")
    sections: list[tuple[str, list[str]]] = [("", [])]
    stack: list[tuple[int, str]] = []  # (level, title)
    in_fence = False
    in_math = False  # v22: 跟踪 math 块，避免 math 内行首 # 被误判为标题
    math_lines = 0  # P2-B53: math 块行数护栏计数
    for line in lines:
        # P1-B26: fence 切换只在非 math 状态下生效 —— math 块内的 ``` 行是
        # 内容，不是围栏开关；旧逻辑会翻转 in_fence，让后续标题识别错乱。
        if line.lstrip().startswith("```") and not in_math:
            in_fence = not in_fence
        stripped = line.strip()
        # v22: math 块状态跟踪（复用 _math_open/_math_selfclosed）
        if not in_fence and not in_math:
            if not _math_selfclosed(stripped):
                closer = _math_open(stripped)
                if closer:
                    in_math = closer
                    math_lines = 0
        elif in_math:
            math_lines += 1
            if _math_line_closes(stripped, in_math):
                in_math = False
                math_lines = 0
            elif math_lines >= 50:
                # P2-B53: 未闭合 math 超过 50 行，恢复文本并重启用标题
                # 识别 —— 否则一个丢了 \end 的公式块吞掉后续整篇标题。
                in_math = False
                math_lines = 0
        heading = None if (in_fence or in_math) else _parse_heading(line)
        if heading:
            level, title = heading
            if not title:
                # P3-B29: "## " (empty title) is not a section boundary —
                # the old code pushed an empty segment, giving paths like
                # "A >  > B", while bare "##" (no gap) was not a heading at
                # all. An empty title now stays a body line, consistent
                # with the no-gap case. (_parse_heading's (level, "") return
                # itself is kept: the linear-time differential test pins it.)
                sections[-1][1].append(line)
                continue
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            path = _truncate_heading_path(" > ".join(t for _, t in stack))
            sections.append((path, []))
        else:
            sections[-1][1].append(line)
    return [(path, "\n".join(body).strip()) for path, body in sections if "\n".join(body).strip()]

def _math_open(stripped: str) -> str | None:
    """If *stripped* opens a display-math block, return the closer token."""
    if stripped.startswith("$$"):
        # single-line $$…$$ is already closed
        return None if stripped.count("$$") >= 2 and len(stripped) > 2 else "$$"
    if stripped.startswith("\\["):
        return None if stripped.endswith("\\]") and len(stripped) > 3 else "\\]"
    if stripped.startswith("\\begin{"):
        return "\\end{"
    return None


_BEGIN_ENV_RE = re.compile(r"^\\begin\{([^}]+)\}")


def _math_selfclosed(stripped: str) -> bool:
    # P1-B23: a single-line \begin{env}…\end{env} used to open a math block
    # that swallowed every following section until a stray \end{ appeared.
    # The env must match: \begin{aligned}…\end{equation} is not self-closed.
    if (
        (stripped.startswith("$$") and stripped.count("$$") >= 2 and len(stripped) > 2)
        or (stripped.startswith("\\[") and stripped.endswith("\\]") and len(stripped) > 3)
    ):
        return True
    m = _BEGIN_ENV_RE.match(stripped)
    if m:
        return f"\\end{{{m.group(1)}}}" in stripped[m.end():]
    return False


def _math_line_closes(stripped: str, closer: str) -> bool:
    """Whether a line inside a math block closes it (P1-B24).

    The old rule was startswith-only, so a content line ending with the
    closer (``x $$``) never closed the block, while ``$$5$$`` — an inline
    span, not a closer — closed it early whenever it happened to start a
    line. New rule: the line contains the closer and is not itself a fresh
    opener span — for ``$$`` that means an odd count (``$$5$$`` has two);
    for ``\\]`` / ``\\end{`` it means no fresh opener of the same family
    on the same line.
    """
    if closer == "$$":
        return stripped.count("$$") % 2 == 1
    if closer == "\\]":
        return "\\]" in stripped and "\\[" not in stripped
    if closer == "\\end{":
        return "\\end{" in stripped and "\\begin{" not in stripped
    return stripped.startswith(closer)


def _split_blocks(
    body: str, *, protected_spans: bool = False, smiles_min_len: int = 20
) -> list[str]:
    """Split a section body into blocks; tables, fences and math stay atomic.

    2.6：protected_spans=True 时，反应式 / SMILES 独立行在文本模式下
    先 flush 当前文本块、再作为独立原子块（与 _math_selfclosed 同构）；
    默认 False 时行为与旧版逐行一致。
    """
    lines = body.split("\n")
    blocks: list[str] = []
    current: list[str] = []
    mode = "text"  # text | table | html_table | fence | math
    math_closer = ""

    def flush() -> None:
        block = "\n".join(current).strip()
        # v2 P0-9: 视觉解析占位符直接丢弃，不进入索引（检索噪声）。
        # 占位符形如 ">\n> _（视觉解析…）_"，strip 后只剩引用行。
        if block and not _is_vision_placeholder(block):
            blocks.append(block)
        current.clear()

    # P3-B34: blank lines tolerated inside a markdown table (0 = none
    # seen yet; reset on every pipe row and on entering table mode).
    _table_blanks = 0
    # v19-2: 块级行数上限，未闭合时强制闭合，避免吞整篇。
    # v21-fix: 推广到 fence/html_table（与 math 同类问题）。
    _BLOCK_MAX_LINES = 50
    _block_lines = 0
    # P1-B22: an unclosed block that hits the line cap used to flush and
    # re-enter the state machine, so its continuation lines were re-read as
    # new fences/math/tables — a cascade of mislabels. Now the cap trip
    # suppresses further mode transitions for the rest of this section body:
    # continuation lines stay plain text (a later genuine fence in the same
    # section is sacrificed, which is cheaper than mislabelling the block).
    # Suppression is per _split_blocks call, so later sections are unaffected.
    suppress_modes = False

    for line in lines:
        stripped = line.strip()
        if mode == "fence":
            # P2-B46: a page marker inside a fence still splits the block —
            # otherwise chunk_markdown never sees it and later chunks inherit
            # the wrong page. The fence resumes after the marker.
            if PAGE_MARKER_RE.match(line):
                flush()
                blocks.append(stripped)
                continue
            current.append(line)
            _block_lines += 1
            if stripped.startswith("```"):
                flush()
                mode = "text"
                _block_lines = 0
            elif _block_lines >= _BLOCK_MAX_LINES:
                logger.warning(
                    "chunking: unclosed fence force-closed after %d lines; "
                    "remaining lines in this section stay plain text",
                    _BLOCK_MAX_LINES,
                )
                flush()
                mode = "text"
                _block_lines = 0
                suppress_modes = True
            continue
        if mode == "math":
            if PAGE_MARKER_RE.match(line):
                flush()
                blocks.append(stripped)
                continue
            current.append(line)
            _block_lines += 1
            # P1-B24: line-end closers (``x $$``) and odd-count $$ lines
            # close; ``$$5$$`` inline spans do not.
            if _math_line_closes(stripped, math_closer):
                flush()
                mode = "text"
                _block_lines = 0
            elif _block_lines >= _BLOCK_MAX_LINES:
                logger.warning(
                    "chunking: unclosed math force-closed after %d lines; "
                    "remaining lines in this section stay plain text",
                    _BLOCK_MAX_LINES,
                )
                flush()
                mode = "text"
                _block_lines = 0
                suppress_modes = True
            continue
        if stripped.startswith("```") and not suppress_modes:
            flush()
            mode = "fence"
            current.append(line)
            continue
        if PAGE_MARKER_RE.match(line):
            # page markers become standalone blocks (consumed by chunk_markdown)
            flush()
            blocks.append(stripped)
            continue
        if BLOCK_MARKER_RE.match(line):
            # block-kind markers likewise become standalone blocks; the
            # chunker consumes them into Chunk.block_type and strips them.
            flush()
            blocks.append(stripped)
            continue
        if _math_selfclosed(stripped):
            flush()
            blocks.append(stripped)
            continue
        closer = _math_open(stripped) if not suppress_modes else None
        if closer:
            flush()
            mode = "math"
            math_closer = closer
            _block_lines = 0
            current.append(line)
            continue
        is_table_row = stripped.startswith("|") and stripped.count("|") >= 2
        # v18-17: HTML 表格原子化 —— <table>...</table> 遇空行不截断，
        # 与模块头"表格原子"承诺对齐。
        _is_html_table_start = stripped.lower().startswith("<table")
        _is_html_table_end = "</table>" in stripped.lower()
        if mode == "table":
            # P2-B50: once anchored, a row needs only a pipe — leading /
            # trailing pipes are not required.
            if "|" in stripped:
                current.append(line)
                _table_blanks = 0
                continue
            if not stripped and _table_blanks == 0:
                # P3-B34: one blank line inside a table is tolerated (a
                # stray blank from PDF conversion); the second blank ends
                # the table, as before. The tolerated blank is stripped by
                # flush() if it ends up trailing.
                current.append(line)
                _table_blanks = 1
                continue
            flush()
            mode = "text"
        if mode == "html_table":
            current.append(line)
            _block_lines += 1
            if _is_html_table_end:
                flush()
                mode = "text"
                _block_lines = 0
            elif _block_lines >= _BLOCK_MAX_LINES:
                # P1-B22: same suppression as fence/math on cap trip.
                logger.warning(
                    "chunking: unclosed html table force-closed after %d lines; "
                    "remaining lines in this section stay plain text",
                    _BLOCK_MAX_LINES,
                )
                flush()
                mode = "text"
                _block_lines = 0
                suppress_modes = True
            continue
        # P2-B50: separator-row anchor — a table without leading/trailing
        # pipes is recognised by its delimiter row, backtracking one line
        # for the header (which is still in `current`: no blank line sits
        # between a header and its delimiter row).
        if _is_table_sep_row(stripped) and not suppress_modes:
            if current and "|" in current[-1].strip():
                header = current.pop()
                flush()
                mode = "table"
                _table_blanks = 0
                current.append(header)
                current.append(line)
                continue
        if _is_html_table_start and not suppress_modes:
            flush()
            mode = "html_table"
            _block_lines = 0
            current.append(line)
            # 单行完整表格直接结束
            if _is_html_table_end:
                flush()
                mode = "text"
            continue
        if is_table_row and not suppress_modes:
            flush()
            mode = "table"
            _table_blanks = 0
            current.append(line)
            continue
        # 2.6：受保护独立行（反应式 / SMILES）——先 flush 当前文本块，
        # 再把该行作为独立原子块；不进入 table / fence / math 状态机。
        if protected_spans and stripped and _is_protected_line(stripped, smiles_min_len):
            flush()
            blocks.append(stripped)
            continue
        if not stripped and current:
            flush()
            continue
        if stripped or current:
            current.append(line)
    flush()
    return blocks


def _is_table_sep_row(stripped: str) -> bool:
    """A Markdown delimiter row: pipes, colons, dashes, spaces — and at
    least one ``---`` run. A bare ``---`` is a thematic break, not a table,
    so the pipe is required. Used by _split_blocks (P2-B50 anchor) and by
    _is_atomic/_is_table_block/_classify_block_type (pipe-less tables)."""
    return (
        "|" in stripped
        and "---" in stripped
        and all(ch in "|-: " for ch in stripped)
    )


def _block_has_table_sep(block: str) -> bool:
    return any(_is_table_sep_row(line.strip()) for line in block.split("\n"))


def _looks_like_table(block: str) -> bool:
    """P3-B37: the single table predicate shared by ``_classify_block_type``
    and ``_is_atomic`` (pipe-led row, ``<table`` at the block start, or a
    delimiter row). ``_is_table_block`` deliberately keeps its wider
    anywhere-in-200-chars ``<table`` rule for caption attachment and is
    NOT unified here — narrowing it would detach captions from text-led
    blocks that embed a table.
    """
    first = block.lstrip()
    lower = first[:64].lower()
    return (
        first.startswith("|")
        or lower.startswith("<table")
        or _block_has_table_sep(block)
    )


def _classify_block_type(block: str) -> str:
    """Heuristic block kind for the text chain: table | formula | figure | code | text.

    Mirrors ``_is_atomic``'s classification; layout-aware parsers (MinerU,
    Phase 1) overwrite this with detected values. Kept deliberately coarse —
    the text chain has no real geometry, so this only separates "probably a
    table/formula" from running text for retrieval-time filtering.
    """
    first = block.lstrip()
    if _looks_like_table(block):
        return "table"
    if (
        first.startswith("$$")
        or first.startswith("\\[")
        or first.startswith("\\begin{")
    ):
        return "formula"
    if first.startswith("```"):
        return "code"
    if first.startswith("!["):
        return "figure"
    return "text"


def _is_atomic(block: str) -> bool:
    """Blocks that must not be split on blank lines (tables / code / math).

    P1 #17: MinerU / HTML pipelines often emit ``<table>…</table>``; treating
    them as atomic prevents blank-line splits from shredding rows.
    """
    first = block.lstrip()
    return (
        _looks_like_table(block)
        or first.startswith("```")
        or first.startswith("$$")
        or first.startswith("\\[")
        or first.startswith("\\begin{")
        # P2-B51: <table> counts only at the block start, aligned with
        # _classify_block_type — the old "anywhere in the first 200 chars"
        # clause marked text-led blocks atomic and hid their tables.
        # (Now expressed via _looks_like_table, P3-B37.)
    )


_CAPTION_START_RE = re.compile(
    r"^\s*(?:表格|表|Table|TABLE|table|Tab\.?)\s*"
    r"(?:[\dIVXivx]+|[A-Z]\d*|[一二三四五六七八九十百千]+)",
    re.UNICODE,
)
# P2-B47: 长度上限维持 300 —— 报告的“放宽”分支与既有钉子测试直接冲突
# （403 字“表1 说明文字×100”段必须不是 caption）；形式扩展（表格/小写
# table/Tab/字母附录/中文数字）已做，长度半项不擅自放宽，待 Cheng 定。
_CAPTION_MAX_CHARS = 300


def _is_table_block(block: str) -> bool:
    first = block.lstrip()
    return (
        first.startswith("|")
        or "<table" in first[:200].lower()
        # P2-B50: a pipe-less table block still counts for caption attachment.
        or _block_has_table_sep(block)
    )


def _is_table_caption(paragraph: str) -> bool:
    """A short paragraph that *starts* with ``表 N`` / ``Table N`` — what a table's caption looks like.

    Anchored on purpose: "如表1所示，粘度符合要求" is a sentence about a table, not its caption.
    """
    text = paragraph.strip()
    if not text or len(text) > _CAPTION_MAX_CHARS or "\n\n" in text:
        return False
    text = re.sub(r"^#{1,6}\s+", "", text).replace("**", "").replace("__", "")
    return bool(_CAPTION_START_RE.match(text))


def _strip_block_markers(md: str) -> str:
    """Remove ``<!-- block:K -->`` lines (plain-text fallback paths)."""
    return "\n".join(
        line for line in md.split("\n") if not BLOCK_MARKER_RE.match(line)
    )


def _split_pages(md: str) -> list[tuple[int | None, str]]:
    """Split page-marked text into [(page_no, segment)]; markers removed."""
    segments: list[tuple[int | None, list[str]]] = [(None, [])]
    for line in md.split("\n"):
        m = PAGE_MARKER_RE.match(line)
        if m:
            segments.append((int(m.group(1)), []))
        else:
            segments[-1][1].append(line)
    return [(p, "\n".join(body).strip()) for p, body in segments if "\n".join(body).strip()]


# P3-B39: 中文数字（实施例三）、"3-1" 型编号、复数（Examples /
# Embodiments）与 "第" 前缀一并认；独立成行才算边界（行内提及不受影响）。
_EXAMPLE_HEAD_LINE = re.compile(
    r"^\s*(?:第\s*)?(examples?|embodiments?|实施例|实例)\s*"
    r"([0-9０-９]+(?:-[0-9０-９]+)?|[IVXLC]+|[一二三四五六七八九十百千]+)?"
    r"\s*[:：.]?\s*$",
    re.I,
)


def mark_example_boundaries(md: str) -> str:
    """v2 P1-10: 专利 Example N 感知切分 —— 独立成行的 Example 头转为 ``##`` 标题。

    使 ``_split_sections`` 按示例边界切分，单个示例不被拦腰切断；
    heading_path 自然带上 "Example N"，示例 chunk 可整体召回。
    非独立成行的行内提及（如 "as shown in example 1"）不受影响。

    P2-B52: fence/math 块内的行不改写 —— 代码块里出现 "Example 1"
    是代码内容，不是示例边界。
    """
    out: list[str] = []
    in_fence = False
    in_math: str | bool = False
    for line in (md or "").split("\n"):
        stripped = line.strip()
        if line.lstrip().startswith("```") and not in_math:
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence and not in_math:
            if not _math_selfclosed(stripped):
                closer = _math_open(stripped)
                if closer:
                    in_math = closer
                    out.append(line)
                    continue
        elif in_math and _math_line_closes(stripped, in_math):
            in_math = False
            out.append(line)
            continue
        if (in_fence or in_math) or not _EXAMPLE_HEAD_LINE.match(line):
            out.append(line)
        else:
            out.append(f"## {line.strip()}")
    return "\n".join(out)


def chunk_markdown(
    md: str, *, max_chars: int = 1600, overlap: int = 200,
    bookmarks: list[tuple[str, int, int]] | None = None,
    example_aware: bool = False,
    protected_spans: bool | None = None,
    smiles_min_len: int | None = None,
) -> list[Chunk]:
    """Structure-aware chunking; degrades to the plain splitter for non-Markdown.

    PageIndex 借鉴 A2: ``bookmarks`` 为 [(title, level, page_1based)] 时，
    page-wise 分支中 heading_path 为空的 chunk 按页查表补充。

    2.6：protected_spans=None 时读 settings.kb_protected_spans_enabled
    （默认关）；显式传 True / False 时覆盖配置。smiles_min_len 同理
    （默认 kb_protected_smiles_min_len=20）。
    """
    overlap = _validate_chunk_params(max_chars, overlap)
    if protected_spans is None or smiles_min_len is None:
        try:
            from ..config import get_settings

            _s = get_settings()
            if protected_spans is None:
                protected_spans = bool(_s.kb_protected_spans_enabled)
            if smiles_min_len is None:
                smiles_min_len = int(_s.kb_protected_smiles_min_len or 20)
        except Exception:  # noqa: BLE001 - fail-open: 旧行为
            if protected_spans is None:
                protected_spans = False
            if smiles_min_len is None:
                smiles_min_len = 20
    md = (md or "").strip()
    if not md:
        return []
    # v2 P1-10: 专利 Example N 感知切分（默认关，仅专利路径开启）。
    if example_aware:
        md = mark_example_boundaries(md)

    # PageIndex 借鉴 A2: 书签按页区间展开（只需算一次）
    _bm_map: dict[int, str] = {}
    if bookmarks:
        try:
            from .parsing import _bookmarks_to_page_map

            _pages = [p for _, _, p in bookmarks]
            _bm_map = _bookmarks_to_page_map(
                bookmarks, max(_pages) if _pages else 0
            )
        except Exception:
            _bm_map = {}

    sections = _split_sections(md)
    has_structure = (
        any(path for path, _ in sections)
        # P2-B42: html tables, display math and ~~~ fences are structure
        # too — without them such documents fell to the plain splitter and
        # lost heading/table handling.
        or "|" in md or "```" in md or "$$" in md
        or "<table" in md or "\\begin" in md or "\\[" in md or "~~~" in md
    )
    if not has_structure:
        pages = _split_pages(md)
        if len(pages) > 1 or (pages and pages[0][0] is not None):
            # Page-marked plain text (pypdf): split page-wise so provenance
            # survives even without headings.
            chunks: list[Chunk] = []
            para_idx = 0
            # P1-3: seg 在 md 中的基址用 find 定位；chunk 偏移用真实值。
            md_cursor = 0
            for page_no, seg in pages:
                seg = _strip_block_markers(seg)
                seg_base = md.find(seg, md_cursor)
                if seg_base == -1:
                    # P3-B30: 同上，页段 find 失败回退并记 warning。
                    logger.warning(
                        "chunking: page segment not found in md, falling "
                        "back to cursor (page=%r)", page_no,
                    )
                    seg_base = md_cursor
                md_cursor = seg_base + len(seg)
                for c, off in chunk_plain_text_with_offsets(
                    seg, max_chars=max_chars, overlap=overlap
                ):
                    # PageIndex 借鉴 A2: 空 heading_path 按书签页映射补充
                    _hp = _bm_map.get(page_no, "") if page_no else ""
                    chunks.append(Chunk(
                        c, _hp[:_MAX_HEADING_PATH], page_no,
                        paragraph_idx=para_idx,
                        offset_start=seg_base + off,
                        offset_end=seg_base + off + len(c),
                        block_type=_classify_block_type(c),
                    ))
                    para_idx += 1
            return chunks
        chunks: list[Chunk] = []
        md = _strip_block_markers(md)
        # P1-3: 用真实偏移，不再 char_pos += len(c)（overlap 下会漂移）。
        for i, (c, off) in enumerate(
            chunk_plain_text_with_offsets(md, max_chars=max_chars, overlap=overlap)
        ):
            chunks.append(Chunk(
                c,
                paragraph_idx=i,
                offset_start=off,
                offset_end=off + len(c),
                block_type=_classify_block_type(c),
            ))
        return chunks

    chunks: list[Chunk] = []
    page: int | None = None
    para_counter = 0
    # v13-4: 结构化路径改用真实偏移（find+游标），不再 char_pos 合成累计。
    md_cursor = 0
    # Block kind announced by a layout-aware parser via <!-- block:K -->.
    # Overrides the markdown heuristic for the next emitted chunk only.
    pending_block: str | None = None

    def _locate_block(bd: str, needle: str, cursor: int) -> tuple[int, int]:
        """在 body 中定位 needle，返回 (body 相对位置, 新 cursor)。找不到则
        返回 (-1, cursor)（防御性，正常不应发生）。"""
        pos = bd.find(needle, cursor)
        if pos == -1:
            return -1, cursor
        return pos, pos + len(needle)

    def _page_span(pages: list[int | None], fallback_first, fallback_end) -> tuple[int | None, int | None]:
        """(first page, last page or None when it is the same) of the blocks accumulated so far."""
        known = [p for p in pages if p is not None]
        if known:
            return known[0], (known[-1] if known[-1] != known[0] else None)
        return fallback_first, (fallback_end if fallback_end != fallback_first else None)

    def _emit_verbatim_or_split(
        t: str, os_: int, blocks: list[str], body_start: int,
        seg_base: int, md: str, body: str, path: str, page_no,
        para_idx: int, blk_type: str, page_end: int | None = None,
        block_pages: list[int | None] | None = None,
    ) -> list["Chunk"]:
        """v14-5: 优先整体发射（verbatim）；md[os:oe]!=t 时退化逐 block 发射。

        单个 strip block 经 body.find 必命中，verbatim 天然成立。
        PageIndex 借鉴 A5: page_end 为跨页 chunk 的末页（None = 单页）。
        P3-B38: the per-block fallback below intentionally drops the range
        (blk_page_end=None): each emitted block sits on exactly one page,
        so there is no range to record — the old "beta" bug was precisely
        stamping the whole accumulation's range onto every block.
        """
        oe_ = os_ + len(t)
        if md[os_:oe_] == t:
            return [Chunk(
                t, path, page_no, page_end=page_end, paragraph_idx=para_idx,
                offset_start=os_, offset_end=oe_, block_type=blk_type,
            )]
        # 降级：逐 block 发射
        # v16 P3-18: 每个 block 递增 paragraph_idx（不再共享）；
        # block_type 优先用传入的 blk_type（调用方已知），分类失败才重算。
        out: list["Chunk"] = []
        cursor = body_start
        for _bi, blk in enumerate(blocks):
            bpos, cursor = _locate_block(body, blk, cursor)
            if bpos == -1:
                # P3-B30: 极端防御：找不到则跳过该 block（不应发生）。
                # 跳过即丢内容，记 warning 而不是静默丢弃。
                logger.warning(
                    "chunking: block not found in body, skipping "
                    "(path=%r, block=%.80s)", path, blk,
                )
                continue
            bos_ = seg_base + bpos
            # A block that was emitted on its own sits on ITS page. Giving every block the range of the whole
            # accumulated chunk labelled "beta" (page 2) as pages 1-3 and sent the citation to the wrong page.
            if block_pages is not None and len(block_pages) == len(blocks):
                blk_page, blk_page_end = block_pages[_bi], None
            else:
                blk_page, blk_page_end = page_no, page_end
            out.append(Chunk(
                blk, path, blk_page, page_end=blk_page_end,
                paragraph_idx=para_idx + _bi,
                offset_start=bos_, offset_end=bos_ + len(blk),
                block_type=blk_type or _classify_block_type(blk),
            ))
        return out

    for path, body in sections:
        # P2-B45: a block marker announces the kind of the immediately
        # following block only — it must not leak across a section boundary
        # (a marker at the end of one section used to type the next
        # section's first chunk).
        pending_block = None
        # section body 在 md 中的基址
        seg_base = md.find(body, md_cursor)
        if seg_base == -1:
            # P3-B30: find 失败回退到游标（不应发生），记 warning。
            logger.warning(
                "chunking: section body not found in md, falling back "
                "to cursor (path=%r)", path,
            )
            seg_base = md_cursor  # 防御性
        md_cursor = seg_base + len(body)
        body_cursor = 0
        current_start_body = 0  # `current` 首 block 在 body 中的位置
        current = ""
        # v14-5: 保留块边界，用于 verbatim 降级（逐 block 发射）
        current_blocks: list[str] = []
        # The page each entry of current_blocks sits on (the latest page marker when it was added).
        current_pages: list[int | None] = []
        current_page = page
        # PageIndex 借鉴 A5: 跨页跟踪 —— current 累积期间遇到的最末页码
        current_page_end = page
        current_para = para_counter
        for block in _split_blocks(
            body, protected_spans=bool(protected_spans),
            smiles_min_len=int(smiles_min_len),
        ):
            # 推进 body_cursor（markers 也占原文位置）
            bpos, body_cursor = _locate_block(body, block, body_cursor)
            m = PAGE_MARKER_RE.match(block)
            if m:
                page = int(m.group(1))
                if not current.strip():
                    current_page = page
                # PageIndex 借鉴 A5: 累积中遇到新页 marker，记录末页
                current_page_end = page
                continue
            bm = BLOCK_MARKER_RE.match(block)
            if bm:
                pending_block = bm.group(1)
                continue
            if _is_atomic(block):
                caption = ""
                caption_body_pos = -1
                caption_page: int | None = None
                # P1-B25: caption attachment by block sequence, not string
                # surgery on `current`. The old rpartition("\n\n") lost the
                # caption whenever a page/block marker (transparent to
                # current_blocks) sat between caption and table, and the
                # verbatim atom lookup then failed and split the pair.
                if current.strip() and _is_table_block(block) and current_blocks:
                    last = current_blocks[-1]
                    if _is_table_caption(last):
                        caption = last.strip()
                        caption_page = current_pages[-1]
                        current_blocks = current_blocks[:-1]
                        current_pages = current_pages[:-1]
                        current = "\n\n".join(current_blocks)
                        # caption 在 body 中的位置（current 起始处搜索）
                        caption_body_pos, _ = _locate_block(
                            body, caption, current_start_body
                        )
                if current.strip():
                    t = current.strip()
                    # v13-4: current 起始于 current_start_body（首 block 位置，
                    # block 已 strip 故无前导空白）
                    # v14-5: verbatim 不成立时退化逐 block 发射
                    os_ = seg_base + current_start_body
                    # PageIndex 借鉴 A5: 跨页 chunk 记录末页
                    _first, _pe = _page_span(current_pages, current_page, current_page_end)
                    chunks.extend(_emit_verbatim_or_split(
                        t, os_, current_blocks, current_start_body,
                        seg_base, md, body, path, _first,
                        current_para, pending_block or "text",
                        page_end=_pe, block_pages=current_pages,
                    ))
                    current = ""
                    current_blocks = []
                    current_pages = []
                    pending_block = None
                    current_page_end = current_page
                atom = f"{caption}\n\n{block}" if caption else block
                # v13-4: atom 定位——优先找完整 atom，否则用 caption 位置，
                # 再否则用 block 位置（bpos）
                # v14-5: verbatim 不成立时（多空行/marker 隔断）拆开发射
                atom_body_pos, _ = _locate_block(body, atom, current_start_body)
                if atom_body_pos == -1 and caption_body_pos != -1:
                    atom_body_pos = caption_body_pos
                if atom_body_pos == -1:
                    atom_body_pos = bpos if bpos != -1 else body_cursor
                aos_ = seg_base + atom_body_pos
                aoe_ = aos_ + len(atom)
                if caption and caption_body_pos != -1:
                    # P1-B25: emit the caption+table atom directly with the
                    # block-sequence span — caption start to table end —
                    # and the page span caption page → table page. The old
                    # verbatim check required contiguity and fell through
                    # to the split emission exactly when a marker intervened.
                    cos_ = seg_base + caption_body_pos
                    bend = bpos if bpos != -1 else body_cursor
                    coe_ = seg_base + bend + len(block)
                    chunks.append(Chunk(
                        atom, path, caption_page,
                        page_end=(page if page != caption_page else None),
                        paragraph_idx=para_counter,
                        offset_start=cos_, offset_end=coe_,
                        block_type=pending_block or _classify_block_type(block),
                    ))
                elif md[aos_:aoe_] == atom:
                    chunks.append(Chunk(
                        atom, path, page,
                        paragraph_idx=para_counter,
                        offset_start=aos_,
                        offset_end=aoe_,
                        block_type=pending_block or _classify_block_type(block),
                    ))
                else:
                    # v14-5: block 单独发射（verbatim 必成立）
                    bbs_ = seg_base + (bpos if bpos != -1 else body_cursor)
                    chunks.append(Chunk(
                        block, path, page,
                        paragraph_idx=para_counter,
                        offset_start=bbs_,
                        offset_end=bbs_ + len(block),
                        block_type=pending_block or _classify_block_type(block),
                    ))
                pending_block = None
                para_counter += 1
                current_page = page
                # v13-4: cursor 已在循环顶部推进到 block 末尾；current 已清空
                continue
            if not current.strip():
                current_page = page
                current_para = para_counter
            if len(current) + len(block) + 2 <= max_chars:
                if not current:
                    # v13-4: 新累积段的起始位置（首 block 处）
                    current_start_body = bpos if bpos != -1 else body_cursor
                current = f"{current}\n\n{block}" if current else block
                # v14-5: 同步块边界
                current_blocks.append(block)
                current_pages.append(page)
                para_counter += 1
                continue
            if current.strip():
                t = current.strip()
                os_ = seg_base + current_start_body
                # v14-5: verbatim 不成立时退化逐 block 发射
                _first, _pe = _page_span(current_pages, current_page, current_page_end)
                chunks.extend(_emit_verbatim_or_split(
                    t, os_, current_blocks, current_start_body,
                    seg_base, md, body, path, _first,
                    current_para, pending_block or "text",
                    page_end=_pe, block_pages=current_pages,
                ))
                current = ""
                current_blocks = []
                current_pages = []
                pending_block = None
                current_page_end = current_page
            current_page = page
            current_para = para_counter
            if len(block) > max_chars:
                # v13-4: block 基址用真实位置（bpos），不再 char_pos 合成。
                block_base = seg_base + (bpos if bpos != -1 else body_cursor)
                # 2.6：硬切父子键 = 硬切前原子块在原文中的偏移区间，
                # 各段共享同一键（保护 ≠ 不限长，超长保护行仍硬切）。
                _parent_key = f"{block_base}-{block_base + len(block)}"
                # P3-B40: all hard-cut segments of this block share
                # para_counter (one paragraph); ordering is by offset.
                for c, off in chunk_plain_text_with_offsets(
                    block, max_chars=max_chars, overlap=overlap
                ):
                    chunks.append(Chunk(
                        c, path, page,
                        paragraph_idx=para_counter,
                        offset_start=block_base + off,
                        offset_end=block_base + off + len(c),
                        block_type=pending_block or _classify_block_type(c),
                        parent_key=_parent_key,
                    ))
                pending_block = None
                para_counter += 1
                current = ""
                current_blocks = []
                current_pages = []
                continue
            else:
                # v13-4: block 太长放不下、current 已刷出，block 独立成段
                current_start_body = bpos if bpos != -1 else body_cursor
                current = block
                current_page_end = page
                # v14-5: 同步块边界
                current_blocks = [block]
                current_pages = [page]
            para_counter += 1
        if current.strip():
            t = current.strip()
            os_ = seg_base + current_start_body
            # v14-5: verbatim 不成立时退化逐 block 发射
            _first, _pe = _page_span(current_pages, current_page, current_page_end)
            chunks.extend(_emit_verbatim_or_split(
                t, os_, current_blocks, current_start_body,
                seg_base, md, body, path, _first,
                current_para, pending_block or "text",
                page_end=_pe, block_pages=current_pages,
            ))
            pending_block = None
    return chunks
