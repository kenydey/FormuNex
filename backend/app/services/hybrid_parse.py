"""Hybrid PDF parsing — local for everything, cloud for the pages that need it.

The shape of the problem: a good local parser handles most of a document
perfectly and a few pages badly. Sending the whole file to a metered cloud
parser pays for the pages that were already fine; sending none of it leaves
the dense tables and the chemical structures unreadable. So decide per page.

    ① local extraction        every page, no network, no quota
    ② triage                  which pages did the local pass handle badly?
    ③ escalate those pages    one page at a time, as a single-page PDF
    ④ route the figures       image blocks → vision model → SMILES
    ⑤ reassemble              page markers intact, so nothing downstream changes

**What escalation is worth.** A 50-page report with three table pages costs
three pages of quota, not fifty. `parse` logs the ratio it actually achieved,
because that number is the whole argument for this design and it should be
checkable rather than asserted.

**What goes to the vision model, and what must not.** MinerU returns
`equation` blocks already in LaTeX and `table` blocks already as HTML. Handing
those to a vision model would replace a structured answer with a guess. Only
`image` and `chart` blocks — and tables MinerU itself could not render — have
anything to gain from being looked at.

**On chemical structures.** MinerU does not produce SMILES; it returns the
structure as a picture. The SMILES come from `vision_extract`, which asks the
LLM and then verifies with RDKit. Without RDKit nothing is verified and every
structure is unchecked model output — `image_markdown` renders that as a ✗ in
its own column rather than hiding it.
"""
from __future__ import annotations

import contextvars
import logging
import re

from ..config import get_settings
from . import mineru_cloud, pdf_local
from .errors import degrade_return, log_handled_exception

logger = logging.getLogger(__name__)

# Block types that arrive structured and must not be second-guessed by a
# vision model, and those that carry no text at all and must be looked at.
_ALREADY_STRUCTURED = frozenset({"text", "title", "list", "code", "equation"})
_VISUAL = frozenset({"image", "chart"})
# Page furniture: repeating it in every chunk is noise that dilutes retrieval.
_DISCARD = frozenset({"header", "footer", "page_number"})

# ── vision circuit breaker ────────────────────────────────────────────────
# Per-document counter: how many consecutive vision calls have failed with a
# *permanent* error (endpoint paused, token rejected).  Reset at the start of
# every ``parse()`` call so a broken endpoint on one document does not leak
# into the next.  Transient failures (timeout, 503 cold-start) do NOT
# increment this counter — retrying may succeed.
# v24-fix: 改为 ContextVar，每请求隔离，避免并发文档互相清零/污染。
_vision_consecutive_failures: contextvars.ContextVar[int] = contextvars.ContextVar(
    "vision_consecutive_failures", default=0
)


def _needs_escalation(page: pdf_local.LocalPage) -> bool:
    """Whether the local pass left something on this page worth paying for.

    A table the local parser already rendered as a pipe table is *not* worth
    re-parsing — that check is what keeps the quota bill proportional to the
    hard pages rather than to the document.

    A page with no text layer at all is the worst-handled page there is, and it
    only reaches here in a *mixed* document: `looks_scanned` is `all()` over the
    pages, so a file with one text cover sheet and twenty scans is not "a scan"
    and never sees the scanned branch. While the layout parser OCR-ed every
    document those pages were quietly filled in; with that off they would come
    back empty, which is the failure mode with no symptom.
    """
    settings = get_settings()
    if page.looks_scanned:
        return True
    if page.n_tables and not page.has_markdown_table:
        return True
    if page.image_area_ratio >= settings.hybrid_image_area_threshold:
        return True
    return False


# PageIndex 借鉴 A1 (Route 2): 扩展编号规则 —— Roman / CJK 汉字数字 / 字母 / Chapter。
# 均为纯文本 token 规则（仿 pageindex/flash/heading_detection/detectors.py），
# 防误伤约束：编号后必须跟分隔符+非空文本；"2026 年"这类无分隔符数字开头不命中。
# 原有三组正则（数字编号）保留：
_HEADING_MULTI_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,2})[\s\u3000]+(\S)")
_HEADING_DOT_RE = re.compile(r"^(\d{1,2})\.[ \u3000]+(\S)")
_HEADING_CN_RE = re.compile(r"^(\d{1,2})[、\u3000][ \u3000]*(\S)")
_HEADING_ROMAN_RE = re.compile(r"^([IVXLCDM]{1,6})\.[ \u3000]+(\S)")
_HEADING_CJK_NUM_RE = re.compile(r"^([一二三四五六七八九十百千两]{1,6})[、.\u3000][ \u3000]*(\S)")
_HEADING_LETTER_RE = re.compile(r"^([A-Ha-h])\.[ \u3000]+(\S)")
_HEADING_CHAPTER_RE = re.compile(
    r"^((?:Chapter|CHAPTER|Appendix|APPENDIX|第[一二三四五六七八九十百千\d]+[章节篇]|附录)\b[ \u3000]*\d*[ \u3000]+)(\S)",
    re.IGNORECASE,
)


