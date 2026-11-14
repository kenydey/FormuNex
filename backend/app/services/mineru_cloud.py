"""MinerU cloud parsing — structured blocks for the pages worth paying for.

Used by the hybrid pipeline to escalate individual pages that local extraction
handles poorly: dense tables, formulas, and figures. Whole documents go through
only when there is no text layer to work with at all.

**Why the SDK and not raw REST.** The published REST flow is three calls and a
zip: request a presigned URL, PUT the file, poll a batch endpoint, download and
unpack `full_zip_url`. `mineru-open-sdk` does all of that, and — more valuable —
raises a *typed* exception per failure, so quota exhaustion, an expired token
and a page-limit rejection are distinguishable without matching error strings.
Reimplementing it would be a second code path to the same network dependency:
more surface, no more resilience. When the SDK is absent this module reports
unavailable and the caller keeps its local result, which is the actual fallback.

**Degradation.** Every public function returns `None`/empty rather than
raising. A cloud parser that can take down an upload would be worse than no
cloud parser at all.

⚠️ Verified against `mineru-open-sdk` 0.2.5: `MinerU.extract` accepts a **path
or URL, not bytes** (hence the temp file), and the SDK's `PageLimitError`
documents a **600**-page ceiling even though the web docs say 200.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from ..config import get_settings
from .errors import degrade_return, optional_import
from .runtime_secrets import effective_setting, setting_or_default

logger = logging.getLogger(__name__)

# Formats MinerU accepts. Images are included deliberately: it is how a
# rendered scan page gets escalated.
SUPPORTED_EXTS = frozenset(
    {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx",
     "png", "jpg", "jpeg", "jp2", "webp", "gif", "bmp",
     # Batch 4: HTML sources run the dedicated MinerU-HTML model
     # (see _model_for_ext); .html/.htm uploads go through parse_bytes
     # instead of the local converter when MinerU is available.
     "html", "htm"}
)


@dataclass
class MinerUBlock:
    """One block of MinerU's `content_list`, normalised.

    The block *type* is the routing signal the fusion layer needs: `equation`
    already carries LaTeX and `table` already carries HTML, so neither has
    anything to gain from a vision model, while `image` and `chart` have
    nothing else to offer it.
    """

    type: str
    page_idx: int = 0
    text: str = ""
    text_level: int = 0
    html: str = ""
    caption: str = ""
    image_path: str = ""
    image: bytes | None = None
    # Bounding box [x0, y0, x1, y1] when the cloud content_list carries one.
    # Whether content_list includes bbox is UNVERIFIED (no real API sample
    # as of 2026-10-01): _normalise passes it through when present and
    # leaves None otherwise — never assert absence.
    bbox: list | None = None


@dataclass
class MinerUDocument:
    blocks: list[MinerUBlock] = field(default_factory=list)
    markdown: str = ""       # MinerU's own full.md — the fallback if fusion fails
    cached: bool = False


def mineru_available() -> tuple[bool, str]:
    """(usable, actionable Chinese hint) — the shape `vision_available` uses."""
    settings = get_settings()
    if not settings.mineru_enabled:
        return False, "MinerU 云端解析未启用（FORMUNEX_MINERU_ENABLED）"
    if not effective_setting(settings, "mineru_api_key"):
        return False, "未配置 MinerU API Token，请在「设置 → API 配置」填写"
    if not optional_import("mineru"):
        return False, (
            "未安装 mineru-open-sdk。请在「设置 → 依赖管理」安装，"
            "或 pip install mineru-open-sdk。"
        )
    return True, ""


# ── content-hash cache ───────────────────────────────────────────────────────


def _is_auth_failure(exc: Exception) -> bool:
    """Whether *exc* is really "your token was rejected".

    The SDK defines `AuthError` for codes A0202/A0211, but a rejected token
    does not reach it: the request 401s first and `httpx.HTTPStatusError`
    comes out raw. Verified against the live API on both `extract` and
    `get_task`. Without this, a wrong token is reported as a connection
    failure and sends the operator to check the network instead of the key.
    """
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in (401, 403)


def _cache_root() -> Path:
    return Path(get_settings().mineru_cache_dir).expanduser()


# P1-B6/P2-B9: cache schema version. Bump when the stored format or the
# meaning of a key changes; _cache_load treats any other version as a miss
# (old entries are orphaned, never served). v2 = options in the key +
# written_at/version in the payload + per-page image filenames.
# v3 = language in the key (an OCR result in German is not a result in Chinese).
# v4 = model in the key (a pipeline result is not a vlm result).
_CACHE_VERSION = 4
# P2-B9: a cached MinerU result is not timeless — the cloud model moves.
# 30 days is a judgment call, not a measured staleness point; the version
# above is the real invalidation signal, this is only a backstop.
_CACHE_TTL_S = 30 * 24 * 3600


def _cache_key(content: bytes, *, ext: str, ocr: bool, formula: bool, table: bool,
               language: str | None = None, model: str | None = None) -> str:
    """Hash the bytes *and* the effective options — the same page parsed
    with OCR on is a different result, and serving one for the other would
    be silently wrong. P1-B6: formula/table recognition options are part of
    the key too, so a caller forcing formula=True/table=True (structured
    path) no longer shares a key with a caller running on different
    settings defaults. Language is part of the key for the same reason:
    an OCR result in German is not a result in Chinese — and so is the
    model, since a pipeline result is not a vlm result. Callers must resolve
    the effective options with _extract_options *before* the cache lookup,
    not after."""
    digest = hashlib.sha256(content).hexdigest()
    return (
        f"{digest}-{ext}-{'ocr' if ocr else 'txt'}"
        f"-f{1 if formula else 0}t{1 if table else 0}"
        f"-l{language or 'auto'}-m{model or 'auto'}-v{_CACHE_VERSION}"
    )


def _cache_load(key: str) -> MinerUDocument | None:
    path = _cache_root() / key / "doc.json"
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text("utf-8"))
        # P2-B9: version mismatch or age past TTL is a miss, not a serve.
        if payload.get("version", 1) != _CACHE_VERSION:
            logger.debug("mineru cache version mismatch for %s — miss", key[:12])
            return None
        written_at = payload.get("written_at")
        if isinstance(written_at, (int, float)) and time.time() - written_at > _CACHE_TTL_S:
            logger.debug("mineru cache expired for %s — miss", key[:12])
            return None
        blocks = []
        for raw in payload.get("blocks", []):
            image_path = raw.get("image_path", "")
            image = None
            if image_path:
                # P2-B9: images are stored per (page_idx, basename); the
                # basename-only file is tried as a defensive fallback for
                # partially migrated caches.
                stored = path.parent / "images" / f"{raw.get('page_idx', 0)}__{Path(image_path).name}"
                if not stored.is_file():
                    stored = path.parent / "images" / Path(image_path).name
                if stored.is_file():
                    image = stored.read_bytes()
            blocks.append(MinerUBlock(**{**raw, "image": image}))
        return MinerUDocument(
            blocks=blocks, markdown=payload.get("markdown", ""), cached=True
        )
    except Exception as exc:
        logger.debug("mineru cache read failed for %s (%s)", key, exc)
        return None


def _cache_store(key: str, document: MinerUDocument) -> None:
    """Persist to disk, not memory: quota is per day, and a restart in the
    middle of a batch should not mean paying for the same pages twice."""
    try:
        target = _cache_root() / key
        (target / "images").mkdir(parents=True, exist_ok=True)
        blocks = []
        for block in document.blocks:
            raw = {k: v for k, v in vars(block).items() if k != "image"}
            if block.image and block.image_path:
                # P2-B9: per-page filename — two pages whose images share a
                # basename no longer overwrite each other in the cache.
                name = f"{block.page_idx}__{Path(block.image_path).name}"
                (target / "images" / name).write_bytes(block.image)
            blocks.append(raw)
        (target / "doc.json").write_text(
            json.dumps({"blocks": blocks, "markdown": document.markdown,
                        "version": _CACHE_VERSION, "written_at": time.time()},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        # A cache that cannot be written must never fail the parse it was
        # meant to speed up.
        logger.debug("mineru cache write failed for %s (%s)", key, exc)


# ── SDK call ─────────────────────────────────────────────────────────────────


def _normalise(result, images_by_path: dict[str, bytes]) -> MinerUDocument:
    blocks: list[MinerUBlock] = []
    bad_fields: list[str] = []

    def _safe_int(value, field: str) -> int:
        # P1-B5: a malformed page_idx/text_level (e.g. a string) used to
        # raise out of _normalise and kill the whole batch result. Fall
        # back to 0 per field and report once, not per block.
        if value is None or value == "":
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            bad_fields.append(field)
            return 0

    for raw in result.content_list or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("type") or "")
        image_path = str(raw.get("img_path") or "")
        caption = raw.get("image_caption") or raw.get("table_caption") or ""
        if isinstance(caption, list):
            caption = " ".join(str(c) for c in caption)
        bbox = raw.get("bbox")
        if bbox is not None:
            try:
                bbox = [float(v) for v in bbox]
                bbox = bbox if len(bbox) == 4 else None
            except (TypeError, ValueError):
                bbox = None
        blocks.append(
            MinerUBlock(
                type=kind,
                page_idx=_safe_int(raw.get("page_idx"), "page_idx"),
                text=str(raw.get("text") or ""),
                text_level=_safe_int(raw.get("text_level"), "text_level"),
                html=str(raw.get("table_body") or ""),
                caption=str(caption),
                image_path=image_path,
                bbox=bbox,
                image=images_by_path.get(image_path)
                or images_by_path.get(Path(image_path).name if image_path else ""),
            )
        )
    if bad_fields:
        logger.warning(
            "mineru: %d block(s) had unparsable %s — fell back to 0",
            len(bad_fields), sorted(set(bad_fields)),
        )
    return MinerUDocument(blocks=blocks, markdown=result.markdown or "")


# ── daily usage counter ──────────────────────────────────────────────────────


def _usage_path() -> Path:
    """Where today's MinerU counters live.

    Next to the parse cache, so tests that isolate the cache dir also
    isolate the counter — and so the counter survives restarts, unlike the
    in-memory stats elsewhere in this codebase.
    """
    return Path(get_settings().mineru_cache_dir) / "usage.json"


def _count_pages(content: bytes, ext: str) -> int:
    """Pages submitted, for the daily quota counter.

    PDFs are counted exactly; anything else counts as one page. Any failure
    counts one — the counter is observability, not billing, and must never
    break a parse.
    """
    if ext.lower() != "pdf":
        return 1
    try:
        import fitz  # lazy: pymupdf is a parser dependency, not a cloud one

        return max(1, len(fitz.open(stream=content, filetype="pdf")))
    except Exception:
        return 1


def _record_usage(*, pages: int, ok: bool, quota: bool = False) -> None:
    """Add one MinerU submission to the daily counter.

    Stored as ``{date: {pages, calls, failed, quota_errors}}`` in
    ``<mineru_cache_dir>/usage.json`` (atomic write; entries older than
    30 days are dropped on write). Only submissions that provably reached
    the server are counted: a success or a timeout definitely submitted
    (a timeout means the task is still running server-side), a failed
    state means the task ran and failed, a quota rejection is recorded as
    an event with no pages (nothing was consumed), and auth/size
    rejections are not counted at all — they never reached the server and
    the probe already surfaces them. `failed` counts submissions that
    returned no usable result. Best-effort throughout: a lost increment
    from two threads writing at once is acceptable, and a failure here
    must never break a parse.
    """
    try:
        from . import _fsutil

        path = _usage_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        today = date.today().isoformat()
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        data = {k: v for k, v in data.items() if isinstance(k, str) and k >= cutoff}
        row = data.setdefault(
            today, {"pages": 0, "calls": 0, "failed": 0, "quota_errors": 0}
        )
        if not isinstance(row, dict):
            row = {"pages": 0, "calls": 0, "failed": 0, "quota_errors": 0}
            data[today] = row
        row["calls"] = int(row.get("calls", 0)) + 1
        row["pages"] = int(row.get("pages", 0)) + max(0, pages)
        if not ok:
            row["failed"] = int(row.get("failed", 0)) + 1
        if quota:
            row["quota_errors"] = int(row.get("quota_errors", 0)) + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        _fsutil.atomic_write_text(path, json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def daily_usage_summary() -> dict | None:
    """Today's MinerU usage counters, or None when nothing was submitted.

    For the Settings probe message: the cloud offers no usage endpoint, so
    this client-side count is the only number the operator can see.
    """
    try:
        data = json.loads(_usage_path().read_text(encoding="utf-8"))
        row = data.get(date.today().isoformat()) if isinstance(data, dict) else None
        if isinstance(row, dict) and int(row.get("calls", 0)) > 0:
            return {
                "pages": int(row.get("pages", 0)),
                "calls": int(row.get("calls", 0)),
                "failed": int(row.get("failed", 0)),
                "quota_errors": int(row.get("quota_errors", 0)),
            }
    except Exception:
        pass
    return None


# ── async resume: pending submissions ────────────────────────────────────────

_PENDING_TTL_S = 24 * 3600  # matches the 24h upload-link validity


def _pending_dir() -> Path:
    return Path(get_settings().mineru_cache_dir) / "pending"


def _pending_path(key: str) -> Path:
    return _pending_dir() / f"{key}.json"


def _pending_load(key: str) -> dict | None:
    """A still-runnable earlier submission for this exact (content, options).

    Returns None when there is no record, it is corrupt, or it is older than
    24h (the upload link and the server-side task are both gone by then).
    A stale record is deleted on sight so it can never be resumed again.
    """
    path = _pending_path(key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or not payload.get("batch_id"):
        return None
    submitted_at = payload.get("submitted_at")
    if not isinstance(submitted_at, (int, float)) or time.time() - submitted_at > _PENDING_TTL_S:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return payload


def _pending_save(key: str, batch_id: str, data_id: str) -> None:
    """Remember a submitted batch so a later call can resume it."""
    try:
        from . import _fsutil

        _pending_dir().mkdir(parents=True, exist_ok=True)
        _fsutil.atomic_write_text(
            _pending_path(key),
            json.dumps(
                {"batch_id": batch_id, "data_id": data_id,
                 "submitted_at": time.time()},
                ensure_ascii=False,
            ),
        )
    except Exception:
        # Resume is an optimisation; a lost record only costs a resubmission.
        pass


def _pending_clear(key: str) -> None:
    try:
        _pending_path(key).unlink(missing_ok=True)
    except OSError:
        pass


def _poll_batch(client, batch_id: str, timeout: int) -> list:
    """Poll get_batch until every result is done/failed or the budget ends.

    Mirrors the SDK's internal _wait_batch (2s first interval, doubling to
    30s) so a resumed poll behaves like the blocking call it replaces — but
    raises the SDK's TimeoutError on deadline, so the caller's except chain
    treats a timed-out poll exactly like a timed-out extract.
    """
    import mineru  # type: ignore

    deadline = time.monotonic() + timeout
    interval = 2.0
    while True:
        results = client.get_batch(batch_id)
        if all(getattr(r, "state", "") in ("done", "failed") for r in results):
            return results
        if time.monotonic() > deadline:
            raise mineru.TimeoutError(timeout, batch_id)
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
        interval = min(interval * 2, 30.0)


def parse_bytes(
    content: bytes,
    *,
    ext: str = "pdf",
    ocr: bool = False,
    timeout: float | None = None,
    formula: bool | None = None,
    table: bool | None = None,
    language: str | None = None,
) -> MinerUDocument | None:
    """Parse *content* through MinerU, or return None and let the caller cope.

    None covers every reason a call cannot or should not happen: disabled, no
    token, no SDK, unsupported format, oversized, quota gone, network down.
    The caller keeps whatever it already had.

    *timeout* overrides `mineru_timeout_s` for this call. A single escalated
    page and a whole scanned document are not the same upload, and a caller that
    is going to make twenty sequential calls needs a much shorter one than a
    caller making a single big one.

    *language* is the MinerU OCR language code (``ch``/``en``/``latin``/…)
    and is only sent when given; None leaves the server default. It only
    matters on OCR paths — text-layer pages are read, not OCR'd.
    """
    ext = (ext or "").lower().lstrip(".")
    available, hint = mineru_available()
    if not available:
        logger.debug("mineru unavailable: %s", hint)
        return None
    if ext not in SUPPORTED_EXTS:
        logger.debug("mineru: .%s is not an accepted format", ext)
        return None

    settings = get_settings()
    limit = settings.mineru_max_upload_mb * 1024 * 1024
    if len(content) > limit:
        # Refused locally on purpose: an upload that is going to be rejected
        # should not cost a round trip, and on a metered API it should not
        # risk costing quota either.
        logger.warning(
            "mineru: %.1f MB exceeds the %d MB limit — not sending",
            len(content) / 1024 / 1024, settings.mineru_max_upload_mb,
        )
        return None

    # P1-B6: resolve the effective options BEFORE the cache lookup, so the
    # key reflects what will actually be requested (previously the key was
    # computed first and the options resolved later, letting a structured
    # caller forcing formula=True/table=True share a key with a caller on
    # different settings defaults).
    eff_formula, eff_table = _extract_options(formula=formula, table=table)
    model = _model_for_ext(ext)
    key = _cache_key(content, ext=ext, ocr=ocr, formula=eff_formula, table=eff_table,
                     language=language, model=model)
    if not settings.prune_mineru_cache:
        cached = _cache_load(key)
        if cached is not None:
            logger.info("mineru: cache hit (%s)", key[:12])
            return cached

    document = _extract(
        content, ext=ext, ocr=ocr, timeout=timeout, formula=formula, table=table,
        language=language, model=model,
    )
    if document is not None and not settings.prune_mineru_cache:
        # Only successes are cached. Caching a failure would turn one network
        # blip into a permanently broken document.
        _cache_store(key, document)
    return document


def parse_pages_batch(
    contents: list[bytes],
    *,
    ext: str = "pdf",
    ocr: bool = False,
    timeout: float | None = None,
    formula: bool | None = None,
    table: bool | None = None,
    language: str | None = None,
) -> list[MinerUDocument | None]:
    """Parse several single-page PDFs in one batch submission.

    The point over ``parse_bytes`` called N times: one ``POST /file-urls/batch``,
    MinerU processes the pages in parallel, one poll loop. Returns one
    ``MinerUDocument`` per input in the same order, ``None`` where a page failed
    or could not be sent, so the caller keeps its local text for those pages
    exactly as it does today.

    Only pages that pass the local pre-flight (supported format, under the size
    limit, not served from cache) are uploaded; the rest come back ``None``
    without touching the network. A single batch failure degrades every page to
    ``None`` — the caller falls back to the local result, never to an error.

    ``ocr`` is False by default because the batch path only takes pages with a
    text layer (see ``hybrid_parse._escalate_pages_batch``); it exists so a
    caller with scanned pages can ask for OCR instead of silently getting
    empty results.
    """
    available, hint = mineru_available()
    if not available:
        logger.debug("mineru unavailable: %s", hint)
        return [None] * len(contents)
    ext = (ext or "").lower().lstrip(".")
    if ext not in SUPPORTED_EXTS:
        logger.debug("mineru: .%s is not an accepted format", ext)
        return [None] * len(contents)

    settings = get_settings()
    limit = settings.mineru_max_upload_mb * 1024 * 1024
    # P1-B6: resolve once, before any cache lookup, so cache keys reflect
    # the options that will actually be requested.
    eff_formula, eff_table = _extract_options(formula=formula, table=table)
    model = _model_for_ext(ext)
    results: list[MinerUDocument | None] = [None] * len(contents)
    accepted: list[int] = []
    paths: list[str] = []

    for i, content in enumerate(contents):
        if len(content) > limit:
            logger.warning(
                "mineru: page %d (%.1f MB) exceeds the %d MB limit — skipping",
                i, len(content) / 1024 / 1024, settings.mineru_max_upload_mb,
            )
            continue
        key = _cache_key(content, ext=ext, ocr=ocr, formula=eff_formula, table=eff_table,
                         language=language, model=model)
        if not settings.prune_mineru_cache:
            cached = _cache_load(key)
            if cached is not None:
                logger.info("mineru: cache hit (%s)", key[:12])
                results[i] = cached
                continue
        handle, path = tempfile.mkstemp(suffix=f".{ext}")
        with os.fdopen(handle, "wb") as fh:
            fh.write(content)
        paths.append(path)
        accepted.append(i)

    if not paths:
        return results

    import mineru  # type: ignore

    token = str(effective_setting(settings, "mineru_api_key") or "")
    wait = float(settings.mineru_batch_timeout_s if timeout is None else timeout)

    try:
        client = mineru.MinerU(token=token, base_url=setting_or_default(settings, "mineru_base_url"))
        batch_kwargs: dict = {
            # D-1: one data_id per page, keyed by the temp path the SDK
            # looks the FileParam up by.
            "file_params": {
                path: mineru.FileParam(
                    data_id=hashlib.sha256(contents[idx]).hexdigest()
                )
                for idx, path in zip(accepted, paths)
            },
        }
        if language is not None:
            # The SDK only sends `language` when explicitly set; passing None
            # would send a null to the server, so it is omitted, not passed.
            batch_kwargs["language"] = language
        documents = list(
            client.extract_batch(
                paths, model=model, ocr=ocr or None,
                formula=eff_formula, table=eff_table, timeout=int(wait),
                **batch_kwargs,
            )
        )
    except mineru.AuthError as exc:
        logger.error("mineru: token rejected (%s) — check 设置 → API 配置", exc)
        return results
    except mineru.QuotaExceededError as exc:
        logger.error("mineru: daily quota exhausted (%s) — falling back to local", exc)
        _record_usage(pages=0, ok=False, quota=True)
        return results
    except mineru.TimeoutError as exc:
        logger.warning("mineru: batch timed out after %ss (%s)", wait, exc)
        _record_usage(pages=len(paths), ok=False)
        return results
    except mineru.MinerUError as exc:
        return degrade_return(logger, exc, "mineru batch failed", results)
    except Exception as exc:
        if _is_auth_failure(exc):
            logger.error("mineru: token rejected (HTTP 401/403) — check 设置 → API 配置")
            return results
        return degrade_return(logger, exc, "mineru batch call failed", results)
    finally:
        for path in paths:
            try:
                os.unlink(path)
            except OSError:  # pragma: no cover - already gone
                pass

    # P2-B8: the batch result must align 1:1 with the submitted pages. zip
    # would silently drop the tail on a short result (or extra results on a
    # long one) and misattribute pages. On any mismatch, discard the whole
    # batch result — cache hits already in `results` are untouched, and
    # every other page keeps its local text.
    if len(documents) != len(accepted):
        logger.warning(
            "mineru: batch result count %d != submitted %d — discarding "
            "batch results, keeping local text",
            len(documents), len(accepted),
        )
        _record_usage(pages=len(paths), ok=False)
        return results

    for idx, document in zip(accepted, documents):
        if document is None or getattr(document, "state", "done") != "done":
            continue
        # P1-B5: one malformed document must not kill the whole batch.
        try:
            images: dict[str, bytes] = {}
            for image in getattr(document, "images", None) or []:
                # P3-B9: skip empty keys — an empty path/name used to
                # pollute images[""], and _normalise's fallback get("")
                # could then attach an unrelated image to an imageless block.
                for key in (getattr(image, "path", ""), getattr(image, "name", "")):
                    if key:
                        images[key] = image.data
            normalised = _normalise(document, images)
        except Exception as exc:
            logger.warning(
                "mineru: batch result for submitted page %d unusable (%s) — "
                "keeping local text",
                idx, exc,
            )
            continue
        results[idx] = normalised
        if not settings.prune_mineru_cache:
            _cache_store(
                _cache_key(contents[idx], ext=ext, ocr=ocr,
                           formula=eff_formula, table=eff_table,
                           language=language, model=model),
                normalised,
            )
    _record_usage(
        pages=len(paths),
        ok=all(results[idx] is not None for idx in accepted),
    )
    return results


def _resolve_model() -> str:
    """The MinerU model to request, pinned explicitly.

    The SDK infers ``vlm`` when ``model`` is omitted, so today every call
    already runs vlm — but implicitly. Pinning it makes the choice deliberate
    and gives the pipeline/vlm A/B a switch to turn. Only ``pipeline`` and
    ``vlm`` are valid here; the HTML model is not a setting (see
    ``_model_for_ext`` — the file type forces it).
    """
    raw = str(get_settings().mineru_model or "").strip().lower()
    if raw in ("pipeline", "vlm"):
        return raw
    logger.warning("mineru: unknown model %r — falling back to vlm", raw)
    return "vlm"


def _model_for_ext(ext: str, model: str | None = None) -> str:
    """The model for a source type. HTML sources always run MinerU-HTML:
    the dedicated HTML model is a property of the file type, not a user
    choice — the mineru_model setting governs PDF/Office/image only."""
    if ext in ("html", "htm"):
        return "html"
    return model or _resolve_model()


def _extract_options(
    *, formula: bool | None, table: bool | None
) -> tuple[bool, bool]:
    """Resolve formula/table recognition flags.

    The SDK only sends ``enable_formula``/``enable_table`` when explicitly
    set — omitting them leaves recognition at the server default, which is
    the cloud equivalent of the local pipeline's mfr_enable trap (§8.2).
    Explicit True/False from the caller wins; otherwise the config defaults
    (both on) apply. The effective pair is logged by the caller.
    """
    settings = get_settings()
    eff_formula = formula if formula is not None else settings.mineru_formula_enabled
    eff_table = table if table is not None else settings.mineru_table_enabled
    return eff_formula, eff_table


def _extract(
    content: bytes,
    *,
    ext: str,
    ocr: bool,
    timeout: float | None = None,
    formula: bool | None = None,
    table: bool | None = None,
    language: str | None = None,
    model: str | None = None,
) -> MinerUDocument | None:
    import mineru  # type: ignore

    settings = get_settings()
    token = str(effective_setting(settings, "mineru_api_key") or "")
    wait = float(settings.mineru_timeout_s if timeout is None else timeout)
    eff_formula, eff_table = _extract_options(formula=formula, table=table)
    model = _model_for_ext(ext, model)
    pages = _count_pages(content, ext)
    # D-1: the content hash as data_id — a stable, opaque trace ID the cloud
    # can show in its own logs, and the key the async-resume pending record
    # uses to find this submission again. 64 hex chars: within the 128-char
    # limit and the [A-Za-z0-9_.-] charset.
    data_id = hashlib.sha256(content).hexdigest()
    # P3-B10: ocr=False does NOT force OCR off — "ocr or None" below sends
    # None, leaving the decision to the MinerU server default (the SDK
    # default is not pinned down in this repo, so callers must not read
    # False as "OCR disabled"). The log shows the value actually sent.
    # Language is the same shape: only sent when the caller detected one —
    # the SDK would forward an explicit None to the server.
    logger.info(
        "mineru: extract options model=%s formula=%s table=%s ocr=%s language=%s (None = server default)",
        model,
        eff_formula,
        eff_table,
        ocr if ocr else None,
        language,
    )

    handle, temp_path = tempfile.mkstemp(suffix=f".{ext}")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(content)

        client = mineru.MinerU(token=token, base_url=setting_or_default(settings, "mineru_base_url"))
        extract_kwargs: dict = {
            "file_params": {temp_path: mineru.FileParam(data_id=data_id)},
        }
        if language is not None:
            extract_kwargs["language"] = language
        key = _cache_key(content, ext=ext, ocr=ocr, formula=eff_formula,
                         table=eff_table, language=language, model=model)
        pending = _pending_load(key)
        if pending is not None:
            logger.info(
                "mineru: resuming pending batch %s (submitted %.0fs ago)",
                pending["batch_id"],
                time.time() - float(pending.get("submitted_at", 0) or 0),
            )
            try:
                results = _poll_batch(client, pending["batch_id"], timeout=int(wait))
            except mineru.TaskNotFoundError:
                # The server no longer has this task (expired) — drop the
                # stale record and submit fresh below.
                logger.warning(
                    "mineru: pending batch %s expired server-side — resubmitting",
                    pending["batch_id"],
                )
                _pending_clear(key)
                pending = None
        if pending is None:
            # submit takes no timeout: it only hands the file over. The
            # pending record goes down *before* polling, so a timeout below
            # leaves it for the next call to resume instead of resubmitting.
            batch_id = client.submit(
                temp_path,
                model=model,
                ocr=ocr or None,
                formula=eff_formula,
                table=eff_table,
                **extract_kwargs,
            )
            _pending_save(key, batch_id, data_id)
            results = _poll_batch(client, batch_id, timeout=int(wait))
        result = results[0] if results else None
        state = getattr(result, "state", "?") if result is not None else "missing"
        error = getattr(result, "error", "") if result is not None else ""
        if result is None or state != "done":
            logger.warning("mineru: task finished in state %r (%s)", state, error)
            _pending_clear(key)
            _record_usage(pages=pages, ok=False)
            return None
        _pending_clear(key)

        images = {}
        for image in getattr(result, "images", None) or []:
            # P3-B9: same empty-key skip as the batch path above.
            for key in (getattr(image, "path", ""), getattr(image, "name", "")):
                if key:
                    images[key] = image.data
        _record_usage(pages=pages, ok=True)
        return _normalise(result, images)

    except mineru.AuthError as exc:
        # Actionable and distinct: the operator needs to replace a token, not
        # debug a parser.
        logger.error("mineru: token rejected (%s) — check 设置 → API 配置", exc)
        return None
    except mineru.QuotaExceededError as exc:
        logger.error("mineru: daily quota exhausted (%s) — falling back to local", exc)
        _record_usage(pages=0, ok=False, quota=True)
        return None
    except (mineru.FileTooLargeError, mineru.PageLimitError) as exc:
        logger.warning("mineru: document rejected by size/page limit (%s)", exc)
        return None
    except mineru.TimeoutError as exc:
        # The pending record stays: the task is still running server-side,
        # and the next call resumes it instead of resubmitting.
        logger.warning("mineru: timed out after %ss (%s)", wait, exc)
        _record_usage(pages=pages, ok=False)
        return None
    except mineru.MinerUError as exc:
        return degrade_return(logger, exc, "mineru extract failed", None)
    except Exception as exc:
        if _is_auth_failure(exc):
            logger.error("mineru: token rejected (HTTP 401/403) — check 设置 → API 配置")
            return None
        return degrade_return(logger, exc, "mineru call failed", None)
    finally:
        # The uploaded file is proprietary formulation data. Delete it whatever
        # happened, including on the exception paths above.
        try:
            os.unlink(temp_path)
        except OSError:  # pragma: no cover - already gone
            pass


def crawl_url(url: str, *, timeout: float | None = None) -> MinerUDocument | None:
    """Fetch a public web page through MinerU's crawl (``extract`` with model=html).

    Returns the parsed document, or None when MinerU is unavailable, the
    crawl fails, or the poll budget runs out. None is the caller's cue to
    fall back to the local HTML conversion — the behaviour this replaces.

    The URL itself is never fetched locally: MinerU's server does the
    fetching. Two consequences follow. First, the SSRF pre-flight is the
    caller's job — ``ingest_url`` runs ``is_safe_url`` before this is
    called, so a private URL never reaches a third-party fetcher; MinerU's
    servers cannot reach this deployment's RFC 1918 / localhost addresses
    in any case. Second, the result is deliberately NOT cached: a page can
    change, and a 30-day parse cache would serve a stale page. The pending
    record (submit+poll resume, batch 3) still applies, keyed on the URL.
    """
    url = (url or "").strip()
    available, hint = mineru_available()
    if not available:
        logger.debug("mineru unavailable: %s", hint)
        return None
    if not url:
        return None

    import mineru  # type: ignore

    settings = get_settings()
    wait = float(settings.mineru_timeout_s if timeout is None else timeout)
    eff_formula, eff_table = _extract_options(formula=None, table=None)
    model = "html"
    # A URL is not a legal data_id (it contains :/?&), so the trace ID is
    # the URL's hash, same shape as the content hash used for files.
    data_id = hashlib.sha256(url.encode("utf-8")).hexdigest()
    # The key is only a pending-record fingerprint here — the parse cache is
    # never consulted for URLs (see the docstring).
    key = _cache_key(url.encode("utf-8"), ext="html", ocr=False, formula=eff_formula,
                     table=eff_table, language=None, model=model)
    logger.info(
        "mineru: crawl options model=%s formula=%s table=%s url=%.120s",
        model, eff_formula, eff_table, url,
    )
    token = str(effective_setting(settings, "mineru_api_key") or "")
    client = mineru.MinerU(token=token, base_url=setting_or_default(settings, "mineru_base_url"))
    try:
        pending = _pending_load(key)
        if pending is not None:
            logger.info(
                "mineru: resuming pending crawl %s (submitted %.0fs ago)",
                pending["batch_id"],
                time.time() - float(pending.get("submitted_at", 0) or 0),
            )
            try:
                results = _poll_batch(client, pending["batch_id"], timeout=int(wait))
            except mineru.TaskNotFoundError:
                logger.warning(
                    "mineru: pending crawl %s expired server-side — resubmitting",
                    pending["batch_id"],
                )
                _pending_clear(key)
                pending = None
        if pending is None:
            # submit takes no timeout: it only hands the URL over. The
            # pending record goes down *before* polling, so a timeout below
            # leaves it for the next call to resume instead of resubmitting.
            # A crawl is one page for quota purposes.
            batch_id = client.submit(
                url,
                model=model,
                formula=eff_formula,
                table=eff_table,
                file_params={url: mineru.FileParam(data_id=data_id)},
            )
            _pending_save(key, batch_id, data_id)
            results = _poll_batch(client, batch_id, timeout=int(wait))
        result = results[0] if results else None
        state = getattr(result, "state", "?") if result is not None else "missing"
        error = getattr(result, "error", "") if result is not None else ""
        if result is None or state != "done":
            logger.warning("mineru: crawl finished in state %r (%s)", state, error)
            _pending_clear(key)
            _record_usage(pages=1, ok=False)
            return None
        _pending_clear(key)

        images = {}
        for image in getattr(result, "images", None) or []:
            # P3-B9: same empty-key skip as the batch path above.
            for image_key in (getattr(image, "path", ""), getattr(image, "name", "")):
                if image_key:
                    images[image_key] = image.data
        _record_usage(pages=1, ok=True)
        return _normalise(result, images)

    except mineru.AuthError as exc:
        logger.error("mineru: token rejected (%s) — check 设置 → API 配置", exc)
        return None
    except mineru.QuotaExceededError as exc:
        logger.error("mineru: daily quota exhausted (%s) — falling back to local", exc)
        _record_usage(pages=0, ok=False, quota=True)
        return None
    except (mineru.FileTooLargeError, mineru.PageLimitError) as exc:
        logger.warning("mineru: crawl rejected by size/page limit (%s)", exc)
        return None
    except mineru.TimeoutError as exc:
        # The pending record stays: the crawl is still running server-side,
        # and the next call resumes it instead of resubmitting.
        logger.warning("mineru: crawl timed out after %ss (%s)", wait, exc)
        _record_usage(pages=1, ok=False)
        return None
    except mineru.MinerUError as exc:
        return degrade_return(logger, exc, "mineru crawl failed", None)
    except Exception as exc:
        if _is_auth_failure(exc):
            logger.error("mineru: token rejected (HTTP 401/403) — check 设置 → API 配置")
            return None
        return degrade_return(logger, exc, "mineru crawl call failed", None)


def probe_token() -> tuple[bool, str]:
    """Cheapest possible authentication check, for the Settings test button.

    MinerU publishes no token-validation or quota endpoint, so this asks for a
    task that cannot exist. A rejected token raises AuthError; anything else —
    including "no such task" — means the token was accepted.
    """
    settings = get_settings()
    token = str(effective_setting(settings, "mineru_api_key") or "")
    if not token:
        return False, "未配置 MinerU API Token"
    if not optional_import("mineru"):
        return False, "未安装 mineru-open-sdk（pip install mineru-open-sdk）"

    import mineru  # type: ignore

    try:
        client = mineru.MinerU(token=token, base_url=setting_or_default(settings, "mineru_base_url"))
        client.get_task("00000000-0000-0000-0000-000000000000")
        return True, "Token 有效"
    except mineru.AuthError as exc:
        return False, f"Token 无效或已过期：{exc}"
    except mineru.TaskNotFoundError:
        # The expected answer: authenticated, and the task genuinely is absent.
        return True, "Token 有效"
    except mineru.MinerUError as exc:
        return True, f"Token 已接受（服务返回：{exc}）"
    except Exception as exc:
        if _is_auth_failure(exc):
            return False, "Token 无效或已过期（服务返回 401）"
        return False, f"连接失败：{exc}"
