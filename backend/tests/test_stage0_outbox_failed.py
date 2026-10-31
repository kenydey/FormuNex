"""Stage 0 / SR-002 stopgap: FAILED terminal state + replay snapshot guard."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.db.database import make_engine, make_session_factory
from app.db.dispatcher import recover_stalled
from app.db.models import TaskOutbox
from app.db.outbox_store import enqueue
from app.domain.schemas import TaskState, TaskStatus
from tests.alembic_helpers import run_upgrade

PAYLOAD = {"q": "stage0-outbox"}


@pytest.fixture()
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh SQLite DB migrated to head, exposed as an ORM session."""
    db_url = f"sqlite:///{tmp_path}/outbox83.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_TASK_DIR", str(tmp_path / "tasks"))
    # record_failed/on_failure resolve the DB through default_session_factory.
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    import app.db.database as db_mod

    db_mod._default.clear()
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()


def _stale_row(session, *, task_id=None):
    from app.db.outbox_store import idempotency_key

    row_id, _ = enqueue(session, "research_deep", idempotency_key("research_deep", PAYLOAD), PAYLOAD)
    session.commit()
    row = session.get(TaskOutbox, row_id)
    row.task_id = task_id
    row.updated_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    session.commit()
    return row


def test_on_failure_marks_failed_not_done(session, monkeypatch):
    from app.worker.tasks import _OutboxTask

    _stale_row(session)
    task = _OutboxTask()
    task.outbox_operation = "research_deep"
    task.on_failure(RuntimeError("boom message"), "tid", (PAYLOAD,), {}, None)
    row = session.execute(select(TaskOutbox)).scalars().one()
    assert row.status == "FAILED"
    assert row.error == "RuntimeError: boom message"
    # FAILED rows are terminal: recovery never replays them.
    mock = MagicMock()
    monkeypatch.setattr("app.db.dispatcher._dispatch_with_timeout", mock)
    assert recover_stalled(session) == 0
    assert mock.call_count == 0


def test_snapshot_guard_closes_finished_row_without_replay(session, monkeypatch, tmp_path):
    from app.worker.tasks import _persist_task

    task_id = "snap-task-1"
    _stale_row(session, task_id=task_id)
    _persist_task(task_id, TaskStatus(task_id=task_id, kind="research_deep",
                                      state=TaskState.completed, progress=1.0, message="done"))
    mock = MagicMock()
    monkeypatch.setattr("app.db.dispatcher._dispatch_with_timeout", mock)
    assert recover_stalled(session) == 0
    assert mock.call_count == 0
    row = session.execute(select(TaskOutbox)).scalars().one()
    assert row.status == "DONE"


def test_snapshot_guard_closes_failed_row(session, monkeypatch):
    from app.worker.tasks import _persist_task

    task_id = "snap-task-2"
    _stale_row(session, task_id=task_id)
    _persist_task(task_id, TaskStatus(task_id=task_id, kind="research_deep",
                                      state=TaskState.failed, progress=0.0, message="llm blew up"))
    mock = MagicMock()
    monkeypatch.setattr("app.db.dispatcher._dispatch_with_timeout", mock)
    assert recover_stalled(session) == 0
    assert mock.call_count == 0
    row = session.execute(select(TaskOutbox)).scalars().one()
    assert row.status == "FAILED"
    assert row.error == "llm blew up"
