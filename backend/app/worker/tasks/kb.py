"""Knowledge-base tasks: ingest, reparse, ToC summaries, enrichment."""
from __future__ import annotations

from ...services.errors import degrade_return
from ...services.errors import log_handled_exception
from ..celery_app import celery_app
from ..task_progress import TaskProgressStatus
from ..task_progress import persist_result
from ..task_progress import publish_progress
from celery.exceptions import SoftTimeLimitExceeded
from pathlib import Path
import threading
import uuid

from ._common import _OutboxTask, _persist_terminal, logger, task_manager



# ── async KB ingest (search → fulltext → knowledge base, in background) ──────


def _kb_ingest_project_id(payload: dict) -> str | None:
    pid = (payload.get("project_id") or "").strip()
    if pid:
        return pid
    req = payload.get("requirement")
    if isinstance(req, dict):
        return (req.get("project_id") or "").strip() or None
    return None



def _kb_ingest_impl(task_id: str, payload: dict) -> dict:
    """Shared body for the Celery task and the eager-mode background thread."""
    from ...domain.schemas import Evidence
    from ...services import kb_ingest

    evidence = [Evidence.model_validate(e) for e in payload.get("evidence") or []]
    docs_state: dict[str, dict] = {}
    order: list[str] = []
    # Fetching runs on a thread pool, so this callback is now re-entrant. Without
    # the lock, `order.append` landing before `docs_state[ident]` is set lets
    # another thread's comprehension below raise KeyError on the id it just saw
    # appended — which would surface as random documents "failing" for no reason.
    state_lock = threading.Lock()

    def status_cb(meta: dict) -> None:
        ident = meta["identifier"]
        with state_lock:
            docs_state[ident] = dict(meta)   # visible before it is announced
            if ident not in order:
                order.append(ident)
            docs = [docs_state[i] for i in order]
        done = sum(1 for d in docs if d["status"] in kb_ingest.TERMINAL_STATES)
        total = len(docs)
        active = next((d for d in docs if d["status"] in ("fetching", "indexing")), None)
        if active:
            verb = "获取全文" if active["status"] == "fetching" else "解析入库"
            msg = f"知识库构建 {done}/{total} · 正在{verb}：{active['title'][:60]}"
        else:
            msg = f"知识库构建 {done}/{total}"
        publish_progress(
            task_id,
            TaskProgressStatus.RUNNING,
            stage="kb_ingest",
            message=msg,
            progress=(done / total) if total else 0.0,
            data={
                "docs": docs,
                "done": done,
                "total": total,
                "indexed": sum(1 for d in docs if d["status"] == "indexed"),
                "failed": sum(1 for d in docs if d["status"] == "failed"),
            },
        )

    try:
        result = kb_ingest.ingest_evidence_docs(
            evidence,
            status_cb=status_cb,
            project_id=_kb_ingest_project_id(payload),
            query=payload.get("query"),
        )
        summary = (
            f"知识库构建完成：入库 {result['indexed']} 篇"
            + (f"，已在库 {result['skipped']} 篇" if result["skipped"] else "")
            + (f"，失败 {result['failed']} 篇" if result["failed"] else "")
        )
        persist_result(task_id, result, failed=False)
        _persist_terminal(task_id, "kb_ingest", result, message=summary)
        return result
    except SoftTimeLimitExceeded:
        # The soft limit exists so the task can say what happened instead of
        # being killed mid-sentence. Without this it surfaced as a bare
        # exception name, which tells the operator nothing about which knob to
        # turn — and the knob is the point, since a large corpus legitimately
        # takes longer than any default.
        from ...config import get_settings

        limit = get_settings().celery_soft_time_limit_s
        msg = (
            f"知识库构建超过单任务时限（{limit // 60} 分钟）已被中止。"
            "已入库的部分保留，可再次运行继续；"
            "若语料确实很大，请调高 FORMUNEX_CELERY_SOFT_TIME_LIMIT_S "
            "（同时调高 FORMUNEX_CELERY_HARD_TIME_LIMIT_S）。"
        )
        logger.error("kb_ingest hit the %ds soft time limit", limit)
        err = {"error": msg, "soft_time_limit_s": limit}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "kb_ingest", err, failed=True, message=msg)
        raise
    except Exception as exc:
        logger.exception("kb_ingest task failed")
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "kb_ingest", err, failed=True, message=str(exc))
        raise



