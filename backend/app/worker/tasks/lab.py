"""Lab tasks: molecule recognition, screening, noop."""
from __future__ import annotations

from ...services.errors import degrade_return
from ...services.errors import log_handled_exception
from ..celery_app import celery_app
from ..task_progress import TaskProgressStatus
from ..task_progress import persist_result
from ..task_progress import publish_progress
import uuid

from ._common import _persist_terminal, logger, task_manager



@celery_app.task(bind=True, name="formunex.molscribe_recognize")
def run_molscribe_recognize_task(self, payload: dict) -> dict:
    """离线识别单张结构图 → SMILES（MolScribe 后端）。只在 molscribe 队列 worker 上执行。

    由主 backend 通过 ``send_task(..., queue=molscribe_queue)`` 投递，返回
    ``{"ok": True, "smiles": ...}`` 或 ``{"ok": False, "reason": ...}``。
    不写 SSE 进度（内部同步 RPC 语义）。

    P1 起：结果附带 MolJSON/RDKit 结构校验（``valid`` / ``atom_count`` /
    ``ring_count`` / ``roundtrip_ok``），识别误差在进入下游推理前暴露。
    """
    from ...services.ocsr import validate_recognized_smiles

    image_path = payload.get("image_path", "")
    if not image_path:
        return {"ok": False, "smiles": None, "reason": "missing image_path"}
    return validate_recognized_smiles(image_path)



@celery_app.task(name="formunex.noop")
def noop_task(*args: object, **kwargs: object) -> str:
    """Producer warm-up probe (2026-09-05): 无副作用, 只验证 broker 通道."""
    return "ok"



def warm_celery_producer(timeout: float = 60.0) -> None:
    """发布一次 noop —— uvicorn 进程内首次 producer 初始化极慢/卡请求线程。

    在 FastAPI lifespan 后台线程预热, 把 producer 首建移到启动期;
    失败仅日志(首个 submit 请求会再付一次冷启动)。
    """
    try:
        noop_task.delay()
    except Exception:
        logger.exception("celery producer warmup failed (non-fatal)")




# ── Async literature screening (W6-2) ─────────────────────────────────────────
# Manifest > screening_async_threshold() 条目时走后台 job，进度经 task 进度
# 管线（/api/tasks/{id}/stream）可查；小 manifest 保持同步直跑。


def _screening_impl(task_id: str, payload: dict) -> dict:
    """Shared body for the Celery task and the eager-mode background thread."""
    from ...config import get_settings
    from ...services import literature_screening as ls

    project_id = str(payload.get("project_id") or "")
    last = {"n": 0}

    def _cb(done: int, total: int) -> None:
        # 节流：每 25 条或收尾才 publish，避免 redis 风暴
        if done >= total or done - last["n"] >= 25:
            last["n"] = done
            publish_progress(
                task_id,
                TaskProgressStatus.RUNNING,
                stage="screening",
                message=f"文献筛选 {done}/{total}",
                progress=(done / total) if total else 0.0,
                kind="screening",
            )

    try:
        result = ls.screen_project(
            project_id,
            payload.get("criteria") or {},
            apply=True,
            settings=get_settings(),
            preset=payload.get("preset"),
            rule_name=payload.get("rule_name"),
            progress_cb=_cb,
        )
        out = {
            "project_id": project_id,
            "summary": result.get("summary"),
            "decisions": len(result.get("decisions") or []),
            "preset": payload.get("preset"),
            "rule_name": payload.get("rule_name"),
        }
        persist_result(task_id, out, failed=False)
        _persist_terminal(task_id, "screening", out)
        return out
    except Exception as exc:
        err = {"error": str(exc), "project_id": project_id}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "screening", err, failed=True)
        log_handled_exception(logger, exc, "screening background job")
        raise



@celery_app.task(bind=True, name="formunex.screening")
def run_screening_task(self, payload: dict) -> dict:
    return _screening_impl(self.request.id, payload)



def _safe_screening(task_id: str, payload: dict) -> None:
    try:
        _screening_impl(task_id, payload)
    except Exception as exc:  # already persisted as failed inside the impl
        log_handled_exception(logger, exc, "screening background thread")



def dispatch_screening_job(
    project_id: str,
    criteria: dict,
    *,
    preset: str | None = None,
    rule_name: str | None = None,
    actor: str = "user",
) -> str | None:
    """Enqueue a background screening job; return task id for SSE/status polling.

    Returns None when screening is disabled or dispatch itself fails (fail-open:
    the caller can fall back to synchronous screening).
    """
    from ...config import get_settings
    from ...services import literature_screening as ls

    if not ls.screening_enabled(get_settings()):
        return None
    payload = {
        "project_id": project_id,
        "criteria": criteria or {},
        "preset": preset,
        "rule_name": rule_name,
        "actor": actor,
    }
    try:
        if get_settings().celery_eager:
            task_id = f"screening-{uuid.uuid4().hex[:16]}"
            task_manager.register_celery_task(task_id, "screening")
            from ..eager_pool import submit as _eager_submit

            _eager_submit(_safe_screening, "screening", task_id, payload)
            return task_id
        async_result = run_screening_task.delay(payload)
        task_manager.register_celery_task(async_result.id, "screening")
        return async_result.id
    except Exception as exc:
        return degrade_return(logger, exc, "screening dispatch failed", None)
