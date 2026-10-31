"""W1-10 ②: the workbench loop dispatches like every other submission.

Before, ``_start_loop_task`` called ``run_loop_task.delay()`` directly: no
broker probe, no 503 semantics, no outbox row, and a broker outage raised a
Celery exception straight into the sync endpoint that had already saved its
results. Now it goes through ``_dispatch.dispatch_task`` (probe + id-first
ordering + outbox row + replay handler) and reports dispatch failure as
None, which ``dispatch_loop_after_sync`` turns into a "loop not started"
message instead of failing the sync.
"""
from __future__ import annotations

from sqlalchemy import select

from app.api import _dispatch
from app.config import get_settings
from app.db import dispatcher
from app.db.database import default_session_factory
from app.db.models import TaskOutbox
from app.domain.schemas import Requirement
from app.services import workbench_loop
from app.worker import tasks as worker_tasks


def _requirement() -> Requirement:
    return Requirement(domain="anticorrosion_coating", substrate="carbon_steel")


def _loop_rows(campaign_id: int) -> list[TaskOutbox]:
    with default_session_factory()() as session:
        rows = session.execute(
            select(TaskOutbox).where(TaskOutbox.operation == "loop")
        ).scalars().all()
        return [r for r in rows if (r.payload or {}).get("workbench_campaign_id") == campaign_id]


def test_broker_down_returns_none_without_touching_celery(monkeypatch):
    """Broker down: no .delay(), no registration, None — not a Celery
    exception propagating into the sync response."""
    settings = get_settings()
    monkeypatch.setattr(settings, "celery_eager", False, raising=False)
    monkeypatch.setattr(_dispatch, "broker_reachable", lambda: False)
    monkeypatch.setattr(
        worker_tasks.run_loop_task, "apply_async",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not publish")),
    )
    assert workbench_loop._start_loop_task(
        _requirement(), workbench_campaign_id=900001) is None


def test_non_eager_dispatch_publishes_under_one_id_and_records_it(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "celery_eager", False, raising=False)
    monkeypatch.setattr(_dispatch, "broker_reachable", lambda: True)
    published: dict = {}
    monkeypatch.setattr(
        worker_tasks.run_loop_task, "apply_async",
        lambda *a, **k: published.setdefault("args", a) or published.setdefault("kwargs", k),
    )
    registered: list = []
    monkeypatch.setattr(
        worker_tasks.task_manager, "register_celery_task",
        lambda task_id, kind: registered.append((task_id, kind)),
    )
    task_id = workbench_loop._start_loop_task(_requirement(), workbench_campaign_id=900002)
    assert task_id
    assert published["kwargs"]["task_id"] == task_id
    assert published["kwargs"]["args"][0]["workbench_campaign_id"] == 900002
    assert registered == [(task_id, "loop")]
    rows = _loop_rows(900002)
    assert len(rows) == 1 and rows[0].task_id == task_id


def test_eager_dispatch_registers_before_the_thread_starts(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "celery_eager", True, raising=False)
    started: dict = {}
    monkeypatch.setattr(
        _dispatch, "_submit_eager_background",
        lambda task, payload, kind, task_id=None: started.setdefault("task_id", task_id),
    )
    registered: list = []
    monkeypatch.setattr(
        worker_tasks.task_manager, "register_celery_task",
        lambda task_id, kind: registered.append((task_id, kind)),
    )
    task_id = workbench_loop._start_loop_task(_requirement(), workbench_campaign_id=900003)
    assert task_id.startswith("loop-")
    assert registered == [(task_id, "loop")]
    assert started["task_id"] == task_id


def test_dispatch_failure_becomes_a_loop_not_started_message(monkeypatch):
    monkeypatch.setattr(workbench_loop, "should_trigger_loop_after_sync", lambda *a, **k: True)
    monkeypatch.setattr(workbench_loop, "is_doecycle_paused", lambda campaign_id: False)
    monkeypatch.setattr(
        workbench_loop, "_campaign_loop_context",
        lambda campaign_id, req: ([], False, ""),
    )
    monkeypatch.setattr(workbench_loop, "_start_loop_task", lambda *a, **k: None)
    task_id, message = workbench_loop.dispatch_loop_after_sync(
        training_ingested=3, workbench_campaign_id=900004,
        requirement=_requirement(), trigger_loop=True, project_id="",
    )
    assert task_id is None
    assert "闭环未启动" in message and "同步结果已保存" in message


def test_recovery_replays_loop_under_the_original_id(monkeypatch):
    published: dict = {}
    monkeypatch.setattr(
        worker_tasks.run_loop_task, "apply_async",
        lambda *a, **k: published.setdefault("args", a) or published.setdefault("kwargs", k),
    )
    assert "loop" in dispatcher.DISPATCHABLE_OPERATIONS
    dispatcher._dispatch("loop", {"workbench_campaign_id": 900005}, task_id="loop-abc")
    assert published["kwargs"]["task_id"] == "loop-abc"
    assert published["kwargs"]["args"][0]["workbench_campaign_id"] == 900005
