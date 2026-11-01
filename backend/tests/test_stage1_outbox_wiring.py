"""Stage 1 / SR-002 full: every dispatchable operation has a wired task.

W1-10 left 10 operations on bare ``@celery_app.task`` definitions: their
rows were never marked DONE/FAILED, so recovery could not distinguish a
finished job from a crashed one. This pins the wiring (``_HANDLERS`` op ->
task with matching ``outbox_operation``) and the success/failure marking
for a newly wired task, using captured ``record_done``/``record_failed``
so no database is needed here.
"""
from __future__ import annotations

import pytest

from app.db import dispatcher
from app.db import outbox_store
from app.worker import tasks as worker_tasks

# (operation, task attribute). The first four were wired in W1-10; the
# remaining ten are the stage-1 wiring.
WIRED = [
    ("research_deep", "run_deep_research_task"),
    ("research_recommend", "run_recommend_task"),
    ("inverse_design", "run_inverse_design_task"),
    ("doe_cycle", "run_doe_cycle_task"),
    ("loop", "run_loop_task"),
    ("optimize", "run_optimize_task"),
    ("kb_reparse", "run_kb_reparse_task"),
    ("kg_relations_rebuild", "run_kg_relations_rebuild"),
    ("toc_summaries", "run_toc_summaries_task"),
    ("pubchem_enrich", "run_pubchem_enrich_task"),
    ("deps", "run_deps_install_task"),
    ("search", "run_search_task"),
    ("topic_sweep", "run_topic_sweep"),
    ("wiki_storm_report", "run_wiki_storm_report_task"),
    # Stage 3 batch 2 (SR-026/063): the async endpoints' operations.
    ("url_ingest", "run_url_ingest_task"),
    ("task_ingest", "run_task_ingest_task"),
    ("evidence_ingest", "run_evidence_ingest_task"),
    ("document_generate", "run_document_generate_task"),
    ("wiki_lint_run", "run_wiki_lint_task"),
    ("wiki_lint_sweep", "run_wiki_lint_sweep_task"),
]


def test_every_dispatchable_operation_has_a_wired_task():
    assert set(dispatcher.DISPATCHABLE_OPERATIONS) == {op for op, _ in WIRED}
    for operation, task_attr in WIRED:
        task = getattr(worker_tasks, task_attr)
        assert task.outbox_operation == operation, (operation, task_attr)


def test_newly_wired_task_marks_done_and_failed(monkeypatch):
    task = worker_tasks.run_loop_task
    payload = {"probe": "loop"}
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
    task.on_success(None, "tid-1", (payload,), {})
    assert done["args"] == ("loop", payload)
    task.on_failure(ValueError("boom"), "tid-2", (payload,), {}, None)
    assert failed["args"][0] == "loop"
    assert failed["args"][1] == payload
    assert "ValueError" in failed["args"][2]
