"""Optimisation / DOE / closed-loop tasks."""
from __future__ import annotations

from ...domain.schemas import DOEPlan
from ...domain.schemas import OptimizationResult
from ...domain.schemas import Requirement
from ...pipeline import workflow
from ...services.errors import log_handled_exception
from ..celery_app import celery_app
from ..task_progress import TaskProgressStatus
from ..task_progress import ThinkingTracker
from ..task_progress import persist_result
from ..task_progress import publish_progress
from typing import Any
try:
    from celery.exceptions import SoftTimeLimitExceeded as _SoftTimeLimitExceeded
except ImportError:
    _SoftTimeLimitExceeded = None

from ._common import _OutboxTask, _persist_terminal, logger



@celery_app.task(
    bind=True,
    base=_OutboxTask,
    outbox_operation="optimize",
    name="formunex.optimize",
    soft_time_limit=2400,
    time_limit=3000,
    max_retries=2,
    # G P2-3: SoftTimeLimitExceeded 不是 TimeoutError 子类，soft 超时后需重试。
    autoretry_for=(TimeoutError, ConnectionError, OSError, _SoftTimeLimitExceeded)
    if _SoftTimeLimitExceeded
    else (TimeoutError, ConnectionError, OSError),
    retry_backoff=True,
)
def run_optimize_task(self, payload: dict) -> dict:
    task_id = self.request.id
    tracker = ThinkingTracker(task_id, kind="optimize")
    tracker.emit(
        "init",
        "starting optimizer",
        progress=0.05,
        step_id="init",
        title="正在初始化寻优器…",
    )

    try:
        # req 构造在 try 内：payload 校验失败也要走失败持久化，
        # 否则任务快照永远停留 queued、SSE 无终止事件。
        req = Requirement(**payload["requirement"])

        def progress(p: float, msg: str) -> None:
            stage, title = _optimize_stage(p, msg)
            tracker.emit(
                stage,
                msg,
                progress=round(p, 3),
                step_id=stage,
                title=title,
                kind="stage",
                detail=msg,
            )

        result = workflow.run_optimization(
            req,
            iterations=payload.get("iterations"),
            progress_cb=progress,
            engine=payload.get("engine", "auto"),
            campaign_state=payload.get("campaign_state"),
            workbench_campaign_id=payload.get("workbench_campaign_id"),
            seed=payload.get("seed"),
        )
        data = result.model_dump()
        tracker.emit(
            "converge",
            "optimization complete",
            progress=0.98,
            step_id="converge",
            title="寻优收敛完成",
        )
        tracker.finish()
        persist_result(task_id, data, failed=False)
        _persist_terminal(task_id, "optimize", data)
        # P1-34: output receipt (fail-open, never breaks the run).
        try:
            from ...services.tech_report import write_output_receipt

            write_output_receipt("optimize", task_id, inputs=payload, outputs=data)
        except Exception:
            pass
        # P4.2: optional dossier S6/S7 patch when optimize completes (default OFF).
        _notify_dossier_optimize_completed(payload)
        return data
    except Exception as exc:
        tracker.finish(error=True)
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "optimize", err, failed=True, message=str(exc))
        raise



def _optimize_stage(progress: float, message: str) -> tuple[str, str]:
    """Map optimizer progress float + message → stage id / timeline title."""
    msg = (message or "").lower()
    if progress < 0.2 or "init" in msg or "start" in msg:
        return "init", "正在初始化寻优器…"
    if progress < 0.55 or "propos" in msg or "sample" in msg or "suggest" in msg:
        return "propose", "正在提出候选实验点…"
    if progress < 0.85 or "evaluat" in msg or "score" in msg or "object" in msg:
        return "evaluate", "正在评估目标与约束…"
    return "converge", "正在收敛最优配方…"



def _notify_dossier_optimize_completed(payload: dict) -> None:
    """Best-effort: map optimize success → dossier ``optimize_completed`` event."""
    try:
        from ...services.wiki.dossier import notify_dossier_event, notify_dossier_event_for_campaign

        cid = payload.get("workbench_campaign_id")
        if cid is not None and cid != "":
            notify_dossier_event_for_campaign(cid, "optimize_completed")
            return
        req = payload.get("requirement") or {}
        pid = ""
        if isinstance(req, dict):
            pid = str(req.get("project_id") or "").strip()
        if pid:
            notify_dossier_event(pid, "optimize_completed")
    except Exception:
        pass



