"""Shared task framework: persistence, TaskManager, _OutboxTask base.

Stage 4 / SR-057: the task-framework half of the old ``app.worker.tasks``
module. Domain modules import from here; this module imports no domain
module, so the split stays acyclic.
"""
from __future__ import annotations

from ...config import task_dir as _task_persist_dir
from ...domain.schemas import TaskState
from ...domain.schemas import TaskStatus
from ...services._fsutil import atomic_write_text
from ...services._fsutil import read_text_with_retry
from ...services.errors import degrade_return
from ...services.errors import log_handled_exception
from ..celery_app import celery_app
from ..task_progress import TaskProgressStatus
from ..task_progress import publish_progress
from ..task_progress import register_pending
from pathlib import Path
from typing import Any
import json
import logging
import re
import time

# Logger name stays app.worker.tasks across the split so
# log output does not change with the module move.
logger = logging.getLogger("app.worker.tasks")



# Task ids are uuids or ``<prefix>-<hex>``; anything else must never become part of a file name (``../x`` wrote outside
# the task directory).
_TASK_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")



def _task_file(task_id: str) -> Path:
    if not _TASK_ID_RE.fullmatch(task_id or ""):
        raise ValueError(f"invalid task id: {task_id!r}")
    return _task_persist_dir() / f"{task_id}.json"



def _persist_task(task_id: str, status: TaskStatus) -> None:
    try:
        path = _task_file(task_id)
        persist_dir = path.parent
        persist_dir.mkdir(parents=True, exist_ok=True)
        data = status.model_dump()
        data["state"] = data["state"].value if hasattr(data["state"], "value") else data["state"]
        # 原子写：先写 .tmp 再 rename，避免进程崩溃时留下半截 JSON（W5）。临时文件名按进程+线程区分——
        # 固定的 ``.<task_id>.tmp`` 让同一任务的两个并发写方互相覆盖对方写到一半的文件。
        atomic_write_text(path, json.dumps(data, ensure_ascii=False))
    except Exception as exc:
        log_handled_exception(logger, exc, "handled exception")



