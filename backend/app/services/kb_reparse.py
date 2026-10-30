"""Re-parse one KB source from its retained original file.

The upload path keeps the original bytes under ``data/source_files`` (see
``source_files.persist_source_file``). Re-parsing runs them through the
*current* parse profile (``parse_document`` reads the live settings that
``parse_profiles.apply_profile`` changes) and rebuilds the source's full
text, chunks, FTS rows, table sidecar and PageIndex ToC tree in place — the
source id, project scope and provenance fields stay untouched.

Sources fetched from a URL / identifier never had their bytes retained:
:class:`SourceFileMissing` lets the API answer 409 instead of queueing a job
that can only fail.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff"})


class SourceNotFound(LookupError):
    pass


class SourceFileMissing(RuntimeError):
    pass


def original_file(source_id: str) -> Path:
    """The retained original of ``source_id``; raises when it cannot be re-parsed."""
    from ..db.source_store import get_source_store
    from .source_files import find_source_file

    if get_source_store().get(source_id) is None:
        raise SourceNotFound(source_id)
    path = find_source_file(source_id)
    if path is None or not path.is_file():
        raise SourceFileMissing(
            "该资料未保留原始文件（URL / 标识符抓取或历史数据），无法重新解析；请重新上传原文件"
        )
    return path


def _parse(path: Path, filename: str):
    """(markdown, parser, parsed|None, warnings) under the current parse profile."""
    from .parse_notices import collect as _collect_notices

    ext = path.suffix.lower().lstrip(".")
    content = path.read_bytes()
    if ext in _IMAGE_EXTS:
        from .vision_extract import extract_image, image_markdown

        extraction, err = extract_image(content, filename)
        if extraction is None or not (extraction.markdown.strip() or extraction.molecules):
            raise ValueError(f"图片未能结构化解析：{err or '视觉模型无输出'}")
        return image_markdown(extraction, filename), "vision", None, []
    from .parsing import ParserUnavailable, can_parse, install_hint, parse_document

    with _collect_notices() as warnings:
        parsed = parse_document(content, ext)
    text = parsed.markdown or ""
    if not text.strip():
        if not can_parse(ext):
            raise ParserUnavailable(ext, install_hint(ext))
        raise ValueError(f"未能提取到文本（格式：{ext}）——可能是扫描件，请检查 OCR 解析配置")
    return text, getattr(parsed, "parser", None), parsed, list(warnings)


def reparse_source(
    source_id: str,
    progress: Callable[[str, float], None] | None = None,
) -> dict:
    """Re-parse and re-index one source. Raises on failure (the task records it)."""
    from ..db.source_store import get_source_store
    from . import kb_index

    def _p(msg: str, frac: float) -> None:
        if progress is not None:
            try:
                progress(msg, frac)
            except Exception:  # noqa: BLE001 - progress is cosmetic
                pass

    path = original_file(source_id)
    store = get_source_store()
    row = store.get(source_id)
    filename = (row.filename if row is not None else "") or path.name

    _p(f"解析 {filename}…", 0.1)
    text, parser, parsed, warnings = _parse(path, filename)

    _p("更新全文…", 0.5)
    store.update_fields(
        source_id,
        full_text=text,
        raw_text_chars=len(text),
        parser=(parser or None),
    )
    if parsed is not None:
        from .ingestion import _maybe_persist_mineru_structured, _maybe_persist_ocr_text_tables
        from .parsing import maybe_persist_table_sidecar

        maybe_persist_table_sidecar(source_id, parsed)
        _maybe_persist_mineru_structured(True, source_id, parsed)
        _maybe_persist_ocr_text_tables(True, source_id, parsed)

    _p("重建分块与索引…", 0.7)
    # The old ToC tree points at chunk ids that are about to be replaced; when
    # PageIndex build-on-ingest is on, index_source builds a fresh one.
    kb_index._drop_toc_tree(source_id)
    n_chunks = kb_index.index_source(
        source_id,
        text,
        fail_soft=False,
        tables=list(getattr(parsed, "tables", None) or []) or None,
    )
    if n_chunks:
        store.update_fields(source_id, ingest_status="indexed", ingest_error=None)
    elif kb_index.kb_enabled():
        store.update_fields(
            source_id, ingest_status="failed", ingest_error="reparse produced 0 chunks"
        )
        warnings.append("重新解析后未生成任何分块")
    return {
        "source_id": source_id,
        "chunks": int(n_chunks or 0),
        "chars": len(text),
        "parser": parser,
        "warnings": warnings,
    }