@celery_app.task(bind=True, base=_OutboxTask, outbox_operation="loop", name="formunex.loop")
def run_loop_task(self, payload: dict) -> dict:
    return run_loop_iterate_impl(self.request.id, payload)



def _parse_optional_model(raw: Any, model_cls: type):
    if not raw:
        return None
    try:
        return model_cls.model_validate(raw)
    except (TypeError, ValueError):
        return None



def _loop_plan_id_for_task(task_id: str | None) -> str:
    """P2-8 (2026-11-04 deep review): derive a loop plan's id from the
    task id instead of minting a fresh UUID per run — doe_plan_store
    .save is idempotent on plan_id, so a replay reuses the row instead
    of stacking a duplicate plan. Fits String(36). Direct callers
    without a task id keep the old UUID path."""
    import uuid

    if task_id:
        return "loop-" + task_id.replace("-", "")[:31]
    return uuid.uuid4().hex


def _append_running_placeholder_if_needed(store, campaign_id: int) -> bool:
    """P2-8: append the loop running placeholder only when the last
    history entry is not already one. A replay used to append a second
    running entry (the first one then stranded forever, since only the
    last entry gets updated at the end). Best-effort: any store failure
    just skips the placeholder. Returns True when appended."""
    if not hasattr(store, "append_loop_history_sync"):
        return False
    hist = []
    try:
        camp = store.get_campaign_sync(campaign_id) if hasattr(store, "get_campaign_sync") else None
        hist = list(getattr(camp, "loop_history", None) or [])
    except Exception:  # noqa: BLE001 - placeholder is best-effort
        hist = []
    if hist and hist[-1].get("running"):
        return False
    try:
        from datetime import UTC, datetime

        store.append_loop_history_sync(
            campaign_id,
            {"running": True, "at": datetime.now(UTC).isoformat()},
        )
    except Exception:  # noqa: BLE001 - placeholder is best-effort
        return False
    return True


def _persist_loop_history(campaign_id: int | None, report, task_id: str | None = None) -> None:
    if not campaign_id:
        return
    from datetime import UTC, datetime

    from ...db.campaign_store import get_campaign_store

    # 持久化 next_doe 到 doe_plans（带 campaign_id + round），并把 plan_id 记入 loop_history
    doe_plan_id = None
    next_doe = getattr(report, "next_doe", None)
    if next_doe is not None:
        store = get_campaign_store()
        campaign = (
            store.get_campaign_sync(campaign_id)
            if hasattr(store, "get_campaign_sync")
            else None
        )
        # round_no 由 _append_loop_history 内部以 len(history)+1 单一真相源计算，
        # 不在两个独立读改写点各算一次（避免 TOCTOU 与 JSON 列丢失更新）。
        # 若最后一个 entry 是 running 占位（任务开始时写入），本轮就是它的 round。
        _hist = list(campaign.loop_history or []) if campaign is not None else []
        round_no = (
            len(_hist) if (_hist and _hist[-1].get("running")) else len(_hist) + 1
        ) if campaign is not None else None
        if not getattr(next_doe, "plan_id", None):
            next_doe.plan_id = _loop_plan_id_for_task(task_id)
        doe_plan_id = next_doe.plan_id
        try:
            from ...db import doe_plan_store
            from ...db.database import default_session_factory
            from ...db.session_utils import commit_session

            with commit_session(default_session_factory()) as session:
                doe_plan_store.save(
                    session,
                    next_doe,
                    campaign_id=campaign_id,
                    round_no=round_no,
                    project_id=getattr(campaign, "project_id", None) or None,
                )
        except Exception as exc:
            log_handled_exception(logger, exc, "persist loop next_doe")
            doe_plan_id = None

    entry = {
        # round 由 _append_loop_history 内部以 len(history)+1 兜底重写，此处仅占位
        "round": 0,
        "at": datetime.now(UTC).isoformat(),
        "converged": report.converged,
        "rmse_by_metric": report.rmse_by_metric,
        "engine": report.engine,
        "loop_message": report.loop_message,
        "doe_plan_id": doe_plan_id,
    }
    try:
        store = get_campaign_store()
        if hasattr(store, "append_loop_history_sync"):
            # 任务开始时写过 running entry（run_loop_iterate_impl）：原地更新，
            # 不新增 round，避免 rounds 计数膨胀。
            last_running = False
            try:
                camp = store.get_campaign_sync(campaign_id)
                hist = list(getattr(camp, "loop_history", None) or [])
                last_running = bool(hist and hist[-1].get("running"))
            except Exception:  # noqa: BLE001
                last_running = False
            if last_running and hasattr(store, "update_last_loop_entry_sync"):
                store.update_last_loop_entry_sync(campaign_id, entry)
            else:
                store.append_loop_history_sync(campaign_id, entry)
    except Exception as exc:
        log_handled_exception(logger, exc, "persist loop_history")