@celery_app.task(bind=True, name="formunex.kb_ingest")
def run_kb_ingest_task(self, payload: dict) -> dict:
    return _kb_ingest_impl(self.request.id, payload)



# ── PageIndex ToC node summaries ─────────────────────────────────────────────
# Triggered explicitly per document (POST /kb/sources/{id}/toc-tree/summarize);
# never a full-library sweep (LLM cost). Retries cover transient provider
# outages; per-node failures land the document in "partial", not a failed job.


@celery_app.task(bind=True, base=_OutboxTask, outbox_operation="toc_summaries", name="formunex.toc_summaries", max_retries=3,
                 autoretry_for=(ConnectionError, TimeoutError, OSError),
                 retry_backoff=True)
def run_toc_summaries_task(self, payload: dict) -> dict:
    from ...services.pageindex.summaries import generate_summaries

    return generate_summaries(payload["source_id"])



# ── PubChem GHS enrichment ─────────────────────────────────────────────────
# 补齐计划 1-6: per-substance PUG-View fetch → substance_classifications with
# source='pubchem'. PubChemUnavailable (transport / 5xx) retries with
# backoff; PubChemNotFound (no CID / no GHS section) is terminal — a
# definitive answer, not a transient failure.


@celery_app.task(bind=True, base=_OutboxTask, outbox_operation="pubchem_enrich", name="formunex.pubchem_enrich", max_retries=3,
                 autoretry_for=(ConnectionError, TimeoutError, OSError),
                 retry_backoff=True, retry_backoff_max=600)
def run_pubchem_enrich_task(self, payload: dict) -> dict:
    from ...services.plm.regulatory.pubchem_enrich import (
        PubChemUnavailable,
        enrich_substance_by_id,
    )

    try:
        return enrich_substance_by_id(payload["substance_id"])
    except PubChemUnavailable as exc:
        # Re-raise as ConnectionError so Celery's autoretry picks it up.
        raise ConnectionError(str(exc)) from exc



# ── Async file ingest (upload → 202 → SSE) ───────────────────────────────────
# Parsing a scanned PDF is minutes of OCR; holding the HTTP request open while
# that runs is what let an intermediary in front of uvicorn cut the connection
# (the client saw a 502 even though the backend finished the work). The route
# now writes the upload to a temp dir, returns 202, and this task does the
# parse + persist + index off the request path.

@celery_app.task(bind=True, name="formunex.file_ingest")
def run_file_ingest_task(self, payload: dict) -> dict:
    return _file_ingest_impl(self.request.id, payload)



def _file_ingest_impl(task_id: str, payload: dict) -> dict:
    """Shared body for the Celery task and the eager-mode background thread.

    Everything that can fail sits inside one ``try``: the per-file parse loop used to run before it, so a parser that
    raised (``ParserUnavailable`` - no PDF parser installed) left the task at ``running`` for ever and the uploaded
    files in their temp directory; only a failure while indexing was recorded.
    """
    try:
        return _file_ingest_body(task_id, payload)
    except Exception as exc:
        logger.exception("file ingest task failed")
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "file_ingest", err, failed=True, message=str(exc))
        raise
    finally:
        upload_dir = payload.get("dir")
        if upload_dir:
            try:
                import shutil

                shutil.rmtree(upload_dir, ignore_errors=True)
            except Exception as exc:  # noqa: BLE001
                logger.debug("file ingest: temp cleanup skipped: %s", exc)



