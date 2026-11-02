"""Search tasks: keyword search and topic sweep."""
from __future__ import annotations

from ...domain.schemas import Requirement
from ..celery_app import celery_app
from ..task_progress import TaskProgressStatus
from ..task_progress import persist_result
from ..task_progress import publish_progress

from ._common import _OutboxTask, _persist_terminal, logger
from .kb import dispatch_kb_ingest



@celery_app.task(bind=True, base=_OutboxTask, outbox_operation="search", name="formunex.search")
def run_search_task(self, payload: dict) -> dict:
    from ...services import literature

    task_id = self.request.id
    publish_progress(task_id, TaskProgressStatus.RUNNING, message="检索中…")
    query = payload["query"]
    source_types = payload.get("source_types") or []
    req = Requirement(**payload["requirement"]) if payload.get("requirement") else None

    try:
        def progress(partial, meta=None) -> None:
            meta = meta or {}
            source = meta.get("source")
            new_count = int(meta.get("new_count") or 0)
            done = meta.get("sources_done") or []
            pending = meta.get("sources_pending") or []
            if source:
                msg = f"[{source}] +{new_count} 条（累计 {len(partial)}）"
            elif meta.get("final"):
                msg = f"检索完成，共 {len(partial)} 条"
            else:
                msg = f"已找到 {len(partial)} 条，继续搜索…"
            # Only ship summary metadata for intermediate updates.
            # Serialising the full evidence list to Redis on every source
            # page (30+ pages from arXiv alone) is the primary reason search
            # tasks exceed the Vite proxy timeout.
            if meta.get("final"):
                data = {
                    "evidence": [e.model_dump() for e in partial],
                    "total": len(partial),
                }
            else:
                data = {
                    "total": len(partial),
                    "source": source,
                    "new_count": new_count,
                    "sources_done": done,
                    "sources_pending": pending,
                }
            filter_report = meta.get("filter_report") or meta.get("filter")
            if filter_report:
                data["filter_report"] = filter_report
            publish_progress(
                task_id,
                TaskProgressStatus.RUNNING,
                stage=f"search:{source}" if source else "search",
                message=msg,
                data=data,
            )

        iter_result = literature.iter_search(
            query,
            source_types,
            req=req,
            total_limit=payload.get("total_limit", 300),
            per_source_cap=payload.get("per_source_cap", 50),
            progress_cb=progress,
        )
        # iter_search returns (evidence_list, filter_report); accept a bare
        # list too for backward compatibility with mocks / older callers.
        if isinstance(iter_result, tuple):
            final, filter_report = iter_result
        else:
            final, filter_report = list(iter_result), {}
        status = {k: v for k, v in literature.get_source_availability().items()}
        data = {
            "evidence": [e.model_dump() for e in final],
            "total": len(final),
            "source_status": status,
            "used_seed_fallback": any(e.is_seed_corpus for e in final),
            "filter_report": filter_report,
        }
        # Feed the registry deep research reads, so a search's hits are
        # retrievable by resolve_grounded_evidence / retrieve_node. This is the
        # one thing the old /research/kb/refresh did that a search did not; the
        # registry is keyed by doc id, so re-indexing is idempotent.
        from ...services import colbert_store

        colbert_store.index_evidence(final)

        # Background KB build: enqueue is non-blocking, so the search stream
        # terminates immediately below and the frontend keeps its results.
        kb_task_id = dispatch_kb_ingest(
            data["evidence"],
            project_id=(req.project_id if req else None),
            query=query,
            domain=getattr(req, "domain", None) if req else None,
        )
        if kb_task_id:
            data["kb_ingest_task_id"] = kb_task_id
        persist_result(task_id, data, failed=False)
        _persist_terminal(task_id, "search", data)
        return data
    except Exception as exc:
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "search", err, failed=True, message=str(exc))
        raise



@celery_app.task(bind=True, base=_OutboxTask, outbox_operation="topic_sweep", name="formunex.topic_sweep")
def run_topic_sweep(self, payload: dict) -> dict:
    """主题雷达单次触发: 检索 → topic 筛选 → 后台 KB 回填.

    两个入口: ``POST /api/search/topic-sweep``（手动一次）和 Celery Beat（设置
    ``FORMUNEX_TOPIC_RADAR_*`` 生成的计划，见 ``worker/topic_radar.py``）。结果写入
    任务状态，``GET /api/tasks/{id}`` 可查（``found`` / ``ingest_task_id`` / ``error``）。
    """
    from ...config import get_settings
    from ...domain.schemas import Requirement
    from ...services import literature

    task_id = self.request.id
    query = payload["query"]
    project_id = payload.get("project_id")
    try:
        req = Requirement(**payload["requirement"]) if payload.get("requirement") else None
        iter_result = literature.iter_search(
            query,
            payload.get("source_types") or list(get_settings().federated_sources),
            req=req,
            total_limit=int(payload.get("total_limit", 100)),
            per_source_cap=int(payload.get("per_source_cap", 30)),
        )
        final, _filter_report = (
            iter_result if isinstance(iter_result, tuple) else (list(iter_result), {})
        )
        kb_task_id = None
        if final:
            kb_task_id = dispatch_kb_ingest(
                [e.model_dump() for e in final],
                project_id=project_id,
                query=query,
                domain=getattr(req, "domain", None) if req else None,
            )
        data = {
            "task_id": task_id,
            "query": query,
            "found": len(final),
            "ingest_task_id": kb_task_id,
        }
        if task_id:  # a direct ``.run()`` call (tests, scripts) has no Celery id to record under
            persist_result(task_id, data, failed=False)
            _persist_terminal(task_id, "topic_sweep", data)
        return data
    except Exception as exc:
        logger.exception("topic_sweep failed")
        err = {"task_id": task_id, "query": query, "error": str(exc)}
        if task_id:
            persist_result(task_id, err, failed=True)
            _persist_terminal(task_id, "topic_sweep", err, failed=True, message=str(exc))
        return err
