"""Stage 4 / SR-057: worker/tasks package split compatibility locks.

The old ``app.worker.tasks`` module is now a package (``tasks/`` by domain).
These tests lock the compatibility surface the split promised:

- the package object is the package, not a leftover module file;
- every framework helper / dispatch helper / alias that app code and tests
  imported from ``app.worker.tasks`` is still reachable there;
- the shared logger keeps the old module name in its output;
- the intra-package import graph stays a DAG with exactly one cross-domain
  edge (research/search -> kb for ``dispatch_kb_ingest``).
"""
import ast
from pathlib import Path

import app.worker.tasks as tasks

FRAMEWORK_HELPERS = [
    "TaskManager",
    "task_manager",
    "_OutboxTask",
    "_persist_task",
    "_persist_terminal",
    "_task_file",
    "load_persisted_task",
    "list_persisted_task_ids",
    "cancel_task",
    "_status_from_progress",
    "_thinking_title",
    "warm_celery_producer",
    "dispatch_file_ingest",
    "dispatch_kb_ingest",
    "dispatch_wiki_compile",
    "dispatch_screening_job",
    "enqueue_fulltext_retry",
    "_notify_dossier_optimize_completed",
    "_register_inverse_design_round",
    "_screening_impl",
    "optimize_task",
    "loop_task",
    "deep_research_task",
    "recommend_task",
    "ingest_patents_task",
    "wiki_storm_report_task",
]

# module -> siblings it may import at top level (besides _common).
ALLOWED_SIBLING_EDGES = {
    "_common": set(),
    "kb": set(),
    "research": {"kb"},
    "doe": set(),
    "plm": set(),
    "wiki": set(),
    "search": {"kb"},
    "lab": set(),
}


def test_package_is_package_not_module_file():
    assert tasks.__file__.endswith("tasks/__init__.py")


def test_framework_helpers_still_reachable():
    missing = [n for n in FRAMEWORK_HELPERS if not hasattr(tasks, n)]
    assert not missing


def test_logger_name_unchanged_by_split():
    assert tasks.logger.name == "app.worker.tasks"


def test_intra_package_import_graph_is_dag():
    pkg = Path(tasks.__file__).parent
    siblings = set(ALLOWED_SIBLING_EDGES)
    for mod, allowed in ALLOWED_SIBLING_EDGES.items():
        tree = ast.parse((pkg / f"{mod}.py").read_text(encoding="utf-8"))
        edges = set()
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                if node.module in siblings and node.module != "_common":
                    edges.add(node.module)
        assert edges <= allowed, (mod, edges)
