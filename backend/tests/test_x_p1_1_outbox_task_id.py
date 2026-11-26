"""X-P1-1 (review 2026-11-26): SR-015 prescribed fix — first claim wins.

Same payload resubmitted (503 retry, double-click) used to mint a second
Celery id and overwrite the outbox row's ``task_id``; if the second task
then crashed, the row lost its recovery identity. The fix:

* :func:`record_task_id` is a conditional write (``WHERE task_id IS NULL``);
* :func:`dispatch_task_kernel` / :func:`submit` read the row first and reuse
  the already-claimed id — never minting a second one or overwriting the
  row. The dispatch flow still runs (probe + publish) under the reused id:
  a row cannot tell "published" from "recorded then 503'd at the probe",
  so skipping the flow would turn a broker-down retry into a false 202
  and break the dispatch-guard contract. One Celery id, not one publish —
  a resubmission may execute twice under the same id (D-4 at-least-once);
  what the fix removes is the second id overwriting the row.

These tests pin that behaviour. They do NOT change the at-least-once
(D-4) execution semantics: a replay can still run the payload twice.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import _dispatch
from app.db.database import make_engine, make_session_factory
from app.db.models import TaskOutbox
from app.db.outbox_store import (
    enqueue,
    get_task_id,
    idempotency_key,
    rearm_failed_row,
    record_failed,
    record_task_id,
)
from app.services import dispatch as _dispatch_kernel
from tests.alembic_helpers import run_upgrade


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Migrated SQLite DB that is also the process-default DB."""
    db_url = f"sqlite:///{tmp_path}/x_p1_1_outbox.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture()
