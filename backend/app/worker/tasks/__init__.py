"""Celery-backed async task orchestration (package form).

Stage 4 / SR-057: the old ``app.worker.tasks`` module, split by domain.
Importing this package imports every domain module, which registers all
Celery tasks exactly as the single module did (same ``formunex.*`` names).
Every previously accessible name is re-exported here, so
``from app.worker.tasks import X`` and ``tasks.X`` keep working.
"""
from __future__ import annotations

from ._common import logger
from ._common import _TASK_ID_RE, _task_file, _persist_task, load_persisted_task, _OutboxTask, _persist_terminal, _result_from_event, _status_from_progress, TaskManager, task_manager, list_persisted_task_ids, reap_stalled_snapshots, cancel_task, _thinking_title
from .kb import _kb_ingest_project_id, _kb_ingest_impl, run_kb_ingest_task, run_toc_summaries_task, run_pubchem_enrich_task, run_file_ingest_task, _file_ingest_impl, _file_ingest_body, dispatch_file_ingest, _safe_file_ingest, dispatch_kb_ingest, _safe_kb_ingest, run_kg_relations_rebuild, retry_fulltext_fetch, enqueue_fulltext_retry, run_chunk_quality_sample, _kb_reparse_impl, run_kb_reparse_task, _url_ingest_impl, run_url_ingest_task, _task_ingest_impl, run_task_ingest_task, _evidence_ingest_impl, run_evidence_ingest_task
from .research import run_deep_research_task, _stage_progress, _convert_formulas, run_recommend_task, run_inverse_design_task, _register_inverse_design_round, run_inverse_design_impl, deep_research_task, recommend_task, ingest_patents_task  # noqa: F401  # P3-A5: old aliases kept as module attributes (hasattr contract), deliberately NOT in __all__
from .doe import run_optimize_task, _optimize_stage, _notify_dossier_optimize_completed, run_loop_task, _parse_optional_model, _persist_loop_history, run_loop_iterate_impl, run_doe_cycle_task, optimize_task, loop_task  # noqa: F401  # P3-A5: old aliases kept as module attributes (hasattr contract), deliberately NOT in __all__
from .plm import run_deps_install_task, _jsonable, _document_generate_impl, run_document_generate_task
from .wiki import run_wiki_storm_report_task, wiki_storm_report_task, _wiki_compile_impl, run_wiki_compile_task, dispatch_wiki_compile, _safe_wiki_compile, _wiki_lint_impl, run_wiki_lint_task, run_wiki_lint_sweep_task  # noqa: F401  # P3-A5: old aliases kept as module attributes (hasattr contract), deliberately NOT in __all__
from .search import run_search_task, run_topic_sweep
from .lab import run_molscribe_recognize_task, noop_task, warm_celery_producer, _screening_impl, run_screening_task, _safe_screening, dispatch_screening_job

# P3-A5: legacy aliases (optimize_task, loop_task, ...) stay importable
# as attributes for the Stage 4 split-compatibility surface (SR-057),
# but are NOT part of the export surface: new code must use the
# canonical run_* names, so a stray alias cannot bypass the
# outbox_operation check that keys on the run_* task object.
__all__ = ['logger', '_TASK_ID_RE', '_task_file', '_persist_task', 'load_persisted_task', '_OutboxTask', '_persist_terminal', '_result_from_event', '_status_from_progress', 'TaskManager', 'task_manager', 'list_persisted_task_ids', 'reap_stalled_snapshots', 'cancel_task', '_thinking_title', 'run_deep_research_task', '_stage_progress', '_convert_formulas', 'run_recommend_task', 'run_optimize_task', '_optimize_stage', '_notify_dossier_optimize_completed', '_kb_ingest_project_id', '_kb_ingest_impl', 'run_kb_ingest_task', 'run_toc_summaries_task', 'run_pubchem_enrich_task', 'run_file_ingest_task', '_file_ingest_impl', '_file_ingest_body', 'dispatch_file_ingest', '_safe_file_ingest', 'dispatch_kb_ingest', '_safe_kb_ingest', 'run_search_task', 'run_topic_sweep', 'run_loop_task', '_parse_optional_model', '_persist_loop_history', 'run_loop_iterate_impl', 'run_inverse_design_task', '_register_inverse_design_round', 'run_inverse_design_impl', 'run_doe_cycle_task', 'run_deps_install_task', 'run_molscribe_recognize_task', 'run_wiki_storm_report_task', 'noop_task', 'run_kg_relations_rebuild', '_wiki_compile_impl', 'run_wiki_compile_task', 'dispatch_wiki_compile', '_safe_wiki_compile', 'warm_celery_producer', '_screening_impl', 'run_screening_task', '_safe_screening', 'dispatch_screening_job', 'retry_fulltext_fetch', 'enqueue_fulltext_retry', 'run_chunk_quality_sample', '_kb_reparse_impl', 'run_kb_reparse_task', '_url_ingest_impl', 'run_url_ingest_task', '_task_ingest_impl', 'run_task_ingest_task', '_evidence_ingest_impl', 'run_evidence_ingest_task', '_jsonable', '_document_generate_impl', 'run_document_generate_task', '_wiki_lint_impl', 'run_wiki_lint_task', 'run_wiki_lint_sweep_task']
