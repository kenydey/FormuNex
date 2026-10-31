"""W1-10 ③: every async submission kind has an outbox row and a replay handler.

Before, only research/inverse/doe (and, since ②, loop) were recoverable:
a broker outage between submit and pickup silently lost optimize, search,
wiki, KB, KG, PageIndex, PubChem and deps jobs. Each kind below was checked
for replay safety before wiring (same-slot task persistence, upsert, or
fill-missing semantics — never append-a-new-record), so ``recover_stalled``
may replay them under the original task id.
"""
from __future__ import annotations

import pytest

from app.db import dispatcher
from app.worker import tasks as worker_tasks

HANDLERS = [
    ("optimize", "run_optimize_task"),
    ("kb_reparse", "run_kb_reparse_task"),
    ("kg_relations_rebuild", "run_kg_relations_rebuild"),
    ("toc_summaries", "run_toc_summaries_task"),
    ("pubchem_enrich", "run_pubchem_enrich_task"),
    ("deps", "run_deps_install_task"),
    ("search", "run_search_task"),
    ("topic_sweep", "run_topic_sweep"),
    ("wiki_storm_report", "run_wiki_storm_report_task"),
]


@pytest.mark.parametrize("operation,task_attr", HANDLERS)
def test_every_wired_operation_replays_to_its_task(monkeypatch, operation, task_attr):
    published: dict = {}
    task = getattr(worker_tasks, task_attr)
    monkeypatch.setattr(
        task, "apply_async",
        lambda *a, **k: published.setdefault("args", a) or published.setdefault("kwargs", k),
    )
    assert operation in dispatcher.DISPATCHABLE_OPERATIONS
    payload = {"probe": operation}
    dispatcher._dispatch(operation, payload, task_id="replay-1")
    assert published["kwargs"]["task_id"] == "replay-1"
    assert published["kwargs"]["args"][0] == payload


def test_unknown_operation_is_never_counted_as_replayed():
    with pytest.raises(ValueError, match="no outbox handler"):
        dispatcher._dispatch("not_a_kind", {}, task_id="x")
