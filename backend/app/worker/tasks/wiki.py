"""Wiki tasks: compile, storm reports, lint."""
from __future__ import annotations

from ...services.errors import degrade_return
from ...services.errors import log_handled_exception
from ..celery_app import celery_app
from ..task_progress import TaskProgressStatus
from ..task_progress import ThinkingTracker
from ..task_progress import persist_result
from ..task_progress import publish_progress
import uuid

from ._common import _OutboxTask, _persist_terminal, logger, task_manager



@celery_app.task(
    bind=True,
    base=_OutboxTask,
    outbox_operation="wiki_storm_report",
    name="formunex.wiki_storm_report",
    soft_time_limit=1500,
    time_limit=1800,
)
def run_wiki_storm_report_task(self, payload: dict) -> dict:
    """STORM longform report: outline → sequential draft → polish → persist."""
    from ...services.wiki.storm_orchestrator import run_storm_report

    task_id = self.request.id
    tracker = ThinkingTracker(task_id, kind="wiki_storm_report")
    tracker.emit(
        "generating_outline",
        "正在生成 STORM 大纲…",
        progress=0.08,
        step_id="generating_outline",
        title="正在生成报告大纲…",
    )

    def progress_cb(
        stage: str,
        message: str,
        progress: float = 0.0,
        data: dict | None = None,
    ) -> None:
        tracker.emit(
            stage,
            message,
            progress=float(progress or 0.0),
            step_id=stage or None,
            title=message or stage or "STORM",
            kind="stage",
            detail=message,
            data=data,
        )

    try:
        # Plan 2-5: an edited outline travels in the payload; invalid payloads
        # fall back to generated outline (never fail the report for it).
        outline = None
        raw_outline = payload.get("outline")
        if isinstance(raw_outline, dict):
            try:
                from ...services.wiki.storm_schema import ReportOutline

                outline = ReportOutline.model_validate(raw_outline)
            except Exception as exc:
                logger.warning("STORM: ignoring invalid outline in payload: %s", exc)
                outline = None
        result = run_storm_report(
            str(payload.get("project_id") or ""),
            topic=str(payload.get("topic") or ""),
            max_sections=payload.get("max_sections"),
            perspectives=payload.get("perspectives"),
            use_llm=bool(payload.get("use_llm", False)),
            parallel=payload.get("parallel"),
            max_workers=payload.get("max_workers"),
            ensure_dossier=bool(payload.get("ensure_dossier", True)),
            persist=bool(payload.get("persist", True)),
            campaign_id=payload.get("campaign_id"),
            task_id=task_id,
            progress_cb=progress_cb,
            outline=outline,
        )
        # Drop heavy markdown from terminal snapshot if enormous; keep path.
        slim = {
            k: v
            for k, v in result.items()
            if k != "markdown" or len(str(v or "")) < 50_000
        }
        tracker.finish()
        persist_result(task_id, slim, failed=False)
        _persist_terminal(task_id, "wiki_storm_report", slim)
        return result
    except Exception as exc:
        logger.exception("wiki_storm_report task failed")
        tracker.finish(error=True)
        err = {"error": str(exc), "disclaimer": "draft_not_claims"}
        persist_result(task_id, err, failed=True)
        _persist_terminal(
            task_id, "wiki_storm_report", err, failed=True, message=str(exc)
        )
        raise

wiki_storm_report_task = run_wiki_storm_report_task



def _wiki_compile_impl(task_id: str, payload: dict) -> dict:
    from ...services.wiki.compile import compile_source

    source_id = str(payload.get("source_id") or "").strip()
    result = compile_source(source_id)
    return {"task_id": task_id, **result}



@celery_app.task(bind=True, name="formunex.wiki_compile")
def run_wiki_compile_task(self, payload: dict) -> dict:
    return _wiki_compile_impl(self.request.id, payload)



def dispatch_wiki_compile(source_id: str) -> str | None:
    """Fire-and-forget Wiki compile after KB index. Never raises.

    Eager mode uses a daemon thread (same pattern as ``dispatch_kb_ingest``)
    so parent ingest/index paths stay non-blocking.
    """
    from ...config import get_settings

    settings = get_settings()
    if not settings.wiki_enabled or not settings.wiki_compile_on_ingest:
        return None
    sid = (source_id or "").strip()
    if not sid:
        return None
    payload = {"source_id": sid}
    try:
        if settings.celery_eager:
            task_id = f"wikicompile-{uuid.uuid4().hex[:16]}"
            task_manager.register_celery_task(task_id, "wiki_compile")
            from ..eager_pool import submit as _eager_submit

            _eager_submit(_safe_wiki_compile, "wiki-compile", task_id, payload)
            return task_id
        async_result = run_wiki_compile_task.delay(payload)
        task_manager.register_celery_task(async_result.id, "wiki_compile")
        return async_result.id
    except Exception as exc:
        return degrade_return(logger, exc, "wiki compile dispatch failed", None)



def _safe_wiki_compile(task_id: str, payload: dict) -> None:
    try:
        _wiki_compile_impl(task_id, payload)
    except Exception as exc:
        log_handled_exception(logger, exc, "wiki compile background thread")



def _wiki_lint_impl(task_id: str, payload: dict, *, operation: str) -> dict:
    """Stage 3 / SR-063 (3-10): wiki lint worker body (was inline).

    ``/lint/run`` and ``/lint/sweep`` scanned pages in the request
    thread (multiple DB reads + file reads per page). The store opens
    short sessions per call, so the worker needs no session plumbing.
    """
    from ...services.wiki.lint import run_lint_pass, sweep_flagged_pages

    limit = int(payload.get("limit") or 200)
    detect_orphan = bool(payload.get("detect_orphan", True))
    try:
        publish_progress(
            task_id, TaskProgressStatus.RUNNING, stage="lint",
            message="扫描 Wiki 页面中…", progress=0.02,
        )
        if operation == "wiki_lint_sweep":
            result = sweep_flagged_pages(limit=limit, detect_orphan=detect_orphan)
        else:
            result = run_lint_pass(limit=limit, detect_orphan=detect_orphan)
    except Exception as exc:
        logger.exception("wiki lint failed (%s)", operation)
        err = {"error": str(exc), "operation": operation}
        persist_result(task_id, err, failed=True, message=str(exc))
        _persist_terminal(task_id, operation, err, failed=True, message=str(exc))
        raise
    _persist_terminal(task_id, operation, result, message="Lint 完成")
    persist_result(task_id, result, failed=False)
    return result



@celery_app.task(bind=True, base=_OutboxTask, outbox_operation="wiki_lint_run", name="formunex.wiki_lint_run")
def run_wiki_lint_task(self, payload: dict) -> dict:
    return _wiki_lint_impl(self.request.id, payload, operation="wiki_lint_run")



@celery_app.task(bind=True, base=_OutboxTask, outbox_operation="wiki_lint_sweep", name="formunex.wiki_lint_sweep")
def run_wiki_lint_sweep_task(self, payload: dict) -> dict:
    return _wiki_lint_impl(self.request.id, payload, operation="wiki_lint_sweep")
