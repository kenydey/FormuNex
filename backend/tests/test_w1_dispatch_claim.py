"""W1-10 ①: a 503 from dispatch must be claimable, not a blind retry.

A thread blocked in a publish cannot be cancelled in Python, so a publish
that lands after the producer timeout is unavoidable in the residual
hung-socket case. What must never happen is the client being unable to tell
"definitely not submitted" from "maybe submitted": the task id is fixed
before the probe, recorded on the outbox row even when the probe then fails,
and carried on every dispatch 503 in the ``X-FormuNex-Task-Id`` header, so
the client polls that id (and startup recovery replays under it) instead of
resubmitting and duplicating the work.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api import _dispatch
from app.api._idempotency import enqueue_outbox
from app.config import get_settings
from app.db.database import default_session_factory
from app.db.models import TaskOutbox
from app.worker import tasks as worker_tasks


def _row(outbox_id: str) -> TaskOutbox | None:
    with default_session_factory()() as session:
        return session.get(TaskOutbox, outbox_id)


@pytest.fixture()
def non_eager(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "celery_eager", False, raising=False)
    return settings


def test_broker_down_503_claims_the_id_and_the_row_keeps_it(monkeypatch, non_eager):
    """Probe fails: the 503 header names the id, and the outbox row already
    carries it, so startup recovery replays exactly that id."""
    monkeypatch.setattr(_dispatch, "broker_reachable", lambda: False)
    payload = {"query": "w1-10-claim-probe", "source_types": []}
    outbox_id = enqueue_outbox("search", payload)
    assert outbox_id
    with pytest.raises(HTTPException) as excinfo:
        _dispatch.dispatch_task(worker_tasks.run_search_task, payload, "search",
                                outbox_id=outbox_id)
    exc = excinfo.value
    assert exc.status_code == 503
    assert isinstance(exc.detail, str)  # frontend parses detail as a string
    claimed = exc.headers["X-FormuNex-Task-Id"]
    assert claimed
    assert _row(outbox_id).task_id == claimed


def test_producer_timeout_503_claims_the_same_id(monkeypatch, non_eager):
    """Producer timeout (the orphan window): the 503 still claims the id
    the abandoned thread may publish under."""
    monkeypatch.setattr(_dispatch, "broker_reachable", lambda: True)

    def _boom(task, payload, task_id=None):
        raise _dispatch._DispatchTimeout("task.delay > 10.0s")

    monkeypatch.setattr(_dispatch, "_delay_with_timeout", _boom)
    payload = {"query": "w1-10-claim-timeout", "source_types": []}
    outbox_id = enqueue_outbox("search", payload)
    with pytest.raises(HTTPException) as excinfo:
        _dispatch.dispatch_task(worker_tasks.run_search_task, payload, "search",
                                outbox_id=outbox_id)
    exc = excinfo.value
    assert exc.status_code == 503
    claimed = exc.headers["X-FormuNex-Task-Id"]
    assert _row(outbox_id).task_id == claimed


def test_submit_propagates_the_claim_header(monkeypatch, non_eager):
    """The HTTP wrapper adds nothing and drops nothing: the 503 a client
    actually sees carries the claim header."""
    monkeypatch.setattr(_dispatch, "broker_reachable", lambda: False)
    with pytest.raises(HTTPException) as excinfo:
        _dispatch.submit(worker_tasks.run_search_task, {"query": "x"}, "search")
    assert excinfo.value.status_code == 503
    assert excinfo.value.headers["X-FormuNex-Task-Id"]
