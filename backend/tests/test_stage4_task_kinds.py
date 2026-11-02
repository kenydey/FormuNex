"""Stage 4 / SR-053 + SR-057: the task-kind registry is the single taxonomy.

Locks (publish side and recovery side read the same table):

* dispatcher recovery handlers keyed by operation == registry operations
  (neither side may gain an operation the other does not know);
* every registry entry resolves to a registered Celery task whose
  ``outbox_operation`` matches the table (guards the worker/tasks split:
  a domain module the package forgets to import registers nothing);
* the adapter's ``submit`` rejects an unknown kind with 400 before any
  202 body or task-manager registration happens.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException


def test_registry_operations_match_dispatcher_handlers():
    from app.db import dispatcher
    from app.worker.task_kinds import BY_OPERATION

    assert set(dispatcher._HANDLERS) == set(BY_OPERATION)


def test_display_kind_to_operation_mapping():
    from app.worker.task_kinds import BY_KIND, operation_for_kind

    # The only two non-identity mappings (research predates outbox naming).
    assert operation_for_kind("recommend") == "research_recommend"
    assert operation_for_kind("deep_research") == "research_deep"
    # Everything else submitted today is identity-mapped.
    for kind in (
        "optimize", "search", "topic_sweep", "loop", "inverse_design",
        "doe_cycle", "deps", "kb_reparse", "kg_relations_rebuild",
        "toc_summaries", "pubchem_enrich", "url_ingest", "task_ingest",
        "evidence_ingest", "document_generate", "wiki_lint_run",
        "wiki_lint_sweep", "wiki_storm_report",
    ):
        assert operation_for_kind(kind) == kind
    assert len(BY_KIND) == 28


def test_registry_tasks_resolve_with_matching_operation():
    import app.worker.tasks as tasks_mod
    from app.worker.celery_app import celery_app
    from app.worker.task_kinds import KINDS

    for entry in KINDS:
        task = celery_app.tasks[entry.celery_name]
        # Module attributes are Celery PromiseProxy objects wrapping the
        # registered task, so compare by equality + name, not identity.
        exposed = getattr(tasks_mod, entry.task_attr)
        assert exposed == task
        assert exposed.name == entry.celery_name
        assert getattr(task, "outbox_operation", "") == (entry.operation or "")


def test_submit_rejects_unknown_kind_with_400():
    from app.api import _dispatch

    with pytest.raises(HTTPException) as excinfo:
        _dispatch.submit(object(), {"k": "v"}, "no_such_kind")
    assert excinfo.value.status_code == 400