def _heading_markdown(text: str, api_level: int) -> tuple[str, int] | None:
    """Title hierarchy rule layer (2026-09-05, live-API verified).

    云 MinerU 把数字编号标题扁平化 —— 实测 ``1.``/``1.1``/``1.2`` 全部
    text_level=2, 页 2+ 的标题在文档 markdown 里还会丢 ``#`` 前缀。规则:
    文本形如编号标题(``1. X`` / ``1.1 X`` / ``1.2.3 X``) → 层级 = 编号段数;
    否则退回 API 的 text_level。``2026 年`` 这类无句点/顿号的数字开头正文
    不匹配(需编号后跟 . 、 或 全角句点), 避免误伤。
    """
    if not text or not text.strip():
        return None
    stripped = text.strip()
    m = _HEADING_MULTI_RE.match(stripped)
    if m:
        return stripped, min(m.group(1).count(".") + 1, 6)
    m = _HEADING_DOT_RE.match(stripped)
    if m:
        return stripped, 1
    m = _HEADING_CN_RE.match(stripped)
    if m:
        return stripped, 1
    # PageIndex 借鉴 A1 (Route 2): 扩展编号规则，开关 pdf_heading_rules_extended。
    try:
        from ..config import get_settings

        _extended = get_settings().pdf_heading_rules_extended
    except Exception:
        _extended = True
    if _extended:
        if any(r.match(stripped) for r in (_HEADING_ROMAN_RE, _HEADING_CJK_NUM_RE, _HEADING_LETTER_RE, _HEADING_CHAPTER_RE)):
            return stripped, 1
    if api_level:
        return stripped, min(max(int(api_level), 1), 6)
    return None


def _render_blocks(blocks: list[mineru_cloud.MinerUBlock], *, page_label: str) -> str:
    """MinerU blocks → Markdown, routing only what benefits from vision."""
    from .vision_extract import extract_image, image_markdown, vision_available

    can_see, vision_hint = vision_available()
    parts: list[str] = []

    for block in blocks:
        kind = block.type
        if kind in _DISCARD:
            continue

        # Headings come back as `text` blocks carrying a `text_level`, not as a
        # `title` type — verified against the live API, which returns
        # {"type": "text", "text": "…", "text_level": 2} for a document title
        # and never emits "title" at all. Keying on the type alone silently
        # demoted every heading to prose, which cost `heading_path` on every
        # escalated page. `_heading_markdown` adds the numbered-heading rule
        # layer on top (API 把 1./1.1 扁平化为同 level)。
        # v18-3: 只对文本类块做标题判定 —— equation/table 块的文本可能以
        # "数字编号+空白"开头（如表格首单元格"1. 试剂名称"），误判为标题
        # 会导致整表 HTML 静默丢失。
        # P2-B7: list blocks are lists, not headings. A multi-line list block
        # starting with "1. ..." used to be swallowed whole into one broken
        # heading (the matcher only anchors at the start). Heading judgment
        # applies only to single-line text/title blocks; anything else keeps
        # its lines as prose.
        if kind in ("text", "title") and "\n" not in (block.text or "").strip():
            heading = _heading_markdown(block.text, block.text_level)
            if heading:
                text, level = heading
                parts.append(f"{'#' * level} {text}")
                continue

        if kind == "equation":
            # Already LaTeX. A vision model can only do worse.
            if block.text.strip():
                parts.append(f"$$\n{block.text.strip()}\n$$")
            else:
                # P2-B12: an empty equation used to vanish silently. Keep a
                # visible marker so the gap is auditable downstream.
                parts.append("> _（公式块内容为空）_")
            continue

        if kind == "table":
            if block.html.strip():
                # Already structured HTML — keep it verbatim.
                if block.caption:
                    parts.append(f"**{block.caption.strip()}**")
                parts.append(block.html.strip())
                continue
            # v29 Phase1 H-3: block.text 兜底 —— 无 HTML 时先查 block.text，
            # 有文本则按 pipe table 保留，避免表格变占位符且下游不可恢复。
            if block.text and block.text.strip():
                if block.caption:
                    parts.append(f"**{block.caption.strip()}**")
                parts.append(block.text.strip())
                logger.debug("MinerU 表格无 HTML，已用 block.text 兜底")
                continue
            # A table MinerU could not render is the one table worth a look.
            parts.append(_visual_markdown(block, page_label, can_see, vision_hint,
                                          extract_image, image_markdown))
            continue

        if kind in _VISUAL:
            parts.append(_visual_markdown(block, page_label, can_see, vision_hint,
                                          extract_image, image_markdown))
            continue

        # Everything else — text, list, code, and any block type MinerU adds
        # later — is already prose. An unknown type keeps its text rather than
        # being dropped: silently losing content is the one failure mode with
        # no symptom.
        if block.text.strip():
            parts.append(block.text.strip())

    return "\n\n".join(p for p in parts if p.strip())