def non_eager(monkeypatch):
    monkeypatch.setenv("FORMUNEX_CELERY_EAGER", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(_dispatch_kernel, "broker_reachable", lambda: True)
    return monkeypatch


class _FakeTask:
    """A Celery task double: ``apply_async`` records, ``delay`` explodes."""

    def __init__(self, events: list):
        self.events = events

    def apply_async(self, args=None, task_id=None, **_):
        self.events.append(("publish", task_id))
        from types import SimpleNamespace

        return SimpleNamespace(id=task_id)

    def delay(self, *_args, **_kwargs):  # pragma: no cover - must not be used
        raise AssertionError("X-P1-1: publish must go through apply_async with the recorded id")


def _row_id(db, operation: str, payload: dict) -> str:
    with db() as session:
        row_id, _ = enqueue(session, operation, idempotency_key(operation, payload), payload)
        session.commit()
        return row_id


def _row(db, row_id: str) -> TaskOutbox | None:
    with db() as session:
        return session.get(TaskOutbox, row_id)


# ── conditional write ───────────────────────────────────────────────────────


def test_record_task_id_first_claim_wins(db) -> None:
    row_id = _row_id(db, "research_deep", {"k": "claim"})
    assert record_task_id(row_id, "first-id") is True
    assert record_task_id(row_id, "second-id") is False
    assert get_task_id(row_id) == "first-id"
    assert _row(db, row_id).task_id == "first-id"


def test_get_task_id_returns_none_for_missing_row_and_empty_args(db) -> None:
    assert get_task_id(str(uuid.uuid4())) is None
    assert get_task_id(None) is None
    assert get_task_id("") is None


# ── kernel reuse ────────────────────────────────────────────────────────────


def test_kernel_reuses_existing_task_id_and_publishes_under_it(db, non_eager) -> None:
    """Resubmission reuses the claimed id (no second id, row unchanged) and
    still dispatches under it — the probe + publish must run, otherwise a
    broker-down retry would come back a false 202 (dispatch-guard contract)."""
    payload = {"k": "reuse"}
    row_id = _row_id(db, "doe_cycle", payload)
    assert record_task_id(row_id, "first-id") is True
    events: list = []
    returned = _dispatch_kernel.dispatch_task_kernel(
        _FakeTask(events), payload, "doe_cycle", outbox_id=row_id
    )
    assert returned == "first-id"
    assert events == [("publish", "first-id")]
    assert _row(db, row_id).task_id == "first-id"


def test_kernel_records_and_publishes_when_row_has_no_id(db, non_eager) -> None:
    payload = {"k": "fresh"}
    row_id = _row_id(db, "doe_cycle", payload)
    events: list = []
    returned = _dispatch_kernel.dispatch_task_kernel(
        _FakeTask(events), payload, "doe_cycle", outbox_id=row_id
    )
    assert returned
    assert events == [("publish", returned)]
    assert _row(db, row_id).task_id == returned


def test_kernel_claim_race_returns_winner_id_instead_of_failing(db, non_eager, monkeypatch) -> None:
    """Record lost a claim race after the read: the winner's id is reused
    (and published under), not a DispatchUnavailable — the row is
    recoverable under the winner."""
    payload = {"k": "race"}
    row_id = _row_id(db, "doe_cycle", payload)
    assert record_task_id(row_id, "winner-id") is True
    # Simulate the race: the first read saw no id, the conditional write lost.
    reads = {"n": 0}

    def _read(outbox_id):
        reads["n"] += 1
        return None if reads["n"] == 1 else ("winner-id" if outbox_id == row_id else None)

    monkeypatch.setattr(_dispatch_kernel, "_record_outbox_task_id", lambda o, t: False)
    monkeypatch.setattr(_dispatch_kernel, "get_outbox_task_id", _read)
    events: list = []
    returned = _dispatch_kernel.dispatch_task_kernel(
        _FakeTask(events), payload, "doe_cycle", outbox_id=row_id
    )
    assert returned == "winner-id"
    assert events == [("publish", "winner-id")]


def test_kernel_record_failure_without_winner_still_fails(db, non_eager, monkeypatch) -> None:
    """SR-088 guard: a record failure on a still id-less row is still a
    hard dispatch failure — the X-P1-1 re-read must not weaken it."""
    payload = {"k": "sr088"}
    row_id = _row_id(db, "doe_cycle", payload)
    monkeypatch.setattr(_dispatch_kernel, "_record_outbox_task_id", lambda o, t: False)
    events: list = []
    with pytest.raises(_dispatch_kernel.DispatchUnavailable):
        _dispatch_kernel.dispatch_task_kernel(
            _FakeTask(events), payload, "doe_cycle", outbox_id=row_id
        )
    assert events == []


# ── HTTP submit: same payload twice, one Celery id ─────────────────────────


def test_submit_same_payload_twice_yields_one_celery_id(db, non_eager) -> None:
    """Same payload twice: one Celery id in both 202 bodies, the row keeps
    it, and both publishes go out under that one id (at-least-once, D-4 —
    no second id is ever minted or recorded)."""
    payload = {"k": "double-submit"}
    row_id = _row_id(db, "doe_cycle", payload)
    events: list = []
    first = _dispatch.submit(_FakeTask(events), payload, "doe_cycle", outbox_id=row_id)
    second = _dispatch.submit(_FakeTask(events), payload, "doe_cycle", outbox_id=row_id)
    first_id = json.loads(first.body)["task_id"]
    assert json.loads(second.body)["task_id"] == first_id
    assert events == [("publish", first_id), ("publish", first_id)]
    assert _row(db, row_id).task_id == first_id


def test_second_submission_crash_leaves_row_recoverable(db, non_eager) -> None:
    """The X-P1-1 harm chain, closed: the second submission never overwrites
    the id (both publishes share the one id), so when the task fails, the
    row is FAILED under the original id and can be re-armed for a fresh
    submission."""
    payload = {"k": "crash-after-dedup"}
    row_id = _row_id(db, "doe_cycle", payload)
    events: list = []
    first = _dispatch.submit(_FakeTask(events), payload, "doe_cycle", outbox_id=row_id)
    first_id = json.loads(first.body)["task_id"]
    _dispatch.submit(_FakeTask(events), payload, "doe_cycle", outbox_id=row_id)
    assert events == [("publish", first_id), ("publish", first_id)]

    # The task crashes: its failure mark lands on the same row.
    assert record_failed("doe_cycle", payload, "boom") is True
    row = _row(db, row_id)
    assert row.status == "FAILED"
    assert row.task_id == first_id

    # Manual re-arm still works: the row goes PENDING with a clean id slot,
    # so a fresh submission mints a new id instead of reusing the dead one.
    with db() as session:
        fresh = session.get(TaskOutbox, row_id)
        assert rearm_failed_row(session, fresh) is True
        session.commit()
    assert _row(db, row_id).status == "PENDING"
    assert _row(db, row_id).task_id is None
