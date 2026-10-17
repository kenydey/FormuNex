"""GET /api/tasks — the caller's recent tasks from the durable on-disk snapshots."""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import TaskState, TaskStatus
from app.main import app
from app.worker.tasks import _persist_task, _task_file, task_manager


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_MULTI_USER", "false")
    monkeypatch.setenv("FORMUNEX_TASK_DIR", str(tmp_path / "tasks_l"))
    monkeypatch.setenv("FORMUNEX_TASK_PROGRESS_DIR", str(tmp_path / "tasks_l" / "progress"))
    get_settings.cache_clear()
    task_manager._kinds.clear()
    task_manager._owners.clear()
    yield
    get_settings.cache_clear()


def _snap(task_id: str, kind: str, state: TaskState, owner: str | None, age_s: float) -> None:
    _persist_task(
        task_id,
        TaskStatus(task_id=task_id, kind=kind, state=state, owner_id=owner, result={"big": "x" * 10},
                   stream_url=f"/api/tasks/{task_id}/stream"),
    )
    ts = time.time() - age_s
    os.utime(_task_file(task_id), (ts, ts))


def test_lists_newest_first_without_result():
    _snap("t-old", "recommend", TaskState.completed, None, 300)
    _snap("t-new", "deep_research", TaskState.failed, None, 10)
    _snap("t-mid", "recommend", TaskState.completed, None, 100)
    r = TestClient(app).get("/api/tasks")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["task_id"] for i in items] == ["t-new", "t-mid", "t-old"]
    assert "result" not in items[0]
    assert items[0]["updated_at"]


def test_state_kind_and_limit_filters():
    _snap("a1", "recommend", TaskState.completed, None, 30)
    _snap("a2", "recommend", TaskState.failed, None, 20)
    _snap("a3", "optimize", TaskState.completed, None, 10)
    c = TestClient(app)
    assert [i["task_id"] for i in c.get("/api/tasks?state=completed").json()["items"]] == ["a3", "a1"]
    assert [i["task_id"] for i in c.get("/api/tasks?kind=recommend").json()["items"]] == ["a2", "a1"]
    assert [i["task_id"] for i in c.get("/api/tasks?limit=1").json()["items"]] == ["a3"]
    assert c.get("/api/tasks?state=bogus").status_code == 422


def test_multi_user_only_own_tasks(monkeypatch):
    monkeypatch.setenv("FORMUNEX_MULTI_USER", "true")
    monkeypatch.setenv("FORMUNEX_API_TOKENS_JSON", '{"alice":"tok_alice","bob":"tok_bob"}')
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "true")
    get_settings.cache_clear()
    _snap("al-1", "recommend", TaskState.completed, "alice", 20)
    _snap("bo-1", "recommend", TaskState.completed, "bob", 10)
    _snap("pub-1", "recommend", TaskState.completed, None, 5)
    c = TestClient(app)
    alice = c.get("/api/tasks", headers={"Authorization": "Bearer tok_alice"}).json()["items"]
    assert [i["task_id"] for i in alice] == ["al-1"]
    bob = c.get("/api/tasks", headers={"Authorization": "Bearer tok_bob"}).json()["items"]
    assert [i["task_id"] for i in bob] == ["bo-1"]


def test_empty_when_no_task_dir():
    r = TestClient(app).get("/api/tasks")
    assert r.status_code == 200
    assert r.json() == {"items": []}