def _count_vision_failure(error: str | None, page_label: str) -> None:
    """Count one permanent vision failure toward the breaker.

    Shared by the "returned None" path and the exception path (P2-B3:
    the same permanent error used to count only in the former shape).
    Transient failures are not counted. Budget <= 0 means the breaker
    is DISABLED (config documents 0 = 不断路): the counter still moves
    for observability, but quota exhaustion only logs instead of
    pretending to open a breaker that can never open.
    """
    from .vision_extract import _is_permanent_vision_failure

    if not _is_permanent_vision_failure(error):
        return
    budget = get_settings().vision_max_consecutive_failures
    _vision_consecutive_failures.set(_vision_consecutive_failures.get() + 1)
    # Quota exhaustion (429 insufficient_quota) is not going to recover
    # until the vendor resets the weekly quota — skip the "wait N pages"
    # ramp and open the breaker immediately so every remaining figure in
    # this document degrades to a placeholder instead of retrying 3× each.
    lower = (error or "").lower()
    if "insufficient_quota" in lower or "quota has been exhausted" in lower:
        if budget:
            _vision_consecutive_failures.set(max(_vision_consecutive_failures.get(), budget))
            logger.info("hybrid: vision quota exhausted — breaker opened immediately")
        else:
            logger.info(
                "hybrid: vision quota exhausted but breaker disabled (budget 0) — continuing"
            )
    logger.info(
        "hybrid: vision permanent failure #%d for %s — "
        "breaker at %d; %s",
        _vision_consecutive_failures.get(), page_label, budget, error,
    )


def _visual_markdown(
    block: mineru_cloud.MinerUBlock,
    page_label: str,
    can_see: bool,
    vision_hint: str,
    extract_image,
    image_markdown,
) -> str:
    """A figure as Markdown — via the vision model, or degraded but never lost.

    Losing the block would be the worst outcome: a reader of the knowledge base
    would have no way to tell a page with an unreadable figure from a page with
    no figure at all.
    """

    caption = (block.caption or "").strip()
    fallback = f"> [图 · {page_label}]{' ' + caption if caption else ''}"

    if not block.image:
        return fallback + "\n>\n> _（图片数据缺失）_"
    if not can_see:
        return f"{fallback}\n>\n> _（未做视觉解析：{vision_hint}）_"

    # ── circuit breaker: skip when the endpoint has been telling us no ──
    settings = get_settings()
    budget = settings.vision_max_consecutive_failures
    if budget and _vision_consecutive_failures.get() >= budget:
        logger.info(
            "hybrid: vision breaker open after %d consecutive permanent "
            "failures — skipping remaining figures in this document",
            _vision_consecutive_failures.get(),
        )
        return (
            f"{fallback}\n>\n>"
            f" _（视觉解析已跳过：端点连续 {_vision_consecutive_failures.get()} 次返回不可恢复错误）_"
        )

    try:
        extraction, error = extract_image(block.image, f"{page_label}.png")
    except Exception as exc:  # P2-B3: the exception path counts exactly
        # like the None path — the same permanent endpoint error used to
        # trip the breaker only when it arrived as "returned None".
        _count_vision_failure(str(exc), page_label)
        return degrade_return(
            logger, exc, "hybrid: vision call failed",
            f"{fallback}\n>\n> _（视觉解析异常）_",
        )

    if extraction is None:
        # The reason goes to the log, not into the document. Provider errors
        # carry account identifiers — a real 429 read "Your account
        # org-a752… <ak-fbkd…> is suspended" — and whatever lands here becomes
        # an indexed, searchable chunk. Embedding a vendor's account id in the
        # knowledge base is bad on its own; doing it on every failed figure
        # also fills the index with a few hundred characters of noise that
        # retrieval then has to compete with.
        logger.warning("hybrid: vision returned nothing for %s (%s)", page_label, error)
        # Permanent failures trip the breaker; transient ones keep trying.
        _count_vision_failure(error, page_label)
        return f"{fallback}\n>\n> _（视觉解析未返回结果，详见服务端日志）_"

    # A successful call resets the counter: the endpoint is reachable again.
    _vision_consecutive_failures.set(0)
    rendered = image_markdown(extraction, page_label)
    return f"{caption}\n\n{rendered}" if caption else rendered


def _has_table_markdown(text: str) -> bool:
    """Table-structure signal: a pipe-table row or an HTML <table>."""
    if "<table" in text:
        return True
    return any(
        line.strip().startswith("|") and line.count("|") >= 2
        for line in text.splitlines()
    )


def _adopt_upgraded(page_no: int, local_text: str, upgraded_text: str) -> tuple[str, bool]:
    """Adoption gate for a MinerU-escalated page (P1-B4).

    Escalation used to replace local text unconditionally, so a
    placeholder-only or sparse MinerU result overwrote good local text.
    Returns (text_to_use, adopted): when not adopted the caller keeps the
    local text and the rejection is user-visible via a note.
    """
    local = (local_text or "").strip()
    upgraded = (upgraded_text or "").strip()
    if not upgraded:
        return local, False
    if not local:
        return upgraded, True
    content_lines = [
        ln for ln in upgraded.splitlines()
        if ln.strip() and not ln.strip().startswith(">")
    ]
    if not content_lines:
        try:
            from .parse_notices import note

            note(f"MinerU 升级第 {page_no} 页仅含占位内容，已保留本地文本")
        except Exception:
            pass
        return local, False
    if len(upgraded) < 0.5 * len(local):
        try:
            from .parse_notices import note

            note(
                f"MinerU 升级第 {page_no} 页字符数显著低于本地 "
                f"（{len(upgraded)} < 本地 {len(local)} 的 50%），已保留本地文本"
            )
        except Exception:
            pass
        return local, False
    if _has_table_markdown(local) and not _has_table_markdown(upgraded):
        try:
            from .parse_notices import note

            note(f"MinerU 升级第 {page_no} 页丢失本地表格结构，已保留本地文本")
        except Exception:
            pass
        return local, False
    return upgraded, True