def run_loop_iterate_impl(task_id: str, payload: dict) -> dict:
    from ...services import auto_loop
    from ...services.workbench_loop import is_doecycle_paused

    publish_progress(task_id, TaskProgressStatus.RUNNING, message="starting loop")

    try:
        campaign_id = payload.get("workbench_campaign_id")
        if campaign_id is not None and is_doecycle_paused(int(campaign_id)):
            err = {
                "error": "DOE cycle paused for this campaign",
                "paused": True,
                "campaign_id": int(campaign_id),
            }
            publish_progress(task_id, TaskProgressStatus.FAILED, message=err["error"])
            persist_result(task_id, err, failed=True)
            _persist_terminal(task_id, "loop", err, failed=True, message=err["error"])
            return err

        # req 构造在 try 内：payload 校验失败也要走失败持久化。
        # 任务开始先写 running 占位 entry，让 campaign_loop_status 能报"进行中"；
        # 结束时 _persist_loop_history 会原地更新它（不膨胀 rounds）。
        # P2-8 (2026-11-04 deep review): placeholder is conditional now
        # (see _append_running_placeholder_if_needed) — a replay used to
        # append a second running entry and strand the first one.
        if campaign_id is not None:
            try:
                from ...db.campaign_store import get_campaign_store

                _append_running_placeholder_if_needed(
                    get_campaign_store(), int(campaign_id)
                )
            except Exception:  # noqa: BLE001
                pass

        req = Requirement(**payload["requirement"])

        def progress(p: float, msg: str) -> None:
            publish_progress(
                task_id,
                TaskProgressStatus.RUNNING,
                message=msg,
                progress=round(p, 3),
            )

        result = auto_loop.loop_iterate(
            req,
            optimize_iterations=payload.get("iterations") or 24,
            n_suggest=payload.get("n_suggest", 4),
            progress_cb=progress,
            optimize_engine=payload.get("optimize_engine", "auto"),
            doe_engine=payload.get("doe_engine", "auto"),
            workbench_campaign_id=payload.get("workbench_campaign_id"),
            campaign_state=payload.get("campaign_state"),
            prior_rmse_history=payload.get("prior_rmse_history") or [],
            prior_optimization=_parse_optional_model(
                payload.get("prior_optimization"), OptimizationResult
            ),
            prior_next_doe=_parse_optional_model(payload.get("prior_next_doe"), DOEPlan),
            budget_remaining=payload.get("budget_remaining"),
            seed=payload.get("seed"),
        )
        # 先持久化（内部会给 next_doe.plan_id 赋值，任务 id 派生则重放幂等），再 dump，
        # 否则任务结果里的 plan_id 是旧值，前端无法关联该 DOE 计划。
        _persist_loop_history(payload.get("workbench_campaign_id"), result, task_id=task_id)
        data = result.model_dump()
        persist_result(task_id, data, failed=False)
        _persist_terminal(task_id, "loop", data)
        # P4.2: refresh S6/S7 after loop round persists (in addition to start-of-loop notify).
        try:
            from ...services.wiki.dossier import notify_dossier_event_for_campaign

            notify_dossier_event_for_campaign(
                payload.get("workbench_campaign_id"), "loop_updated"
            )
        except Exception:
            pass
        return data
    except Exception as exc:
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "loop", err, failed=True, message=str(exc))
        # 失败时也把 running 占位 entry 转为终态，避免 status 永远卡在"进行中"。
        try:
            _cid = payload.get("workbench_campaign_id")
            if _cid is not None:
                from datetime import UTC, datetime

                from ...db.campaign_store import get_campaign_store

                _store = get_campaign_store()
                _camp = _store.get_campaign_sync(int(_cid))
                _hist = list(getattr(_camp, "loop_history", None) or [])
                if _hist and _hist[-1].get("running"):
                    _store.update_last_loop_entry_sync(
                        int(_cid),
                        {
                            "at": datetime.now(UTC).isoformat(),
                            "converged": False,
                            "error": str(exc)[:500],
                            "loop_message": f"闭环任务失败：{str(exc)[:200]}",
                        },
                    )
        except Exception:  # noqa: BLE001
            pass
        raise



