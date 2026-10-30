"""P2-1: the Celery id is fixed and recorded *before* publishing.

The old order (publish, then record the broker-assigned id) left a crash
window: the task could already be running while the outbox row still had
no id, so ``recover_stalled`` replayed it under a *new* id and the id
the client held never resolved. The new order records first, publishes
under exactly that id, and hands the same id to the client — for success,
for publish failure, and for a publish that lands after its timeout.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.api import _dispatch


class _FakeTask:
    """A Celery task double: ``apply_async`` records, ``delay`` explodes."""

    def __init__(self, events: list):
        self.events = events

    def apply_async(self, args=None, task_id=None, **_):
        self.events.append(("publish", task_id))
        from types import SimpleNamespace

        return SimpleNamespace(id=task_id)

    def delay(self, *_args, **_kwargs):  # pragma: no cover - must not be used
        raise AssertionError("P2-1: publish must go through apply_async with the recorded id")


@pytest.fixture()
def events(monkeypatch):
    events: list = []
    monkeypatch.setattr(_dispatch, "broker_reachable", lambda: True)

    def record(outbox_id, task_id):
        events.append(("record", outbox_id, task_id))
        return True

    monkeypatch.setattr(_dispatch, "_record_outbox_task_id", record)
    return events


def test_record_happens_before_publish_with_the_same_id(monkeypatch, events):
    monkeypatch.setenv("FORMUNEX_CELERY_EAGER", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    response = _dispatch.submit(_FakeTask(events), {"k": "v"}, "doe_cycle", outbox_id="row-1")
    body = json.loads(response.body)
    assert [e[0] for e in events] == ["record", "publish"]
    assert events[0][1] == "row-1"
    assert events[0][2] == events[1][1] == body["task_id"]


def test_publish_failure_still_leaves_the_recorded_id(monkeypatch, events):
    monkeypatch.setenv("FORMUNEX_CELERY_EAGER", "false")
    from app.config import get_settings

    get_settings.cache_clear()

    def boom(*_args, **_kwargs):
        raise RuntimeError("broker died mid-publish")

    monkeypatch.setattr(_dispatch, "_delay_with_timeout", boom)
    with pytest.raises(HTTPException) as exc_info:
        _dispatch.submit(_FakeTask(events), {"k": "v"}, "doe_cycle", outbox_id="row-1")
    assert exc_info.value.status_code == 503
    # The row already carries the id, so recovery replays under the id
    # the client would have held — the old code recorded nothing here.
    assert [e[0] for e in events] == ["record"]
    assert events[0][2]


def test_no_outbox_row_still_publishes_under_a_fixed_id(monkeypatch, events):
    monkeypatch.setenv("FORMUNEX_CELERY_EAGER", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    response = _dispatch.submit(_FakeTask(events), {"k": "v"}, "kg_relations_rebuild")
    body = json.loads(response.body)
    assert [e[0] for e in events] == ["publish"]
    assert events[0][1] == body["task_id"]
