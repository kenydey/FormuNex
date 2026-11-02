"""Thin HTTP adapter over the dispatch kernel (stage 4 / SR-020).

All submission logic lives in :mod:`app.services.dispatch` (broker probe,
id-first ordering, eager pool, producer timeout). This module only:

* maps :class:`~app.services.dispatch.DispatchUnavailable` to a 503
  response carrying the task id in the ``X-FormuNex-Task-Id`` header, and
* builds the 202 accepted body for :func:`submit`.

Kept for backward compatibility (same names, same signatures): API modules
import :func:`submit` from here, ``ops``/``main`` read
:func:`broker_reachable` / :func:`broker_probe_failure_count` from here,
and tests construct :class:`DispatchTimeout` from here. New code — and all
service-layer callers — must import from :mod:`app.services.dispatch`
instead; services importing this module is the layering violation SR-020
exists to remove.
"""
from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ..services.dispatch import (
    DispatchTimeout,
    broker_probe_failure_count,
    broker_reachable,
    dispatch_task_kernel,
    DispatchUnavailable,
)

__all__ = [
    "DispatchUnavailable",
    "DispatchTimeout",
    "broker_probe_failure_count",
    "broker_reachable",
    "dispatch_task",
    "submit",
]


def dispatch_task(
    task,
    payload: dict,
    kind: str,
    *,
    outbox_id: str | None = None,
    task_id: str | None = None,
) -> str:
    """Dispatch ``task`` with ``payload``; return the Celery task id.

    HTTP adapter for :func:`app.services.dispatch.dispatch_task_kernel`:
    same id-first ordering and outbox recording, but a broker outage or a
    producer failure surfaces as HTTPException 503 (with the task id in the
    ``X-FormuNex-Task-Id`` header so the client polls that id instead of
    blindly resubmitting) rather than the kernel's
    :class:`DispatchUnavailable`. Service callers must use the kernel
    directly and never catch this adapter's HTTPException.
    """
    try:
        return dispatch_task_kernel(
            task, payload, kind, outbox_id=outbox_id, task_id=task_id
        )
    except DispatchUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=exc.detail,
            headers={"X-FormuNex-Task-Id": exc.task_id},
        ) from exc


def submit(
    task,
    payload: dict,
    kind: str,
    *,
    outbox_id: str | None = None,
    owner_id: str | None = None,
) -> JSONResponse:
    """Dispatch ``task`` with ``payload`` and return the 202 accepted response.

    P2-1 ordering (non-eager path): the Celery id is fixed *before* the
    publish, recorded on the outbox row first, and then handed to the
    broker — the same register-before-start order the eager path already
    uses. The old order (publish, then record) left a crash window in
    which the task was already running but the row still had no id, so
    ``recover_stalled`` replayed it under a *new* id and the client's
    original id never resolved. With the id fixed first, every outcome
    of the publish keeps the same id: success (client polls it),
    publish failure (503, row PENDING with the id, recovery replays
    under it), and even a publish that lands after the producer timeout
    (the replay reuses the id the client already holds).

    Raises:
        HTTPException: 503 when the broker is unreachable or (non-eager)
            producer publish times out / fails. The outbox row (if one was
            written) stays PENDING and is recovered by
            ``dispatcher.recover_stalled``.

    The id is fixed and the 202 body built *before* dispatch, as the
    pre-W1-10 ``submit`` did: starting the eager thread first hands it a
    head start on the caller, and a caller that reads state right after
    ``submit`` returns (pageindex scheduling asserts ``pending``) can lose
    that race to a thread that already ran the job to ``partial``.
    """
    from .tasks import accepted_response
    from ..worker.task_kinds import is_known_kind

    import uuid

    # Stage 4 / SR-053: the publish side checks the task-kind table, so a
    # typo'd kind fails here with 400 instead of becoming a 202 the client
    # polls forever (and that no recovery handler will ever replay).
    if not is_known_kind(kind):
        raise HTTPException(status_code=400, detail=f"未知任务类型：{kind}")

    task_id = str(uuid.uuid4())
    response = accepted_response(task_id, kind, outbox_id=outbox_id, owner_id=owner_id)
    dispatch_task(task, payload, kind, outbox_id=outbox_id, task_id=task_id)
    return response