# Stopwords that separate the Latin-script languages MinerU's `latin` pack
# covers from plain English (`en`). Kept deliberately short: the detector
# only needs a family judgment, and any miss falls back to `en`, which is
# the safe default for Latin script.
_LATIN_STOPWORDS = (
    # German
    " und ", " der ", " die ", " das ", " ist ", " von ", " mit ", " für ",
    " ein ", " eine ", " nicht ", " sich ",
    # French
    " le ", " la ", " les ", " des ", " une ", " est ", " pour ", " dans ",
    " qui ", " que ", " aux ",
    # Spanish
    " el ", " los ", " las ", " una ", " es ", " para ", " con ", " del ",
    # Italian
    " il ", " che ", " di ", " per ", " una ", " sono ", " del ",
    # Portuguese
    " o ", " os ", " uma ", " para ", " com ", " dos ", " que ", " não ",
)


def _mineru_language_for_text(text: str | None) -> str | None:
    """MinerU `language` code for OCR, from already-available text.

    Only a family judgment: CJK-dominant text -> "ch"; Hangul -> "korean";
    kana -> "japan"; Latin script with a continental stopword -> "latin";
    other Latin script -> "en". Returns None when the text says nothing —
    the caller then leaves the parameter unset and the server default
    applies. Detection is best-effort by design: a wrong guess only picks a
    different OCR language pack, and the fallback is always today's
    behaviour (server default).
    """
    if not text or not text.strip():
        return None
    lowered = f" {text.strip().lower()} "
    if re.search(r"[\uAC00-\uD7A3]", lowered):
        return "korean"
    if re.search(r"[\u3040-\u30FF]", lowered):
        return "japan"
    from . import tesseract_local

    if tesseract_local.cjk_ratio(text) >= 0.3:
        return "ch"
    if any(word in lowered for word in _LATIN_STOPWORDS):
        return "latin"
    if re.search(r"[a-z]", lowered):
        return "en"
    return None


def _sample_document_language(content: bytes) -> str | None:
    """Detect the OCR language from the first page, or None.

    Used by the whole-document scan path, which has no local text to judge
    from: one page through local RapidOCR is enough for a family judgment.
    Fail-open — any failure returns None and the server default applies.
    The mixed-document path does not need this: its scanned pages were
    already OCR'd locally, so the text is passed in as a hint instead.
    """
    try:
        from . import rapidocr_local

        if not get_settings().rapidocr_enabled:
            return None
        png = pdf_local.page_as_png(
            content, 1, dpi=get_settings().rapidocr_dpi
        )
        if not png:
            return None
        text, _conf = rapidocr_local.ocr_png_scored(png)
        return _mineru_language_for_text(text)
    except Exception:
        return None


def _escalate_page(content: bytes, page: pdf_local.LocalPage, *, ocr_text: str | None = None) -> str | None:
    """One page through MinerU, or None to keep the local text.

    Sliced as a single-page PDF rather than rendered: the text layer survives,
    so MinerU reads characters instead of OCR-ing pixels, and it costs the same
    one page of quota either way.

    A scanned page has no text layer to read, so it goes up with OCR on —
    without it MinerU returns nothing and the page is lost (the adoption gate
    can only keep local text, which for a scan is empty). When the caller
    already OCR'd the page locally (`ocr_text`), the text doubles as the
    language hint for the cloud OCR pass.
    """
    page_pdf = pdf_local.page_as_pdf(content, page.page_no)
    if not page_pdf:
        return None
    language = (
        _mineru_language_for_text(ocr_text)
        if page.looks_scanned and ocr_text
        else None
    )
    document = mineru_cloud.parse_bytes(
        page_pdf, ext="pdf", ocr=page.looks_scanned, language=language,
        timeout=get_settings().mineru_page_timeout_s,
    )
    if document is None or not document.blocks:
        # v16 P3-4: MinerU 空结果不再静默 —— 写用户可见 note。
        from .parse_notices import note

        note(f"MinerU 解析第 {page.page_no} 页返回空结果，已回退本地文本层")
        return None
    rendered = _render_blocks(document.blocks, page_label=f"p.{page.page_no}")
    if not rendered:
        return None
    adopted, ok = _adopt_upgraded(page.page_no, page.markdown, rendered)
    return adopted if ok else None


