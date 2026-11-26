"""2026-11-26 审查第一波 · 资料检索 P1-6 定向单测 (worker/tasks/kb.py)。

P1-6: run_kb_ingest_task 接 _OutboxTask (outbox_operation="kb_ingest"),
dispatch_kb_ingest 先写 durable outbox 行再经 dispatch kernel 分发;
任务终态时 outbox 行被标 DONE/FAILED。
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db import outbox_store
from app.domain.schemas import Evidence
from app.worker import tasks as worker_tasks
from app.worker.tasks._common import _OutboxTask


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_KB_INGEST_AUTO", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ev(ident: str = "US9999999") -> Evidence:
    return Evidence(
        source="Google Patents", identifier=ident, title=ident,
        snippet="锌系磷化膜耐蚀性…", relevance=0.9,
    )


def test_kb_ingest_task_is_outbox_task():
    task = worker_tasks.run_kb_ingest_task
    assert isinstance(task, _OutboxTask)
    assert task.outbox_operation == "kb_ingest"


def test_kb_ingest_task_marks_outbox_done_and_failed(monkeypatch):
    done: dict = {}
    failed: dict = {}
    monkeypatch.setattr(
        outbox_store, "record_done",
        lambda operation, p: done.setdefault("args", (operation, p)) or True,
    )
    monkeypatch.setattr(
        outbox_store, "record_failed",
        lambda operation, p, summary: failed.setdefault("args", (operation, p, summary)) or True,
    )
    payload = {"probe": "kb_ingest"}
    worker_tasks.run_kb_ingest_task.on_success(None, "tid-1", (payload,), {})
    assert done["args"] == ("kb_ingest", payload)
    worker_tasks.run_kb_ingest_task.on_failure(
        ValueError("boom"), "tid-2", (payload,), {}, None
    )
    assert failed["args"][0] == "kb_ingest"
    assert failed["args"][1] == payload
    assert "ValueError" in failed["args"][2]


def test_dispatch_writes_outbox_row_then_kernel(monkeypatch):
    enqueued: dict = {}
    dispatched: dict = {}

    def fake_enqueue(operation, payload, **kw):
        enqueued.setdefault("args", (operation, payload))
        return "outbox-1"

    def fake_kernel(task, payload, kind, **kw):
        dispatched.setdefault("args", (task, payload, kind))
        dispatched.setdefault("kwargs", kw)
        return "kernel-id-1"

    monkeypatch.setattr("app.api._idempotency.enqueue_outbox", fake_enqueue)
    monkeypatch.setattr("app.services.dispatch.dispatch_task_kernel", fake_kernel)
    task_id = worker_tasks.dispatch_kb_ingest([_ev().model_dump()])
    assert task_id and task_id.startswith("kbingest-")
    assert enqueued["args"][0] == "kb_ingest"
    assert enqueued["args"][1]["evidence"][0]["identifier"] == "US9999999"
    assert dispatched["args"][0] is worker_tasks.run_kb_ingest_task
    assert dispatched["args"][1] is enqueued["args"][1]
    assert dispatched["args"][2] == "kb_ingest"
    assert dispatched["kwargs"]["outbox_id"] == "outbox-1"
    assert dispatched["kwargs"]["task_id"] == task_id


def test_dispatch_non_eager_returns_kernel_task_id(monkeypatch):
    monkeypatch.setenv("FORMUNEX_CELERY_EAGER", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.api._idempotency.enqueue_outbox", lambda op, p, **kw: "outbox-2"
    )

    def fake_kernel(task, payload, kind, **kw):
        assert kw.get("outbox_id") == "outbox-2"
        assert "task_id" not in kw
        return "celery-id-9"

    monkeypatch.setattr("app.services.dispatch.dispatch_task_kernel", fake_kernel)
    assert worker_tasks.dispatch_kb_ingest([_ev().model_dump()]) == "celery-id-9"


def test_dispatch_failure_returns_none(monkeypatch):
    monkeypatch.setenv("FORMUNEX_CELERY_EAGER", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.api._idempotency.enqueue_outbox", lambda op, p, **kw: "outbox-3"
    )

    def boom_kernel(*a, **k):
        raise RuntimeError("broker down")

    monkeypatch.setattr("app.services.dispatch.dispatch_task_kernel", boom_kernel)
    assert worker_tasks.dispatch_kb_ingest([_ev().model_dump()]) is None