def load_persisted_task(task_id: str) -> TaskStatus | None:
    try:
        path = _task_file(task_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        data = json.loads(read_text_with_retry(path))
        return TaskStatus(**data)
    except Exception as exc:
        return degrade_return(logger, exc, "operation failed", None)



class _OutboxTask(celery_app.Task):
    """Base for tasks submitted through a durable outbox row.

    ``api/*`` writes the row (``outbox_operation`` × content-hash of the
    payload) before dispatching. When the task reaches a *final* state — Celery
    calls ``on_success`` / ``on_failure`` only after retries are exhausted —
    the row is closed: success flips it to ``DONE``, failure to ``FAILED``
    with a short summary (stage 0 / SR-002 stopgap; previously failure was
    also marked DONE, which blinded operators and made recovery unable to
    distinguish the two). ``dispatcher.recover_stalled`` replays only rows
    interrupted by a crash, never terminal ones. Never raises: a lost
    completion mark must not turn a finished task into a failed one.
    """

    outbox_operation: str = ""

    @staticmethod
    def _payload_of(args, kwargs):
        return args[0] if args else (kwargs or {}).get("payload")

    def __call__(self, *args, **kwargs):
        # Celery (worker and eager apply alike) enters the task body through __call__.
        # While it runs, keep the outbox row's ``updated_at`` fresh: startup recovery
        # replays only rows nothing has touched for the cutoff, so a job that simply takes
        # longer than the cutoff (loop, whole-library KB ingest) is not run a second time.
        if not self.outbox_operation:
            return super().__call__(*args, **kwargs)
        from ...db.outbox_store import heartbeat

        with heartbeat(self.outbox_operation, self._payload_of(args, kwargs)):
            return super().__call__(*args, **kwargs)

    def _mark_outbox_done(self, args, kwargs) -> None:
        if not self.outbox_operation:
            return
        payload = self._payload_of(args, kwargs)
        if not isinstance(payload, dict):
            return
        from ...db.outbox_store import record_done

        record_done(self.outbox_operation, payload)

    def _mark_outbox_failed(self, args, kwargs, exc: BaseException | None) -> None:
        if not self.outbox_operation:
            return
        payload = self._payload_of(args, kwargs)
        if not isinstance(payload, dict):
            return
        from ...db.outbox_store import record_failed

        # Class name + truncated message only: never tracebacks (einfo),
        # never exception args wholesale — they can carry credentials or
        # payloads that do not belong in an ops-visible row.
        summary = ""
        if exc is not None:
            summary = f"{type(exc).__name__}: {exc}"
        record_failed(self.outbox_operation, payload, summary[:500])

    def on_success(self, retval, task_id, args, kwargs):
        self._mark_outbox_done(args, kwargs)

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        self._mark_outbox_failed(args, kwargs, exc)



def _persist_terminal(
    task_id: str,
    kind: str,
    result: dict[str, Any] | None,
    *,
    failed: bool = False,
    message: str = "",
) -> None:
    """Write terminal task snapshot to disk (works when Redis is unavailable)."""
    status = TaskStatus(
        task_id=task_id,
        kind=kind,
        state=TaskState.failed if failed else TaskState.completed,
        progress=0.0 if failed else 1.0,
        message=message or ("failed" if failed else "done"),
        result=result,
        stream_url=f"/api/tasks/{task_id}/stream",
    )
    _persist_task(task_id, status)
    publish_progress(
        task_id,
        TaskProgressStatus.FAILED if failed else TaskProgressStatus.COMPLETED,
        message=message or ("failed" if failed else "done"),
        progress=0.0 if failed else 1.0,
        data=result,
        kind=kind,
    )



def _result_from_event(meta: dict[str, Any]) -> dict[str, Any] | None:
    try:
        data = json.loads(meta.get("last_event") or "{}").get("data")
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None



def _status_from_progress(task_id: str, kind: str) -> TaskStatus:
    from ..task_progress import get_task_meta, get_task_result

    meta = get_task_meta(task_id) or {}
    result = get_task_result(task_id)
    raw_status = meta.get("status", "PENDING")
    if result is None and raw_status in ("COMPLETED", "FAILED"):
        # The terminal event carries the result, and the separate result store is written just *after* it. A snapshot
        # taken in that gap — or one whose disk copy could not be read at that instant (Windows, mid-replace) — was
        # "completed" with no result and, persisted back, replaced the good terminal snapshot: the task finished and
        # its result was gone.
        result = _result_from_event(meta)
    state_map = {
        "PENDING": TaskState.pending,
        "RUNNING": TaskState.running,
        "COMPLETED": TaskState.completed,
        "FAILED": TaskState.failed,
        "CANCELLED": TaskState.cancelled,
    }
    progress = float(meta.get("progress", 0.0) or 0.0)
    # elapsed_ms
    elapsed_ms = None
    try:
        st = meta.get("started_at")
        if st:
            elapsed_ms = int((time.time() - float(st)) * 1000)
    except Exception:
        elapsed_ms = None
    owner_id = (meta.get("owner_id") or "").strip() or None
    # 回退：内存表或持久化
    if not owner_id:
        try:
            owner_id = task_manager._owners.get(task_id)  # type: ignore[name-defined]
        except Exception:
            owner_id = None
    return TaskStatus(
        task_id=task_id,
        kind=kind or meta.get("kind", "unknown"),
        state=state_map.get(raw_status, TaskState.pending),
        progress=progress,
        message=meta.get("message", ""),
        result=result,
        stream_url=f"/api/tasks/{task_id}/stream",
        stage=meta.get("stage", "") or "",
        elapsed_ms=elapsed_ms,
        owner_id=owner_id,
    )



class TaskManager:
    """Registry of Celery task metadata for read-only status snapshots."""

    # 进程级单例，长生命周期下任务数无界增长 → 用有界 LRU 避免内存泄漏（W4）。
    _KINDS_MAX = 1024

    def __init__(self) -> None:
        from collections import OrderedDict

        self._kinds: "OrderedDict[str, str]" = OrderedDict()
        self._owners: "OrderedDict[str, str | None]" = OrderedDict()

    def _remember(self, task_id: str, kind: str, owner_id: str | None = None) -> None:
        self._kinds[task_id] = kind
        self._kinds.move_to_end(task_id)
        self._owners[task_id] = owner_id
        self._owners.move_to_end(task_id)
        while len(self._kinds) > self._KINDS_MAX:
            self._kinds.popitem(last=False)
        while len(self._owners) > self._KINDS_MAX:
            self._owners.popitem(last=False)

    def register_celery_task(self, task_id: str, kind: str, owner_id: str | None = None) -> None:
        """Announce a task as ``queued`` - once.

        Registration is idempotent and never moves a task backwards: the first call writes ``queued``; a later call (the
        upload route registers after ``dispatch_file_ingest`` already did, and a worker that ends in milliseconds - a
        duplicate upload, a missing parser - has long finished by then) only records the owner. It used to rewrite
        ``queued`` over the finished task's progress meta and snapshot, so the upload sat at "queued" for good although
        its result was stored (seen once on a Windows runner as a 30 s poll that never saw the task end).
        """
        # owner 优先来自显式参数，否则沿用已有记录
        if owner_id is None:
            owner_id = self._owners.get(task_id)
        self._remember(task_id, kind, owner_id)
        existing = load_persisted_task(task_id)
        if existing and existing.state in (TaskState.completed, TaskState.failed, TaskState.cancelled):
            return
        from ..task_progress import get_task_meta

        if (get_task_meta(task_id) or {}).get("status"):
            self._sync_owner_meta(task_id, owner_id)
            return
        register_pending(task_id, kind)
        status = TaskStatus(
            task_id=task_id,
            kind=kind,
            state=TaskState.pending,
            message="queued",
            stream_url=f"/api/tasks/{task_id}/stream",
            owner_id=owner_id,
        )
        _persist_task(task_id, status)
        self._sync_owner_meta(task_id, owner_id)

    @staticmethod
    def _sync_owner_meta(task_id: str, owner_id: str | None) -> None:
        # 同步写入 owner 到 meta，便于 _status_from_progress 回读
        try:
            from ..task_progress import _redis_client, _meta_key

            c = _redis_client()
            c.hset(_meta_key(task_id), mapping={"owner_id": owner_id or ""})
        except Exception as exc:
            logger.debug("owner_id redis sync failed for %s: %s", task_id, exc)

    def get(self, task_id: str) -> TaskStatus | None:
        from ..task_progress import get_task_meta

        meta = get_task_meta(task_id)
        kind = self._kinds.get(task_id) or (meta or {}).get("kind", "")
        persisted = load_persisted_task(task_id)
        # Prefer disk terminal over stale non-terminal Redis/file meta.
        # SSE already reads disk terminals when Redis is down; status must match
        # or clients see COMPLETED on the stream while GET still returns pending.
        if persisted and persisted.state in (
            TaskState.completed,
            TaskState.failed,
            TaskState.cancelled,
        ):
            return persisted
        if meta:
            status = _status_from_progress(task_id, kind)
            _persist_task(task_id, status)
            return status
        if persisted:
            return persisted
        if task_id in self._kinds:
            return TaskStatus(
                task_id=task_id,
                kind=kind,
                state=TaskState.pending,
                message="queued",
                stream_url=f"/api/tasks/{task_id}/stream",
                owner_id=self._owners.get(task_id),
            )
        return None

    def cancel(self, task_id: str) -> bool:
        """Best-effort revoke + persist CANCELLED. Returns True if task was pending/running."""
        meta_status = None
        try:
            from ..task_progress import get_task_meta

            meta = get_task_meta(task_id)
            meta_status = (meta or {}).get("status")
        except Exception:
            meta_status = None
        if meta_status in ("COMPLETED", "FAILED", "CANCELLED"):
            return False
        try:
            celery_app.control.revoke(task_id, terminate=True)
        except Exception as exc:
            logger.warning("revoke failed for %s: %s", task_id, exc)
        kind = self._kinds.get(task_id) or ""
        owner_id = self._owners.get(task_id)
        try:
            from ..task_progress import TaskProgressStatus, publish_progress

            publish_progress(task_id, TaskProgressStatus.CANCELLED, stage="cancelled", message="已取消", kind=kind or "unknown")
            # 保留 owner
            if owner_id:
                try:
                    from ..task_progress import _redis_client, _meta_key

                    _redis_client().hset(_meta_key(task_id), mapping={"owner_id": owner_id})
                except Exception as exc:
                    logger.debug("cancelled owner redis sync failed for %s: %s", task_id, exc)
        except Exception as exc:
            logger.warning("cancel progress publish failed for %s: %s", task_id, exc)
        # 持久化 cancelled 快照
        try:
            status = TaskStatus(task_id=task_id, kind=kind or "unknown", state=TaskState.cancelled, message="已取消", stream_url=f"/api/tasks/{task_id}/stream", stage="cancelled", owner_id=owner_id)
            _persist_task(task_id, status)
        except Exception as exc:
            logger.warning("cancelled snapshot persist failed for %s: %s", task_id, exc)
        return True



task_manager = TaskManager()



def reap_stalled_snapshots(
    *,
    max_age_hours: float = 24.0,
    retention_days: float = 30.0,
    live_ids: set[str] | None = None,
) -> dict[str, int]:
    """P1-A9 (2026-11-04 deep review): heal tasks without an outbox row.

    Tasks dispatched without ``_OutboxTask`` (file ingest, KB ingest,
    wiki compile, screening) leave only a disk snapshot: when the
    worker dies mid-run, nothing ever flips it out of ``running`` and
    clients poll it forever. This pass fails snapshots stuck in
    ``running`` / ``pending`` longer than ``max_age_hours`` (24 h by
    default — past the 5 h Celery hard time limit, so no live worker
    can still own one; eager threads past 24 h are dead by policy)
    and deletes terminal snapshots older than ``retention_days``.

    Fail-open per file, never raises: the reaper runs in lifespan /
    periodic threads where an exception must not break startup. The
    mtime pre-filter means only stale candidates are parsed, and a
    reaped file's refreshed mtime keeps it out of the same pass's
    prune step.
    """
    root = _task_persist_dir()
    stats = {"reaped": 0, "pruned": 0}
    if not root.is_dir():
        return stats
    live = live_ids or set()
    now = time.time()
    reap_before = now - max_age_hours * 3600.0
    prune_before = now - retention_days * 86400.0
    try:
        paths = list(root.glob("*.json"))
    except OSError:
        return stats
    for path in paths:
        task_id = path.stem
        if not _TASK_ID_RE.fullmatch(task_id):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= reap_before and mtime >= prune_before:
            continue
        try:
            snap = load_persisted_task(task_id)
        except Exception:  # noqa: BLE001 - fail-open per file
            continue
        if snap is None:
            continue
        state = getattr(snap.state, "value", snap.state)
        try:
            if mtime < reap_before and state in ("running", "pending") and task_id not in live:
                _persist_terminal(
                    task_id, snap.kind, None, failed=True,
                    message=f"任务超过 {max_age_hours:g} 小时无进展，worker 可能已崩溃，回收为失败",
                )
                stats["reaped"] += 1
            elif mtime < prune_before and state in ("completed", "failed", "cancelled"):
                path.unlink(missing_ok=True)
                stats["pruned"] += 1
        except Exception:  # noqa: BLE001 - fail-open per file
            continue
    return stats


def list_persisted_task_ids(limit: int = 1000) -> list[tuple[str, float]]:
    """``(task_id, mtime)`` of the task snapshots on disk, newest first.

    The per-task JSON snapshots in ``task_dir()`` are the durable record (Redis meta expires, the in-memory
    registry is per process), so the task list is built from them. Files that are not snapshots (temp files,
    the ``progress/`` sub-directory) are skipped.
    """
    root = _task_persist_dir()
    if not root.is_dir():
        return []
    entries: list[tuple[str, float]] = []
    try:
        for path in root.glob("*.json"):
            task_id = path.stem
            if not _TASK_ID_RE.fullmatch(task_id):
                continue
            try:
                entries.append((task_id, path.stat().st_mtime))
            except OSError:
                continue
    except OSError as exc:
        return degrade_return(logger, exc, "task snapshot listing failed", [])
    entries.sort(key=lambda e: e[1], reverse=True)
    return entries[: max(0, limit)]



def cancel_task(task_id: str) -> bool:
    return task_manager.cancel(task_id)



def _thinking_title(stage: str, message: str) -> str:
    labels = {
        "retrieve": "正在检索知识库与证据…",
        "grade": "正在评估证据相关性…",
        "fallback": "检索不足，正在补充召回…",
        "generate": "正在生成研究报告…",
        "claim_check": "正在校验关键声明…",
        "regenerate": "声明未过，正在再生成…",
        "recommend": "正在生成推荐配方…",
        "cancelled": "任务已取消",
    }
    return labels.get(stage) or message or stage or "处理中…"