def _escalate_pages_batch(
    content: bytes, pages: list[pdf_local.LocalPage]
) -> dict[int, str]:
    """All qualifying non-scanned pages in one batch MinerU submission.

    Sliced as single-page PDFs (text layer intact, so MinerU reads characters
    instead of OCR-ing pixels), submitted in one ``/file-urls/batch`` call so the
    server parses them in parallel, then rendered page by page through the same
    fusion layer as the single-page path. A page whose slice failed or whose
    batch result is missing keeps its local text — identical degradation to the
    serial path, just reached with one round trip instead of N.
    """
    page_pdfs: list[bytes] = []
    order: list[int] = []
    for page in pages:
        page_pdf = pdf_local.page_as_pdf(content, page.page_no)
        if not page_pdf:
            continue
        page_pdfs.append(page_pdf)
        order.append(page.page_no)
    if not page_pdfs:
        return {}

    local_by_page = {p.page_no: p.markdown for p in pages}
    documents = mineru_cloud.parse_pages_batch(page_pdfs)
    upgraded: dict[int, str] = {}
    for page_no, document in zip(order, documents):
        if document is None or not document.blocks:
            # v21-fix: 批量路径加 note，与串行路径对齐（否则静默）。
            try:
                from .parse_notices import note

                note(f"MinerU 批量解析第 {page_no} 页返回空结果，已回退本地文本层")
            except Exception:  # noqa: BLE001
                pass
            continue
        rendered = _render_blocks(document.blocks, page_label=f"p.{page_no}")
        if not rendered:
            continue
        adopted, ok = _adopt_upgraded(page_no, local_by_page.get(page_no, ""), rendered)
        if ok:
            upgraded[page_no] = adopted
    return upgraded


def _parse_scanned(content: bytes, pages: list[pdf_local.LocalPage]) -> str | None:
    """A document with no text layer: the whole thing, with OCR.

    Per-page escalation makes no sense here — every page would qualify — so
    this is one call, still bounded by the per-document page cap so a long
    scan cannot swallow the day's quota by accident.
    """
    settings = get_settings()
    cap = settings.mineru_max_pages_per_doc
    if len(pages) > cap:
        logger.warning(
            "hybrid: %d-page scan exceeds the %d-page cap — not sending to MinerU",
            len(pages), cap,
        )
        # P2: 截断用户可见 —— 不再只写日志。
        from .parse_notices import note

        note(f"文档共 {len(pages)} 页，MinerU 解析上限 {cap} 页，超限部分未解析")
        return None

    logger.info("hybrid: no text layer, sending %d pages to MinerU with OCR", len(pages))
    document = mineru_cloud.parse_bytes(
        content, ext="pdf", ocr=True,
        language=_sample_document_language(content),
    )
    if document is None or not document.blocks:
        # v16 P3-4: MinerU 空结果不再静默 —— 写用户可见 note。
        from .parse_notices import note

        note("MinerU OCR 解析返回空结果，已回退本地处理")
        return None

    # P2-B12: _normalise defaults a missing page_idx to 0, so a document
    # whose blocks carry no page info collapses wholesale onto p.1. When
    # every block claims page 0 but the source has several pages, page
    # attribution is untrustworthy: warn and render in document order
    # instead of stamping a false p.1 on everything. (A genuine
    # page-1-only result shares this path harmlessly — its content is
    # preserved in order, only the page label is dropped.)
    if len(pages) > 1 and document.blocks and all(b.page_idx == 0 for b in document.blocks):
        logger.warning(
            "hybrid: MinerU returned no page indices for a %d-page scan — "
            "rendering %d blocks in document order",
            len(pages), len(document.blocks),
        )
        try:
            from .parse_notices import note

            note(f"MinerU 返回的解析块无页码信息（{len(pages)} 页文档），已按文档顺序合并呈现")
        except Exception:
            pass
        return _render_blocks(document.blocks, page_label="scan") or None

    by_page: dict[int, list[mineru_cloud.MinerUBlock]] = {}
    for block in document.blocks:
        by_page.setdefault(block.page_idx + 1, []).append(block)
    rendered = [
        (page_no, _render_blocks(blocks, page_label=f"p.{page_no}"))
        for page_no, blocks in sorted(by_page.items())
    ]
    return pdf_local.assemble(rendered) or None


def _scanned_vision_fallback(content: bytes) -> str | None:
    """Scanned pages OCR could not read (image/tabular): vision LLM fallback.

    RapidOCR returning almost nothing usually means figure/table-heavy pages
    where structure is the content. Send the first few rendered pages to the
    vision model (bounded, gated on availability) and keep whatever markdown
    comes back. Not a per-page double pass — only fires when OCR was empty-ish.
    """
    from . import vision_extract
    from .pdf_local import page_as_png
    from ..config import get_settings

    # v24-fix: 熔断检查 —— 熔断器打开时直接返回 None，不绕过。
    _settings = get_settings()
    _budget = int(_settings.vision_max_consecutive_failures)
    if _budget and _vision_consecutive_failures.get() >= _budget:
        logger.info("hybrid: scanned vision fallback skipped (breaker open)")
        return None

    try:
        ok, _why = vision_extract.vision_available()
    except Exception:
        ok = False
    if not ok:
        return None
    limit = min(3, get_settings().rapidocr_max_pages)
    parts: list[str] = []
    for page_no in range(1, limit + 1):
        png = page_as_png(content, page_no, dpi=get_settings().rapidocr_dpi)
        if not png:
            continue
        try:
            result, _err = vision_extract.extract_image(png, filename=f"scan-p{page_no}.png")
        except Exception as exc:
            log_handled_exception(logger, exc, "vision scan fallback failed")
            continue
        finally:
            del png
        if result and getattr(result, "markdown", None):
            parts.append(f"<!-- page:{page_no} -->\n\n{result.markdown}")
    if parts:
        logger.info("hybrid: %d scanned page(s) read by vision fallback", len(parts))
        return "\n\n".join(parts)
    return None


