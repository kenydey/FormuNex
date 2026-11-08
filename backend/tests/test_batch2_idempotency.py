"""Batch 2 (2026-11-04 deep review): loop / inverse-design / snapshot-reaper idempotency.

P2-8 (loop plan id + running placeholder), P2-9 (inverse-design round
id), P1-A9 (stalled task-snapshot reaping + retention pruning).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.database import default_session_factory
from app.db.models import RecommendOutcomeRow
from app.domain.schemas.task import TaskStatus
from app.worker.tasks._common import _persist_task, reap_stalled_snapshots
from app.worker.tasks.doe import (
    _append_running_placeholder_if_needed,
    _loop_plan_id_for_task,
)
from app.worker.tasks.research import _register_inverse_design_round
from tests.alembic_helpers import run_upgrade


# ── P2-8 ────────────────────────────────────────────────────────────────────


def test_loop_plan_id_derived_from_task_id():
    tid = "12345678-1234-1234-1234-123456789012"
    pid = _loop_plan_id_for_task(tid)
    assert pid == _loop_plan_id_for_task(tid)  # deterministic per task
    assert pid.startswith("loop-") and len(pid) <= 36  # DOEPlanRow.id String(36)
    assert _loop_plan_id_for_task(tid) != _loop_plan_id_for_task("other-task")
    fallback = _loop_plan_id_for_task(None)
    assert len(fallback) == 32  # uuid hex, old behaviour for direct callers


class _Camp:
    def __init__(self, history):
        self.loop_history = history


class _Store:
    def __init__(self, history):
        self._history = history
        self.appended: list = []

    def get_campaign_sync(self, cid):
        return _Camp(self._history)

    def append_loop_history_sync(self, cid, entry):
        self.appended.append(entry)


def test_placeholder_reused_when_last_is_running():
    store = _Store([{"running": True, "at": "x"}])
    assert _append_running_placeholder_if_needed(store, 1) is False
    assert store.appended == []


def test_placeholder_appended_when_last_is_done_or_empty():
    for hist in ([], [{"round": 1}]):
        store = _Store(hist)
        assert _append_running_placeholder_if_needed(store, 1) is True
        assert len(store.appended) == 1 and store.appended[0]["running"] is True


def test_placeholder_store_without_append_is_noop():
    class _NoAppend:
        pass

    assert _append_running_placeholder_if_needed(_NoAppend(), 1) is False


# ── P2-9 ────────────────────────────────────────────────────────────────────


def test_inverse_design_round_id_is_task_derived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Same task id → same recommend_id and a single denominator row;
    a different task id registers a new round."""
    db_url = f"sqlite:///{tmp_path}/p29.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    payload = {"requirement": {"project_id": "p1"}}

    rec1 = _register_inverse_design_round(payload, task_id="task-xyz")
    rec2 = _register_inverse_design_round(payload, task_id="task-xyz")
    assert rec1 and rec1 == rec2 and len(rec1) <= 32
    rec3 = _register_inverse_design_round(payload, task_id="task-other")
    assert rec3 and rec3 != rec1
    with default_session_factory()() as s:
        rows = s.execute(select(RecommendOutcomeRow)).scalars().all()
        assert sorted(r.recommend_id for r in rows) == sorted([rec1, rec3])


# ── P1-A9 ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def taskdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FORMUNEX_TASK_DIR", str(tmp_path / "tasks"))
    return tmp_path / "tasks"


def _write(task_id: str, kind: str, state: str, age_hours: float, taskdir: Path) -> Path:
    _persist_task(task_id, TaskStatus(task_id=task_id, kind=kind, state=state, message=""))
    path = taskdir / f"{task_id}.json"
    old = time.time() - age_hours * 3600
    os.utime(path, (old, old))
    return path


def test_reaper_fails_stale_running_and_pending_but_keeps_fresh(taskdir: Path):
    stale_run = _write("stale-run", "file_ingest", "running", 25, taskdir)
    stale_pend = _write("stale-pend", "kb_ingest", "pending", 25, taskdir)
    fresh = _write("fresh-run", "file_ingest", "running", 1, taskdir)
    stats = reap_stalled_snapshots(max_age_hours=24.0, retention_days=30.0)
    assert stats["reaped"] == 2 and stats["pruned"] == 0
    from app.worker.tasks._common import load_persisted_task

    assert load_persisted_task("stale-run").state == "failed"
    assert load_persisted_task("stale-pend").state == "failed"
    assert load_persisted_task("fresh-run").state == "running"
    assert stale_run.exists() and stale_pend.exists() and fresh.exists()


def test_reaper_prunes_old_terminal_and_skips_corrupt_and_live(taskdir: Path):
    old_done = _write("old-done", "loop", "completed", 31 * 24, taskdir)
    fresh_done = _write("fresh-done", "loop", "completed", 1, taskdir)
    corrupt = taskdir / "corrupt.json"
    taskdir.mkdir(parents=True, exist_ok=True)
    corrupt.write_text("{not json", encoding="utf-8")
    # Age it past both cutoffs: an unparseable old file must be skipped,
    # never reaped or pruned on a guess.
    old = time.time() - 31 * 24 * 3600
    os.utime(corrupt, (old, old))
    old_live = _write("old-live", "loop", "running", 25, taskdir)
    stats = reap_stalled_snapshots(
        max_age_hours=24.0, retention_days=30.0, live_ids={"old-live"}
    )
    assert stats["reaped"] == 0 and stats["pruned"] == 1
    assert not old_done.exists()
    assert fresh_done.exists() and corrupt.exists() and old_live.exists()
