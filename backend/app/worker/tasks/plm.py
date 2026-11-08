"""PLM tasks: document generation, dependency installation."""
from __future__ import annotations

from ..celery_app import celery_app
from ..task_progress import TaskProgressStatus
from ..task_progress import persist_result
from ..task_progress import publish_progress
from typing import Any

from ._common import _OutboxTask, _persist_terminal, logger


@celery_app.task(bind=True, base=_OutboxTask, outbox_operation="deps", name="formunex.deps_install")
def run_deps_install_task(self, payload: dict) -> dict:
    from ...services import dependencies as deps

    task_id = self.request.id
    names = payload["names"]
    upgrade = payload.get("upgrade", False)
    verb = "upgrading" if upgrade else "installing"
    publish_progress(task_id, TaskProgressStatus.RUNNING, message=f"{verb} {', '.join(names)}")

    try:
        result = deps.install(names, upgrade=upgrade)
        failed = not result.get("ok", False)
        persist_result(task_id, result, failed=failed)
        _persist_terminal(
            task_id,
            "deps",
            result,
            failed=failed,
            message=result.get("summary", ""),
        )
        return result
    except Exception as exc:
        err = {"error": str(exc)}
        persist_result(task_id, err, failed=True)
        _persist_terminal(task_id, "deps", err, failed=True, message=str(exc))
        raise



def _jsonable(value: Any) -> Any:
    """Recursively render datetimes as ISO strings (FastAPI's wire shape).

    Task results go through ``json.dumps`` in persist_result, while the
    old sync route let FastAPI serialize. Without this, a _doc_summary
    ``created_at`` datetime breaks result persistence.
    """
    from datetime import datetime

    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value



def _document_generate_impl(task_id: str, payload: dict) -> dict:
    """Stage 3 / SR-026: ``POST /plm/revisions/.../documents`` worker body.

    The multi-regulation loop stays ONE session/transaction inside this
    task (all-or-nothing with rendered-file cleanup lives in
    generate_documents) — splitting regulations across tasks would break
    that atomicity, so "sharding" here means the whole generation is
    one task, not one task per regulation. The regulations default is
    resolved in the route (fast DB read); the non-SDS/label regulations
    misuse check stays sync 422 there too. All generation failures
    surface as failed tasks carrying the original message (the old
    422/503/409 distinctions collapse into the message for a polled
    job); the sync path is not kept, so no per-doc timeout applies.
    """
    # Lazy: app.api.plm imports worker.tasks at module level.
    from sqlalchemy.exc import IntegrityError

    from ...api.plm import _doc_summary
    from ...db.database import default_session_factory
    from ...services.plm.documents import service as docs
    from ...services.plm.render import MissingRenderDependency, PdfaError

    revision_id = str(payload.get("revision_id") or "")
    doc_type = str(payload.get("doc_type") or "")
    lang = str(payload.get("lang") or "zh-CN")
    doc_payload = payload.get("payload") or {}
    created_by = payload.get("created_by")
    pdfa = bool(payload.get("pdfa"))
    regulations = payload.get("regulations")
    try:
        publish_progress(
            task_id, TaskProgressStatus.RUNNING, stage="generate",
            message="生成文档中…", progress=0.02,
        )
        with default_session_factory()() as session:
            try:
                if doc_type in ("sds", "label"):
                    rows = docs.generate_documents(session, revision_id, doc_type, lang=lang,
                                                   payload=doc_payload, created_by=created_by,
                                                   pdfa=pdfa, regulations=regulations,
                                                   generated_by_task_id=task_id)
                else:
                    rows = [docs.generate_document(session, revision_id, doc_type, lang=lang,
                                                   payload=doc_payload, created_by=created_by, pdfa=pdfa,
                                                   generated_by_task_id=task_id)]
            except (PdfaError, MissingRenderDependency, docs.DocumentError, KeyError) as exc:
                session.rollback()
                raise RuntimeError(str(exc)) from exc
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                raise RuntimeError("并发冲突，请重试") from None
            if len(rows) == 1:
                result = {**_doc_summary(rows[0]), "content": rows[0].content}
            else:
                result = {"documents": [{**_doc_summary(r), "content": r.content} for r in rows]}
        result = _jsonable(result)
    except Exception as exc:
        logger.exception("document generate failed for %s %s", revision_id, doc_type)
        err = {"error": str(exc), "revision_id": revision_id, "doc_type": doc_type}
        persist_result(task_id, err, failed=True, message=str(exc))
        _persist_terminal(task_id, "document_generate", err, failed=True, message=str(exc))
        raise
    _persist_terminal(task_id, "document_generate", result, message="文档已生成")
    persist_result(task_id, result, failed=False)
    return result



@celery_app.task(bind=True, base=_OutboxTask, outbox_operation="document_generate", name="formunex.document_generate")
def run_document_generate_task(self, payload: dict) -> dict:
    return _document_generate_impl(self.request.id, payload)
