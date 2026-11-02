"""Stage 4 / SR-019: db must not depend on services through lazy imports.

What this batch did, and what this file locks:

- The pure helpers the db layer used to import from services now live in
  ``app/domain/pure.py`` (stdlib only): ``chunk_dedup_key`` (+ its
  ``normalize_text``), ``content_hash`` and ``wiki_root_from_db_url``.
  The old modules re-export them (migration 0042 imports
  ``chunk_dedup_key`` from ``services.kb_dedup``; migrations are frozen
  and are excluded from every scan here).
- Every function-local (lazy) services import in ``app/db/`` was hoisted
  to top level after verifying, on the top-level-only import graph, that
  the target cannot reach the importing module (so no import-time cycle
  is introduced). The one true cycle, ``wiki_store -> wiki.embed ->
  wiki_store``, was broken on the other side: ``embed.py`` now imports
  ``get_wiki_store`` inside the two functions that use it.
- The remaining db -> services edges are top-level and explicit. They
  are pinned below: the set may only shrink, never grow, without
  updating this test deliberately. A full import-graph DAG is NOT
  asserted: it is unreachable while the stage-1 recovery wiring
  (``db/dispatcher.py`` -> ``worker.tasks``) exists as a documented
  exception, and that exception is pinned to exactly that one edge.
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
DB = APP / "db"

# (importer, target) top-level db -> services edges, measured after the
# 4-4 move. A new edge here fails the test until it is deliberately
# added to this set with a reason.
PINNED_DB_SERVICES_EDGES = {
    ("app.db.campaign_store", "app.services.http_safe"),
    ("app.db.chunk_store", "app.services.kb_ann"),
    ("app.db.datalab_client", "app.services.http_safe"),
    ("app.db.datalab_client", "app.services.runtime_secrets"),
    ("app.db.material_store", "app.services.errors"),
    ("app.db.material_store", "app.services.supplier_normalize"),
    ("app.db.measurement_store", "app.services.errors"),
    ("app.db.product_store", "app.services.chemtools"),
    ("app.db.product_store", "app.services.errors"),
    ("app.db.project_store", "app.services.intent"),
    ("app.db.reconciliation", "app.services.http_safe"),
    ("app.db.source_store", "app.services.patent_ids"),
    ("app.db.source_store", "app.services.provenance"),
    ("app.db.sqlite_lock", "app.services.redis_breaker"),
    ("app.db.store", "app.services._fsutil"),
    ("app.db.store", "app.services.http_safe"),
    ("app.db.wiki_store", "app.services.wiki.embed"),
    ("app.db.wiki_store", "app.services.wiki.fts"),
}

SUNK_FUNCTIONS = (
    "chunk_dedup_key",
    "normalize_text",
    "content_hash",
    "wiki_root_from_db_url",
)


def _module_name(path: Path) -> str:
    rel = path.relative_to(APP)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return "app." + ".".join(parts)


def _module_set() -> set[str]:
    return {_module_name(p) for p in APP.rglob("*.py")}


def _longest_prefix(dep: str, modules: set[str]) -> str | None:
    parts = dep.split(".")
    for i in range(len(parts), 0, -1):
        cand = ".".join(parts[:i])
        if cand in modules:
            return cand
    return None


def _resolve(node: ast.ImportFrom, package: str) -> str:
    if node.level == 0:
        return node.module or ""
    base = package
    for _ in range(node.level - 1):
        base = base.rsplit(".", 1)[0] if "." in base else "app"
    return (base + "." + node.module) if node.module else base


def _collect(source: str, module: str, modules: set[str]) -> tuple[set[str], set[str]]:
    """Return (top-level edges, function-local edges) as resolved modules."""
    tree = ast.parse(source)
    package = module.rsplit(".", 1)[0] if "." in module else "app"
    top: set[str] = set()
    lazy: set[str] = set()

    def walk(node: ast.AST, in_func: bool) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            in_func = True
        if isinstance(node, ast.ImportFrom):
            base = _resolve(node, package)
            for alias in node.names:
                target = _longest_prefix(base + "." + alias.name, modules) or _longest_prefix(
                    base, modules
                )
                if target and target != module:
                    (lazy if in_func else top).add(target)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                target = _longest_prefix(alias.name, modules)
                if target and target != module:
                    (lazy if in_func else top).add(target)
        for child in ast.iter_child_nodes(node):
            walk(child, in_func)

    walk(tree, False)
    return top, lazy


def _db_files() -> list[Path]:
    return sorted(p for p in DB.rglob("*.py") if "alembic" not in p.parts)


def _scan_db() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    modules = _module_set()
    top_all: dict[str, set[str]] = {}
    lazy_all: dict[str, set[str]] = {}
    for path in _db_files():
        module = _module_name(path)
        top, lazy = _collect(path.read_text(encoding="utf-8"), module, modules)
        top_all[module] = top
        lazy_all[module] = lazy
    return top_all, lazy_all


def test_pure_module_is_stdlib_only_and_defines_the_sunk_functions():
    pure = APP / "domain" / "pure.py"
    tree = ast.parse(pure.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, "pure.py must not use relative imports"
            assert not (node.module or "").startswith("app"), "pure.py must not import app code"
        elif isinstance(node, ast.Import):
            assert all(not a.name.startswith("app") for a in node.names)
    defined = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    for name in SUNK_FUNCTIONS:
        assert name in defined, f"{name} must be defined in app/domain/pure.py"


def test_db_imports_the_sunk_functions_from_domain_pure():
    chunk = (DB / "chunk_store.py").read_text(encoding="utf-8")
    assert "from ..domain.pure import chunk_dedup_key" in chunk
    assert "services.kb_dedup import" not in chunk
    wiki = (DB / "wiki_store.py").read_text(encoding="utf-8")
    assert "from ..domain.pure import content_hash, wiki_root_from_db_url" in wiki
    assert "services.wiki.schema import" not in wiki


def test_no_function_local_service_or_api_imports_in_db():
    _, lazy_all = _scan_db()
    violations = []
    for module, edges in lazy_all.items():
        for target in edges:
            if target.startswith("app.services") or target.startswith("app.api"):
                violations.append((module, target))
    assert violations == [], f"lazy db -> services/api imports remain: {violations}"


def test_worker_edge_is_only_the_documented_dispatcher_exception():
    top_all, lazy_all = _scan_db()
    worker_edges = set()
    for module in set(top_all) | set(lazy_all):
        for target in top_all.get(module, set()) | lazy_all.get(module, set()):
            if target.startswith("app.worker"):
                worker_edges.add((module, target))
    assert worker_edges == {("app.db.dispatcher", "app.worker.tasks")}


def test_db_services_edge_set_is_pinned_and_no_db_api_edges():
    top_all, _ = _scan_db()
    edges = set()
    for module, targets in top_all.items():
        for target in targets:
            if target.startswith("app.services"):
                edges.add((module, target))
            assert not target.startswith("app.api"), f"db -> api edge: {module} -> {target}"
    assert edges == PINNED_DB_SERVICES_EDGES, (
        f"db -> services edge set changed.\n"
        f"added: {sorted(edges - PINNED_DB_SERVICES_EDGES)}\n"
        f"removed: {sorted(PINNED_DB_SERVICES_EDGES - edges)}"
    )


def test_checker_flags_a_lazy_service_import_and_a_new_edge():
    modules = _module_set() | {"app.db.fake_store", "app.services.brand_new"}
    source = '''
from ..services import brand_new


def write():
    from ..services import brand_new

    return brand_new.thing()
'''
    top, lazy = _collect(source, "app.db.fake_store", modules)
    assert "app.services.brand_new" in lazy
    assert ("app.db.fake_store", "app.services.brand_new") not in PINNED_DB_SERVICES_EDGES
    clean = "import logging\n\n\ndef write():\n    return logging.getLogger(__name__)\n"
    top2, lazy2 = _collect(clean, "app.db.fake_store", modules)
    assert not (top2 | lazy2) & {"app.services.brand_new"}
