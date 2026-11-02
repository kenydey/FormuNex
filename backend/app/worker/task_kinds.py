"""Task taxonomy: every Celery task, its display kind and its outbox operation.

Stage 4 / SR-053 + SR-057. Two kind namespaces exist and must not be
confused:

* **display kind**: the ``kind`` accepted by the HTTP submit path and shown
  in the 202 body / TaskManager snapshot (e.g. ``"recommend"``).
* **outbox operation**: the durable operation recorded on the outbox row,
  carried by the task's ``outbox_operation`` attribute, and dispatched by
  ``db.dispatcher._HANDLERS`` for startup recovery (e.g.
  ``"research_recommend"``).

They are identical except for the two research tasks, which predate the
outbox naming. This module is the single place stating both mappings, so
publish (the adapter's ``submit`` rejects an unknown kind with 400) and
recovery (dispatcher handlers keyed by operation) can never drift apart:
tests assert the handler keys equal this table's operations, and every
registered task resolves in ``celery_app.tasks`` with a matching
``outbox_operation``.

Tasks with ``operation=None`` have no outbox row and no recovery handler:
they are dispatched through ``dispatch_*`` helpers (file/KB ingest, wiki
compile, screening) or are internal (molscribe, chunk sampling, fulltext
retry, noop). Their registry key is the display kind where one exists,
otherwise the celery short name.

Deliberately a leaf module (stdlib only, no app imports): the API adapter,
the dispatcher, and tests all read it, and any app import here would close
a cycle with ``app.worker.tasks``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskKind:
    """One row of the taxonomy."""

    kind: str
    operation: str | None
    celery_name: str
    task_attr: str


KINDS: tuple[TaskKind, ...] = (
    TaskKind("recommend", "research_recommend", "formunex.recommend", "run_recommend_task"),
    TaskKind("deep_research", "research_deep", "formunex.deep_research", "run_deep_research_task"),
    TaskKind("optimize", "optimize", "formunex.optimize", "run_optimize_task"),
    TaskKind("kb_ingest", None, "formunex.kb_ingest", "run_kb_ingest_task"),
    TaskKind("toc_summaries", "toc_summaries", "formunex.toc_summaries", "run_toc_summaries_task"),
    TaskKind("pubchem_enrich", "pubchem_enrich", "formunex.pubchem_enrich", "run_pubchem_enrich_task"),
    TaskKind("file_ingest", None, "formunex.file_ingest", "run_file_ingest_task"),
    TaskKind("search", "search", "formunex.search", "run_search_task"),
    TaskKind("topic_sweep", "topic_sweep", "formunex.topic_sweep", "run_topic_sweep"),
    TaskKind("loop", "loop", "formunex.loop", "run_loop_task"),
    TaskKind("inverse_design", "inverse_design", "formunex.inverse_design", "run_inverse_design_task"),
    TaskKind("doe_cycle", "doe_cycle", "formunex.doe_cycle", "run_doe_cycle_task"),
    TaskKind("deps", "deps", "formunex.deps_install", "run_deps_install_task"),
    TaskKind("molscribe_recognize", None, "formunex.molscribe_recognize", "run_molscribe_recognize_task"),
    TaskKind("wiki_storm_report", "wiki_storm_report", "formunex.wiki_storm_report", "run_wiki_storm_report_task"),
    TaskKind("noop", None, "formunex.noop", "noop_task"),
    TaskKind("kg_relations_rebuild", "kg_relations_rebuild", "formunex.kg_relations_rebuild", "run_kg_relations_rebuild"),
    TaskKind("wiki_compile", None, "formunex.wiki_compile", "run_wiki_compile_task"),
    TaskKind("screening", None, "formunex.screening", "run_screening_task"),
    TaskKind("chunk_quality_sample", None, "formunex.chunk_quality_sample", "run_chunk_quality_sample"),
    TaskKind("kb_reparse", "kb_reparse", "formunex.kb_reparse", "run_kb_reparse_task"),
    TaskKind("url_ingest", "url_ingest", "formunex.url_ingest", "run_url_ingest_task"),
    TaskKind("task_ingest", "task_ingest", "formunex.task_ingest", "run_task_ingest_task"),
    TaskKind("evidence_ingest", "evidence_ingest", "formunex.evidence_ingest", "run_evidence_ingest_task"),
    TaskKind("document_generate", "document_generate", "formunex.document_generate", "run_document_generate_task"),
    TaskKind("wiki_lint_run", "wiki_lint_run", "formunex.wiki_lint_run", "run_wiki_lint_task"),
    TaskKind("wiki_lint_sweep", "wiki_lint_sweep", "formunex.wiki_lint_sweep", "run_wiki_lint_sweep_task"),
    TaskKind("fulltext_retry", None, "formunex.fulltext_retry", "retry_fulltext_fetch"),
)

BY_KIND: dict[str, TaskKind] = {entry.kind: entry for entry in KINDS}
BY_OPERATION: dict[str, TaskKind] = {
    entry.operation: entry for entry in KINDS if entry.operation is not None
}


def is_known_kind(kind: str) -> bool:
    return kind in BY_KIND


def operation_for_kind(kind: str) -> str | None:
    entry = BY_KIND.get(kind)
    return entry.operation if entry is not None else None


def kind_for_operation(operation: str) -> str | None:
    entry = BY_OPERATION.get(operation)
    return entry.kind if entry is not None else None
