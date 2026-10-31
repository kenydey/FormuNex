"""Stage 1 / SR-048: atomic claim, overflow warning, periodic sweep hooks.

The atomic claim is a conditional UPDATE (status + updated_at
predicate, rowcount decides ownership). True multi-process contention
cannot be reproduced on sandbox SQLite, so these tests pin the
observable contract instead: a second pass never re-dispatches a row
the first pass just claimed, a pass over the cap warns with the
remainder, and the periodic scheduler exists as a daemon thread that
sleeps its interval before the first pass.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db import dispatcher
from app.db.database import make_engine, make_session_factory
from app.db.models import TaskOutbox
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/stage1_recover.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _stalled(session, op, *, hours=2):
    row = TaskOutbox(
        id=f"stage1-{op}",
        operation=op,
        idempotency_key=f"stage1:{op}",
        payload={"probe": op},
        status="PENDING",
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours),
        updated_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours),
    )
    session.add(row)
    session.commit()
    return row


def test_second_pass_never_redispatches_first_pass_claim(db, monkeypatch):
    dispatched: list = []
    monkeypatch.setattr(
        dispatcher, "_dispatch_with_timeout",
        lambda op, payload, timeout, task_id=None: dispatched.append((op, task_id)),
    )
    with db() as s:
        _stalled(s, "search")
        assert dispatcher.recover_stalled(s, cutoff_minutes=30) == 1
        assert dispatched == [("search", None)]
        # The first pass reset the row to PENDING with a fresh
        # updated_at, so the second pass must see nothing stalled.
        assert dispatcher.recover_stalled(s, cutoff_minutes=30) == 0
        assert len(dispatched) == 1


def test_pass_over_cap_warns_with_remainder(db, monkeypatch, caplog):
    monkeypatch.setattr(
        dispatcher, "_dispatch_with_timeout",
        lambda op, payload, timeout, task_id=None: None,
    )
    with db() as s:
        for op in ("search", "loop", "optimize"):
            _stalled(s, op)
        with caplog.at_level("WARNING", logger="app.db.dispatcher"):
            assert dispatcher.recover_stalled(s, cutoff_minutes=30, max_rows=2) == 2
        assert any("3 stalled" in r.message and "1 left" in r.message for r in caplog.records)


def test_periodic_scheduler_is_daemon_and_sleeps_first(monkeypatch):
    sleeps: list = []
    entered = threading.Event()
    park = threading.Event()

    def fake_sleep(s):
        sleeps.append(s)
        entered.set()
        park.wait(timeout=10)  # daemon thread parks here; never runs a pass

    monkeypatch.setattr("time.sleep", fake_sleep)
    thread = dispatcher.schedule_periodic_recover_stalled(123.0)
    assert isinstance(thread, threading.Thread) and thread.daemon
    assert entered.wait(timeout=5)
    assert sleeps == [123.0]