def _scanned_density_ok(text: str, n_pages: int | None) -> bool:
    """Per-page density gate for scanned-document OCR (P1-B8).

    The old code returned any non-empty OCR output as the document, so a
    100-page scan OCR-ing to 300 garbage characters shipped as the whole
    document. Same 50 chars/page threshold as the PDF quality gate; an
    unknown page count fails open (the gate cannot judge, so it does not
    discard a document on its own ignorance).
    """
    if not n_pages:
        return True
    stripped = (text or "").strip()
    if not stripped:
        return False
    try:
        threshold = get_settings().pdf_quality_gate_min_chars_per_page
    except Exception:
        threshold = 50
    return len(stripped) / n_pages >= threshold


def _scanned_without_cloud(content: bytes) -> str | None:
    """Read a document with no text layer, using only what is on this host.

    Language-routed: English scans go to Tesseract (fast, ~7 s/page), Chinese
    scans to RapidOCR (accurate, ~12 s/page). Language is judged from the first
    page's RapidOCR output by its CJK ratio, so the extra cost is one page of
    RapidOCR (~12 s) against a whole-document saving on English scans. RapidOCR
    stays the backstop when Tesseract is absent or cannot read the document.

    Stage 5 / SR-092 (same-source confirmation, no parallelization): the
    vote pages and the body pages raster from the same source — the same
    PDF bytes through the same pdf_local.page_as_png renderer, at the
    same default DPI (rapidocr_dpi defaults to 120; the Tesseract body
    path hardcodes 120; the RapidOCR body path reads rapidocr_dpi).
    The vote's language judgment therefore transfers to the body pages.
    Pages still OCR serially, deliberately: RapidOCR runs as a process
    singleton whose creation — not inference — is locked, and both body
    loops stream one PNG at a time to bound peak memory. Parallelizing
    the page loops needs a proven thread-safe engine first, so it stays
    a separate decision rather than a forced change here.
    """
    from . import rapidocr_local, tesseract_local

    try:
        from . import pdf_local as _pl

        n_pages = _pl.page_count(content) or None
    except Exception:
        n_pages = None

    if tesseract_local.tesseract_available():
        # v3 P0 M-10 真修：多页投票 —— 此前只看第一页，英文封面+中文正文
        # 会被整篇判英文。现采样前 3 页，多数投票。
        # （删掉 tesseract_local._vote_language 死代码，路由在此处）
        from . import pdf_local as _pl

        votes_eng = 0
        votes_chi = 0
        doc = None
        try:
            try:
                doc = _pl._open(content)
            except Exception:
                doc = None
            n_sample = min(3, _pl.page_count(content))
            for pg in range(1, n_sample + 1):
                png = _pl.page_as_png(
                    content, pg, dpi=get_settings().rapidocr_dpi, _doc=doc
                )
                if not png:
                    continue
                txt, _conf = rapidocr_local.ocr_png_scored(png)
                del png
                if not txt:
                    continue
                if tesseract_local.cjk_ratio(txt) < 0.1:
                    votes_eng += 1
                else:
                    votes_chi += 1
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
        # P2-B11: one English vote out of three sampled pages is not a
        # language judgment — require at least two English votes, else
        # fall through to RapidOCR.
        if votes_eng >= 2 and votes_eng > votes_chi:
            eng = tesseract_local.ocr_pdf(content, lang="eng")
            if eng and _scanned_density_ok(eng, n_pages):
                logger.info(
                    "hybrid: scanned document read by Tesseract (English, %d/%d 页投票)",
                    votes_eng, votes_eng + votes_chi,
                )
                return eng
            if eng:
                # P1-B8: sparse Tesseract output must not ship as the
                # document — fall through to RapidOCR (which has its own
                # density gate and vision rescue below).
                logger.info(
                    "hybrid: Tesseract output too sparse (%d chars / %s pages), "
                    "falling back to RapidOCR",
                    len(eng.strip()), n_pages if n_pages else "?",
                )

    text = rapidocr_local.ocr_pdf(content)
    if text:
        logger.info("hybrid: scanned document read by local OCR (no cloud)")
        if _scanned_density_ok(text, n_pages):
            # OCR 输出过短(图多字少的页, 如数据表/图片型表格) → vision 兜底:
            # 前几页整图交给视觉 LLM 结构化(表格保真), 避免结构流失为纯文字。
            if len(text.strip()) < 200:
                visual = _scanned_vision_fallback(content)
                if visual:
                    # v24-fix: 拼接而非整体替换 —— 保留第 4 页起的 OCR 内容。
                    return text + "\n\n" + visual
            return text
        # P1-B8: sparse OCR garbage must not ship as the document. Try the
        # vision rescue; without it, fall through to layout OCR below (and,
        # when MinerU is available, to _parse_scanned) instead of returning
        # a few characters per page as if that were the document.
        visual = _scanned_vision_fallback(content)
        if visual:
            return text + "\n\n" + visual
        try:
            from .parse_notices import note

            note(
                f"扫描件 OCR 结果过稀疏（{len(text.strip())} 字符 / "
                f"{n_pages if n_pages else '未知'} 页），未直接采用；已回退后续解析路径"
            )
        except Exception:
            pass

    # Same page cap as RapidOCR on purpose: this is the same job by another
    # engine, and a 200-page scan must not be able to swallow an ingest run
    # merely because the first reader was unavailable.
    text = pdf_local.ocr_markdown(
        content, max_pages=get_settings().rapidocr_max_pages
    )
    if text:
        logger.info("hybrid: scanned document read by the layout parser's own OCR")
    return text