def _file_ingest_body(task_id: str, payload: dict) -> dict:
    """The work of a file-ingest job; ``_file_ingest_impl`` owns its failure and cleanup."""
    from ...services import colbert_store
    from ...services.ingestion import ingest_files_batch

    import hashlib

    # v3 P1 H-6 full: streaming pipeline — read → hash → dedup → ingest →
    # release, one file at a time. Peak memory = single file, not the batch.
    #
    # Root cause of the old 800MB peak: the `files` list held ALL bytes until
    # return; ingestion.py's `del content` only dropped the loop-local name
    # while the list kept every reference alive (M-15 was a no-op).
    # Now each file goes through ingest_files_batch as a single-item batch
    # (reusing all its merge/error handling), then `del content` truly frees.
    file_items = list(payload.get("files") or [])
    project_id = str(payload.get("project_id") or "").strip() or None
    total_files = len(file_items)

    duplicates: list[str] = []
    duplicate_source_ids: dict[str, str] = {}
    all_evidence: list = []
    all_warnings: list[str] = []
    files_processed = 0
    last_source_id = None
    last_extraction_status = "skipped"

    # Dedup store: fetched once; a failure skips dedup, never blocks ingest.
    try:
        from ...db.source_store import get_source_store

        _store = get_source_store()
    except Exception as exc:  # noqa: BLE001
        logger.warning("file ingest: dedup store unavailable, dedup skipped (%s)", exc)
        _store = None

    for idx, item in enumerate(file_items, 1):
        name = item["name"]
        path = Path(item["path"])
        try:
            content = path.read_bytes()
        except OSError as exc:
            logger.warning("file ingest: cannot read %s (%s)", path, exc)
            continue

        # Content-hash dedup (per-file). Key semantics unchanged:
        # `upload:sha256:<bytes>`; a row whose last ingest failed stays a
        # retry candidate (include_failed=False), otherwise a file could
        # never be re-ingested.
        digest = hashlib.sha256(content).hexdigest()
        key = f"upload:sha256:{digest}"
        try:
            existing = (
                _store.find_by_origin_url(key, include_failed=False)
                if _store is not None
                else None
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("file ingest: dedup check failed for %s (%s)", name, exc)
            existing = None
        if existing:
            logger.info("file ingest: duplicate upload skipped: %s (%s)", name, key)
            duplicates.append(name)
            duplicate_source_ids[name] = existing.id
            del content
            continue

        publish_progress(
            task_id,
            TaskProgressStatus.RUNNING,
            stage="ingest",
            message=f"解析 [{idx}/{total_files}] {name}…",
            progress=(idx - 1) / max(total_files, 1),
        )
        try:
            outcome = ingest_files_batch(
                [(name, content)], origin_url_by_name={name: key}
            )
        finally:
            # Truly release this file's bytes (the single-item list dies
            # with the call frame).
            del content

        all_evidence.extend(outcome.evidence)
        all_warnings.extend(f"{name}：{w}" for w in (outcome.warnings or []))
        files_processed += 1
        if outcome.source_id:
            last_source_id = outcome.source_id
            # Project-scoped upload: stamp the new source (fail-open — a global
            # row is still a usable row).
            if project_id and _store is not None:
                try:
                    _store.update_fields(outcome.source_id, project_id=project_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("file ingest: project scope skipped for %s (%s)", name, exc)
            # PageIndex 借鉴 P6: 源文件落盘 —— 持久化原文件供"新页面打开"下载。
            # Fail-open: 落盘失败不影响入库。
            try:
                from ...services.source_files import persist_source_file
                persist_source_file(outcome.source_id, path, name)
            except Exception as exc:  # noqa: BLE001
                logger.debug("source file persist skipped for %s: %s", name, exc)
        last_extraction_status = outcome.extraction_status

    if files_processed == 0:
        # Every file was a duplicate (or unreadable): finishing cleanly with
        # zero evidence is the honest answer, and the UI reports it as a skip
        # instead of the old "入库失败" that a retry used to produce.
        result = {
            "evidence": [],
            "total": 0,
            "files_processed": 0,
            "source_id": None,
            "extraction_status": "skipped",
            "duplicates": duplicates,
            "duplicate_source_ids": duplicate_source_ids,
        }
        message = f"跳过 {len(duplicates)} 个重复文件（内容与库中已有资料一致）"
        # Terminal snapshot first, result store second: the reverse order left a
        # window where a concurrent GET saw a result in the progress meta while
        # the disk snapshot was still `pending`, and it persisted that pending
        # snapshot over the terminal one — after which SSE waited on pub/sub
        # instead of replaying COMPLETED.
        _persist_terminal(task_id, "file_ingest", result, message=message)
        persist_result(task_id, result, failed=False)
        return result

    publish_progress(
        task_id,
        TaskProgressStatus.RUNNING,
        stage="index",
        message="建立检索索引…",
        progress=0.95,
    )
    # P0-1: "skipped" 仅=无真实文本（see api/ingest.py）。
    if last_extraction_status != "skipped":
        colbert_store.index_evidence(all_evidence)
    result = {
        "evidence": [e.model_dump() for e in all_evidence],
        "total": len(all_evidence),
        "files_processed": files_processed,
        "source_id": last_source_id,
        "extraction_status": last_extraction_status,
        "duplicates": duplicates,
        "duplicate_source_ids": duplicate_source_ids,
        # P2: 解析截断等提示 —— 前端上传完成态展示。
        "warnings": all_warnings,
    }
    message = (
        f"文件入库完成：{len(all_evidence)} 条"
        if all_evidence
        else "文件入库完成（未提取到文本）"
    )
    if duplicates:
        message += f"，跳过 {len(duplicates)} 个重复文件"
    _persist_terminal(task_id, "file_ingest", result, message=message)
    persist_result(task_id, result, failed=False)
    return result



def dispatch_file_ingest(
    files: list[dict], *, upload_dir: str | None = None, project_id: str | None = None
) -> str | None:
    """Enqueue an upload ingest job; return the task id for SSE/status polling.

    ``files`` is ``[{"name": ..., "path": ...}]`` — payloads stay on disk so a
    multi-megabyte PDF is never serialised into the broker. ``project_id``
    scopes the new KB sources to a project (None = global corpus).
    """
    from ...config import get_settings

    if not files:
        return None
    payload: dict = {"files": files, "dir": upload_dir}
    if (project_id or "").strip():
        payload["project_id"] = project_id.strip()
    try:
        if get_settings().celery_eager:
            task_id = f"fileingest-{uuid.uuid4().hex[:16]}"
            task_manager.register_celery_task(task_id, "file_ingest")
            from ..eager_pool import submit as _eager_submit

            _eager_submit(_safe_file_ingest, "file-ingest", task_id, payload)
            return task_id
        async_result = run_file_ingest_task.delay(payload)
        task_manager.register_celery_task(async_result.id, "file_ingest")
        return async_result.id
    except Exception as exc:
        return degrade_return(logger, exc, "file ingest dispatch failed", None)



def _safe_file_ingest(task_id: str, payload: dict) -> None:
    try:
        _file_ingest_impl(task_id, payload)
    except Exception as exc:  # already persisted as failed inside the impl
        log_handled_exception(logger, exc, "file ingest background thread")



def dispatch_kb_ingest(
    evidence_dicts: list[dict], *,
    project_id: str | None = None, query: str | None = None,
    domain=None,
) -> str | None:
    """Fire-and-forget background KB build for freshly searched evidence.

    Returns the ingest task id (for the frontend to stream), or None when the
    feature is off / nothing in the list is fetchable.  With a real broker the
    job goes through Celery; in eager mode ``.delay()`` would run inline and
    stall the parent task, so a daemon thread provides the same non-blocking
    behaviour for single-process deployments.
    """
    from ...config import get_settings
    from ...domain.schemas import Evidence
    from ...services import kb_ingest

    if not kb_ingest.ingest_enabled() or not evidence_dicts:
        return None
    try:
        rows = [Evidence.model_validate(e) for e in evidence_dicts]
        targets = kb_ingest.select_ingest_targets(
            rows, project_id=project_id, query=query, domain=domain
        )
    except Exception as exc:
        return degrade_return(logger, exc, "kb_ingest target selection failed", None)
    if not targets:
        return None

    payload = {
        "evidence": [ev.model_dump() for ev, _ in targets],
        "project_id": project_id,
        "query": query,
        "domain": str(domain) if domain is not None else None,
    }
    if get_settings().celery_eager:
        task_id = f"kbingest-{uuid.uuid4().hex[:16]}"
        task_manager.register_celery_task(task_id, "kb_ingest")
        from ..eager_pool import submit as _eager_submit

        _eager_submit(_safe_kb_ingest, "kb-ingest", task_id, payload)
        return task_id
    async_result = run_kb_ingest_task.delay(payload)
    task_manager.register_celery_task(async_result.id, "kb_ingest")
    return async_result.id



def _safe_kb_ingest(task_id: str, payload: dict) -> None:
    try:
        _kb_ingest_impl(task_id, payload)
    except Exception as exc:  # already persisted as failed inside the impl
        log_handled_exception(logger, exc, "kb_ingest background thread")



@celery_app.task(bind=True, base=_OutboxTask, outbox_operation="kg_relations_rebuild", name="formunex.kg_relations_rebuild", soft_time_limit=5400, time_limit=7200)
def run_kg_relations_rebuild(self, payload: dict) -> dict:
    """补语义关系提取: 实体/提及已在库, 只重跑关系(LLM 慢).

    Top-5′ #4: ``force=True`` 默认，不要求 kg_relation_extract_enabled。
    ``limit`` 限制源数量（默认 50）。
    """
    task_id = self.request.id
    try:
        from ...services.kg.entity_linker import rebuild_relations

        source_id = payload.get("source_id")
        limit = payload.get("limit")
        force = bool(payload.get("force", True))
        result = rebuild_relations(
            [source_id] if source_id else None,
            limit=int(limit) if limit is not None else None,
            force=force,
        )
        return {
            "task_id": task_id,
            "source_id": source_id,
            **result,
        }
    except Exception as exc:
        logger.exception("kg relations rebuild failed")
        return {"task_id": task_id, "error": str(exc)}



# ── P0-1: 全文下载失败重试队列（2026-10-11）─────────────────────────────
# 指数退避：60s → 300s → 900s，最多 3 次。成功后走正常 persist 流程。
@celery_app.task(
    bind=True,
    max_retries=3,
    name="formunex.fulltext_retry",
)
def retry_fulltext_fetch(self, payload: dict) -> dict:
    """重试一次失败的全文下载。

    payload: {"identifier": str, "source": str, "kind": str,
              "project_id": str|None, "title": str|None, "url": str|None}
    """
    from ...domain.schemas import Evidence
    from ...services import fulltext_fetcher as ff

    identifier = payload.get("identifier", "")
    kind = payload.get("kind", "")
    logger.info(
        "fulltext_retry attempt %d/%d for %s",
        self.request.retries + 1, 3, identifier,
    )
    try:
        ev = Evidence(
            source=payload.get("source", ""),
            identifier=identifier,
            title=payload.get("title") or identifier,
            snippet="",
            relevance=0.9,
            url=payload.get("url"),
        )
        hit = ff._dispatch_fetch(kind, ev, timeout=30.0)
        if not hit or not (hit.text or "").strip():
            raise RuntimeError("retry fetch returned empty")
        source_id = ff._persist_fulltext(
            hit.text, ev, kind,
            project_id=payload.get("project_id"),
            acquisition=ff._last_acquisition(),
            pdf_bytes=hit.pdf_bytes,
            tables=hit.tables,
        )
        return {"ok": True, "source_id": source_id, "identifier": identifier}
    except Exception as exc:
        # 指数退避：60s, 300s, 900s
        countdown = [60, 300, 900][min(self.request.retries, 2)]
        logger.warning(
            "fulltext_retry failed for %s (attempt %d): %s — retry in %ds",
            identifier, self.request.retries + 1, exc, countdown,
        )
        # Bug 修复：self.retry() 抛 Retry 异常表示"将重试"，不能被 except 吞掉。
        # max_retries=3 耗尽时 self.retry 会抛原异常，此时才返回失败。
        if self.request.retries >= 3:
            logger.error("fulltext_retry exhausted for %s: %s", identifier, exc)
            return {"ok": False, "identifier": identifier, "error": str(exc)[:200]}
        raise self.retry(exc=exc, countdown=countdown)



def enqueue_fulltext_retry(
    identifier: str, source: str, kind: str,
    project_id: str | None = None, title: str | None = None,
    url: str | None = None,
) -> str | None:
    """下载失败时入重试队列。返回 task id，失败时返回 None（fail-open）。"""
    try:
        from ...config import get_settings
        if get_settings().celery_eager:
            # eager 模式下直接同步重试一次（避免测试/单机环境堆积）
            return None
        payload = {
            "identifier": identifier, "source": source, "kind": kind,
            "project_id": project_id, "title": title, "url": url,
        }
        result = retry_fulltext_fetch.delay(payload)
        logger.info("fulltext_retry enqueued for %s: %s", identifier, result.id)
        return result.id
    except Exception as exc:
        logger.warning("enqueue_fulltext_retry failed (fail-open): %s", exc)
        return None



# ── P2-8: chunk 质量抽检（每天 1 次，由 beat 调度或手动触发）──────────────
@celery_app.task(bind=True, name="formunex.chunk_quality_sample")
def run_chunk_quality_sample(self, n: int = 20) -> dict:
    """定期 chunk 质量抽检。fail-open，不阻塞。"""
    from ...services import chunk_quality as cq
    try:
        return cq.run_quality_sample(n=n)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}



# ── KB: re-parse one source from its retained original file ────────────────
def _kb_reparse_impl(task_id: str, payload: dict) -> dict:
    from ...services.kb_reparse import reparse_source

    source_id = str(payload.get("source_id") or "").strip()
    try:
        publish_progress(
            task_id, TaskProgressStatus.RUNNING, stage="reparse",
            message="重新解析…", progress=0.02,
        )

        def _progress(msg: str, frac: float) -> None:
            publish_progress(
                task_id, TaskProgressStatus.RUNNING, stage="reparse",
                message=msg, progress=frac,
            )

        result = reparse_source(source_id, progress=_progress)
    except Exception as exc:
        logger.exception("kb reparse failed for %s", source_id)
        err = {"error": str(exc), "source_id": source_id}
        persist_result(task_id, err, failed=True, message=str(exc))
        _persist_terminal(task_id, "kb_reparse", err, failed=True, message=str(exc))
        raise
    message = f"重新解析完成：{result.get('chunks', 0)} 个分块"
    _persist_terminal(task_id, "kb_reparse", result, message=message)
    persist_result(task_id, result, failed=False)
    return result



@celery_app.task(bind=True, base=_OutboxTask, outbox_operation="kb_reparse", name="formunex.kb_reparse")
def run_kb_reparse_task(self, payload: dict) -> dict:
    return _kb_reparse_impl(self.request.id, payload)



def _url_ingest_impl(task_id: str, payload: dict) -> dict:
    """Stage 3 / SR-026: ``POST /ingest/url`` worker body (was inline).

    Same semantics as the old sync route: fetch + parse, ColBERT index
    unless the outcome is skipped. SSRF pre-flight stays in the route
    (sync 400) — an unsafe URL never enters the worker queue.
    """
    # Lazy: app.api.ingest imports worker.tasks at module level.
    from ...api.ingest import _to_ingest_response
    from ...services import colbert_store
    from ...services.ingestion import ingest_url

    url = str(payload.get("url") or "").strip()
    try:
        publish_progress(
            task_id, TaskProgressStatus.RUNNING, stage="ingest",
            message="抓取中…", progress=0.02,
        )
        outcome = ingest_url(url)
        if outcome.extraction_status != "skipped":
            colbert_store.index_evidence(outcome.evidence)
        result = _to_ingest_response(url, outcome).model_dump(mode="json")
    except Exception as exc:
        logger.exception("url ingest failed for %s", url)
        err = {"error": str(exc), "url": url}
        persist_result(task_id, err, failed=True, message=str(exc))
        _persist_terminal(task_id, "url_ingest", err, failed=True, message=str(exc))
        raise
    _persist_terminal(task_id, "url_ingest", result, message="入库完成")
    persist_result(task_id, result, failed=False)
    return result



@celery_app.task(bind=True, base=_OutboxTask, outbox_operation="url_ingest", name="formunex.url_ingest")
def run_url_ingest_task(self, payload: dict) -> dict:
    return _url_ingest_impl(self.request.id, payload)



def _task_ingest_impl(task_id: str, payload: dict) -> dict:
    """Stage 3 / SR-026: ``POST /ingest/task`` worker body (was inline).

    A resolve failure (``outcome.error``) is a task failure, not a 502:
    the client polls the task id and sees the message, and the outbox
    row records it. Input-shape errors (bad doc_type, non-URL web
    identifier) stay sync 400s in the route.
    """
    from ...api.ingest import _to_ingest_response
    from ...services import colbert_store
    from ...services.document_task import resolve_document

    doc_type = str(payload.get("doc_type") or "").strip().lower()
    identifier = str(payload.get("identifier") or "").strip()
    try:
        publish_progress(
            task_id, TaskProgressStatus.RUNNING, stage="ingest",
            message="抓取中…", progress=0.02,
        )
        outcome = resolve_document(doc_type, identifier)
        if outcome.error:
            raise RuntimeError(outcome.error)
        if outcome.extraction_status != "skipped":
            colbert_store.index_evidence(outcome.evidence)
        result = _to_ingest_response(outcome.identifier, outcome).model_dump(mode="json")
    except Exception as exc:
        logger.exception("task ingest failed for %s %s", doc_type, identifier)
        err = {"error": str(exc), "doc_type": doc_type, "identifier": identifier}
        persist_result(task_id, err, failed=True, message=str(exc))
        _persist_terminal(task_id, "task_ingest", err, failed=True, message=str(exc))
        raise
    _persist_terminal(task_id, "task_ingest", result, message="入库完成")
    persist_result(task_id, result, failed=False)
    return result



@celery_app.task(bind=True, base=_OutboxTask, outbox_operation="task_ingest", name="formunex.task_ingest")
def run_task_ingest_task(self, payload: dict) -> dict:
    return _task_ingest_impl(self.request.id, payload)



def _evidence_ingest_impl(task_id: str, payload: dict) -> dict:
    """Stage 3 / SR-026: ``POST /kb/ingest-evidence`` worker body (was inline).

    A business-level non-OK outcome (unsupported kind, already indexed)
    is a *completed* task carrying the same dict the old sync route
    returned — only exceptions become failed tasks. The kb_enabled
    guard stays sync in the route (409).
    """
    from ...domain.schemas import Evidence
    from ...services import kb_ingest

    try:
        publish_progress(
            task_id, TaskProgressStatus.RUNNING, stage="ingest",
            message="抓取全文中…", progress=0.02,
        )
        ev = Evidence(**(payload.get("evidence") or {}))
        result = kb_ingest.ingest_single_evidence(ev, project_id=payload.get("project_id"))
    except Exception as exc:
        logger.exception("evidence ingest failed")
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True, message=str(exc))
        _persist_terminal(task_id, "evidence_ingest", err, failed=True, message=str(exc))
        raise
    _persist_terminal(task_id, "evidence_ingest", result, message="入库完成")
    persist_result(task_id, result, failed=False)
    return result



@celery_app.task(bind=True, base=_OutboxTask, outbox_operation="evidence_ingest", name="formunex.evidence_ingest")
def run_evidence_ingest_task(self, payload: dict) -> dict:
    return _evidence_ingest_impl(self.request.id, payload)