@celery_app.task(
    bind=True,
    base=_OutboxTask,
    outbox_operation="doe_cycle",
    name="formunex.doe_cycle",
    soft_time_limit=1500,
    time_limit=1800,
    max_retries=3,
    autoretry_for=(TimeoutError, ConnectionError, OSError),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def run_doe_cycle_task(self, payload: dict) -> dict:
    """Execute one DOE cycle: generate experiments using Bayesian optimization.
    
    This task implements the closed-loop DOE automation:
    1. Load requirement from payload
    2. Call recommendation service to get candidate formulations (Top-20)
    3. Use Baybe engine to generate next batch of experiment points
    4. Write experiment points to experiments table with status=pending
    5. Return experiment ID list
    """
    from ...services import doe_cycle_service
    from ...services.workbench_loop import is_doecycle_paused
    
    task_id = self.request.id
    tracker = ThinkingTracker(task_id, kind="doe_cycle")
    tracker.emit(
        "load",
        "Starting DOE cycle",
        progress=0.1,
        step_id="load",
        title="正在加载技术需求…",
    )
    
    try:
        campaign_id = payload.get("workbench_campaign_id")
        if campaign_id is not None and is_doecycle_paused(int(campaign_id)):
            err = {
                "error": "DOE cycle paused for this campaign",
                "paused": True,
                "campaign_id": int(campaign_id),
            }
            tracker.finish(error=True)
            publish_progress(task_id, TaskProgressStatus.FAILED, message=err["error"])
            persist_result(task_id, err, failed=True)
            _persist_terminal(task_id, "doe_cycle", err, failed=True, message=err["error"])
            return err

        # Extract payload
        requirement_dict = payload.get("requirement", {})
        if not requirement_dict:
            raise ValueError("Missing requirement in payload")
        
        from ...domain.schemas import Requirement
        requirement = Requirement(**requirement_dict)

        tracker.emit(
            "candidates",
            "Generating candidate formulations",
            progress=0.35,
            step_id="candidates",
            title="正在生成候选配方…",
            detail="Top-N 候选供贝叶斯采样",
        )
        tracker.emit(
            "design",
            "Building DOE matrix",
            progress=0.65,
            step_id="design",
            title="正在生成 DOE 矩阵…",
            detail="Bayesian / 实验设计引擎",
        )
        
        # Execute DOE cycle
        result = doe_cycle_service.run_doe_cycle(
            requirement,
            budget_remaining=payload.get("budget_remaining"),
            seed=payload.get("seed"),
            # P1-1: 透传 campaign_id，生成的实验可关联到 workbench campaign
            workbench_campaign_id=campaign_id,
            # P1-A7: 任务 id 透传给 persist 层做重放清理
            task_id=task_id,
        )

        tracker.emit(
            "write",
            "Writing pending experiments",
            progress=0.9,
            step_id="write",
            title="正在写入待做实验点…",
        )
        tracker.finish()
        persist_result(task_id, result, failed=False)
        _persist_terminal(task_id, "doe_cycle", result, failed=False)
        # P1-34: output receipt (fail-open, never breaks the run).
        try:
            from ...services.tech_report import write_output_receipt

            write_output_receipt("doe_cycle", task_id, inputs=payload, outputs=result)
        except Exception:
            pass

        return result
        
    except Exception as exc:
        err = {"error": str(exc)}
        tracker.finish(error=True)
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "doe_cycle", err, failed=True, message=str(exc))
        # Contract errors: fail once. Transient/other: real retry (P1 #28).
        if isinstance(exc, (ValueError, TypeError)):
            raise
        raise self.retry(
            exc=exc,
            countdown=min(60, 5 * (2 ** int(self.request.retries or 0))),
        )



# Legacy names kept for imports
optimize_task = run_optimize_task

loop_task = run_loop_task