def _apply_heading_rules_local(markdown: str) -> str:
    """PageIndex 借鉴 A1: 本地 tier 编号标题规则。

    pymupdf4llm 的黑盒 `#` 发射会漏掉纯编号标题（如 "IV. 结果"、"附录 A"）。
    对非标题行跑 `_heading_markdown`，命中则补 `#` 前缀。保守约束：
    只处理 ≤80 字符的行（标题通常短，正文编号列表项通常长），防误伤。
    Fail-open：异常时原文返回。
    """
    try:
        from ..config import get_settings

        if not get_settings().pdf_heading_rules_extended:
            return markdown
    except Exception:
        # P3-B11: an unreadable switch must NOT silently enable content
        # rewriting — return the input unchanged (fail-closed), instead of
        # falling through to apply the rules.
        return markdown
    try:
        out: list[str] = []
        # P3-B8: skip fenced code blocks — a "1. xxx" line inside a fence
        # is code, not a numbered heading, and must not gain a "#" prefix.
        in_fence = False
        for line in markdown.splitlines():
            s = line.strip()
            if s.startswith("```") or s.startswith("~~~"):
                in_fence = not in_fence
                out.append(line)
                continue
            if in_fence:
                out.append(line)
                continue
            if s and not s.startswith("#") and len(s) <= 80:
                hit = _heading_markdown(s, 0)
                if hit:
                    _, level = hit
                    # _heading_markdown 返回 (stripped, level)；stripped == s
                    out.append(f"{'#' * level} {s}")
                    continue
            out.append(line)
        return "\n".join(out)
    except Exception:
        return markdown


def parse(content: bytes) -> str | None:
    """Parse *content*, escalating only the pages that need it.

    Returns None when local extraction is unavailable, so the caller's parser
    cascade moves on. Every other failure degrades to the local result: a
    cloud outage should cost quality, never the upload.
    """
    _vision_consecutive_failures.set(0)

    pages = pdf_local.extract_pages(content)
    if not pages:
        return None

    local_only = pdf_local.assemble([(p.page_no, p.markdown) for p in pages])
    # PageIndex 借鉴 A1: 本地 tier 补编号标题规则（pymupdf4llm 黑盒漏掉的纯编号标题）。
    local_only = _apply_heading_rules_local(local_only)
    scanned = pdf_local.looks_scanned(pages)

    available, hint = mineru_cloud.mineru_available()
    if not available:
        # A scan has no text layer, so `local_only` here is empty and returning
        # it means the whole document is lost — every tier below this one also
        # reads text layers. Local OCR is the only thing that can read it, and it
        # costs no quota, so try it before giving up.
        if scanned:
            ocr = _scanned_without_cloud(content)
            if ocr:
                return ocr
        else:
            # Mixed document, no cloud parser: the text-layer-less pages have
            # nowhere to go. Escalation is what normally rescues them and it
            # needs MinerU. Say so per document rather than shipping a
            # half-empty one silently — the pages are gone either way, but an
            # operator can act on a number.
            blank = sum(1 for p in pages if p.looks_scanned)
            if blank:
                logger.warning(
                    "hybrid: %d/%d pages have no text layer and no parser can "
                    "read them (MinerU off: %s; enable it or "
                    "FORMUNEX_PDF_LOCAL_OCR)",
                    blank, len(pages), hint,
                )
                # v2 P1-11: 缺页从日志升级为用户可见 —— 静默丢页是质量缺陷。
                try:
                    from .parse_notices import note

                    note(
                        f"本文档 {blank}/{len(pages)} 页无文本层且无可用解析器"
                        "（MinerU 未启用），这些页的内容未入库；"
                        "启用 MinerU 或本地 OCR 后重新入库可补全"
                    )
                except Exception:
                    pass
        logger.debug("hybrid: local only (%s)", hint)
        return local_only or None

    if scanned:
        # RapidOCR 总开关同时作为「本地 OCR 优先」门闩（云端解析档关它）：
        # 开 → 本地 Tesseract/RapidOCR/版面 OCR 先读，读不出再整份 MinerU；
        # 关 → 跳过本机 OCR，扫描件直接云 OCR（省弱机 CPU，耗配额）。
        if get_settings().rapidocr_enabled:
            ocr = _scanned_without_cloud(content)
            if ocr:
                return ocr
        return (
            _parse_scanned(content, pages)
            or local_only
            or None
        )

    cap = get_settings().mineru_max_pages_per_doc
    candidates = [p for p in pages if _needs_escalation(p)]
    selected = candidates[:cap]
    if len(candidates) > cap:
        logger.warning(
            "hybrid: %d pages qualified for escalation, capped at %d — "
            "the rest keep their local text",
            len(candidates), cap,
        )
        # P2: 截断用户可见。
        from .parse_notices import note

        note(f"{len(candidates)} 页需高精度解析，上限 {cap} 页，其余保留本地文本")

    if selected:
        # Wake a scale-to-zero vision endpoint once, up front. The loop below is
        # sequential, so only the first call would pay the boot anyway — but
        # paying it here means the log attributes minutes of waiting to a cold
        # start instead of to whichever page happened to go first. A no-op for
        # every provider that is not a rented endpoint.
        from .vision_extract import prewarm

        prewarm()

    # The per-page paths (scanned pages, and the batch-off rollback below) are
    # sequential, so a failure is not free — it costs the full per-page timeout,
    # and an unreachable MinerU charges that once per page. The breaker turns
    # "20 pages × one broken network" into three attempts. Counted
    # *consecutively*: a document where some pages come back keeps going,
    # because that is a working connection with a few hard pages. The batch
    # path needs no breaker — a broken network costs it one timeout, not N.
    budget = get_settings().mineru_max_page_failures
    min_conf = get_settings().rapidocr_min_confidence
    upgraded: dict[int, str] = {}
    attempted = 0
    consecutive_failures = 0

    scanned_sel = [p for p in selected if p.looks_scanned]
    cloud_sel = [p for p in selected if not p.looks_scanned]

    # ① 扫描页：RapidOCR 开时本地 OCR 优先（快、免费、省配额），低置信度或
    # 读不出才回退 MinerU；关则直接单页云升级（与云端解析档一致）。
    # 逐页串行 + 熔断保留——扫描页在本语料里罕见。
    for page in scanned_sel:
        attempted += 1
        ocr_text = None
        if get_settings().rapidocr_enabled:
            from . import rapidocr_local

            png = pdf_local.page_as_png(
                content, page.page_no, dpi=get_settings().rapidocr_dpi
            )
            if png:
                text, conf = rapidocr_local.ocr_png_scored(png)
                ocr_text = text
                del png
                if text and conf >= min_conf:
                    upgraded[page.page_no] = text
                    consecutive_failures = 0
                    logger.info(
                        "hybrid: p.%d read by local OCR (conf %.2f ≥ %.2f)",
                        page.page_no, conf, min_conf,
                    )
                    continue
        rendered = _escalate_page(content, page, ocr_text=ocr_text)
        if rendered:
            upgraded[page.page_no] = rendered
            consecutive_failures = 0
            continue
        consecutive_failures += 1
        if budget and consecutive_failures >= budget:
            logger.warning(
                "hybrid: %d consecutive MinerU page failures — abandoning "
                "escalation after %d/%d pages; the rest keep their local text",
                consecutive_failures, attempted, len(selected),
            )
            break

    # ② 非扫描页（密集表格/图）：一次批量提交，服务端并行；开关关闭退回逐页串行。
    if cloud_sel:
        if get_settings().mineru_batch_enabled:
            attempted += len(cloud_sel)
            upgraded.update(_escalate_pages_batch(content, cloud_sel))
        else:
            for page in cloud_sel:
                attempted += 1
                rendered = _escalate_page(content, page)
                if rendered:
                    upgraded[page.page_no] = rendered
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if budget and consecutive_failures >= budget:
                        logger.warning(
                            "hybrid: %d consecutive MinerU page failures — abandoning "
                            "escalation after %d/%d pages; the rest keep their local text",
                            consecutive_failures, attempted, len(selected),
                        )
                        break

    # `attempted`, not `len(selected)`: once the breaker can cut the loop short,
    # reporting the plan instead of the work would make this line a lie about
    # what the quota actually bought.
    logger.info(
        "hybrid: %d/%d pages escalated (%d succeeded)",
        attempted, len(pages), len(upgraded),
    )
    # Surfaced on the per-document ingest line too. The parser tier is recorded
    # as "hybrid" whether or not MinerU was called, so without this there is no
    # per-document way to tell whether the quota bought anything — which is the
    # first question anyone asks after paying to turn it on.
    if upgraded:
        from . import ingest_timing as timing

        timing.note(mineru_pages=len(upgraded))
    return pdf_local.assemble(
        [(p.page_no, upgraded.get(p.page_no, p.markdown)) for p in pages]
    ) or None
