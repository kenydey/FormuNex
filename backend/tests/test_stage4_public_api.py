"""Stage 4 / SR-054: services expose a public API; routes stop reaching
into service privates, and services stop raising HTTPException.

- No api module may import an underscore member from a services module
  (``from ..services.x import _priv`` or ``from ..services import _mod``),
  nor touch one through a services-module alias (``exports_svc._priv``).
  The promoted names (``DispatchTimeout``, ``clamp_relevance``,
  ``sanitize_evidence``, ``link_campaign_to_project``,
  ``pubchempy_available``, ``rules_dir``, ``FALLBACKS``,
  ``provider_default_base_url``, ``is_safe_url``, ``mask``) are pinned
  as the public replacements.
- ``project_exports`` and ``literature_screening`` raise domain errors
  only (``ProjectExport*Error`` / ``ScreeningValidationError``); the
  route layer maps them to HTTP statuses. The wiki evaluate endpoint's
  ValueError -> 400 mapping is pinned structurally (it mirrors the
  sibling screening endpoints).
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
API = APP / "api"

# (module file, old private name, new public name)
PROMOTED = [
    ("app/services/dispatch.py", "_DispatchTimeout", "DispatchTimeout"),
    ("app/services/chat_orchestration.py", "_clamp_relevance", "clamp_relevance"),
    ("app/services/chat_orchestration.py", "_sanitize_evidence", "sanitize_evidence"),
    ("app/services/workbench_training.py", "_link_campaign_to_project", "link_campaign_to_project"),
    ("app/services/compounds.py", "_pubchempy_available", "pubchempy_available"),
    ("app/services/rule_loader.py", "_rules_dir", "rules_dir"),
    ("app/services/rule_loader.py", "_FALLBACKS", "FALLBACKS"),
    ("app/services/llm.py", "_provider_default_base_url", "provider_default_base_url"),
    ("app/services/ingestion.py", "_is_safe_url", "is_safe_url"),
    ("app/services/secrets_store.py", "_mask", "mask"),
    ("app/services/table_contract.py", "_tables_path", "tables_path"),
    ("app/services/reviewer_fix_loop.py", "_run_path", "run_path"),
    ("app/services/llm.py", "_call_llm", "call_llm"),
]

DOMAIN_ERROR_MODULES = {
    "app/services/project_exports.py": (
        "ProjectExportError", "ProjectExportNotFoundError",
        "ProjectExportValidationError", "ProjectExportTooLargeError",
    ),
    "app/services/literature_screening.py": ("ScreeningValidationError",),
}


def _module_name(path: Path) -> str:
    rel = path.relative_to(APP)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return "app." + ".".join(parts)


def _resolve_importfrom(module: str, level: int, package: str) -> str:
    if level == 0:
        return module or ""
    base = package
    for _ in range(level - 1):
        base = base.rsplit(".", 1)[0] if "." in base else "app"
    return (base + "." + module) if module else base


def _is_services_module(resolved: str) -> bool:
    return resolved == "app.services" or resolved.startswith("app.services.")


def _underscore_imports(source: str, module: str) -> list[str]:
    """Underscore members imported from services modules (ImportFrom + Import)."""
    tree = ast.parse(source)
    package = module.rsplit(".", 1)[0] if "." in module else "app"
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = _resolve_importfrom(node.module or "", node.level, package)
            if not _is_services_module(base):
                continue
            for alias in node.names:
                if alias.name.startswith("_"):
                    violations.append(f"{module}:{node.lineno}: {base}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if "services" in parts and any(p.startswith("_") for p in parts[parts.index("services") + 1:]):
                    violations.append(f"{module}:{node.lineno}: {alias.name}")
    return violations


def _services_aliases(source: str, module: str) -> set[str]:
    """Names bound to services modules (``from ..services import X [as Y]``).

    Tracked per file, not per function: api modules use function-local
    imports heavily, and an over-approximation here only risks a false
    positive if a file reuses such a name for something else.
    """
    tree = ast.parse(source)
    package = module.rsplit(".", 1)[0] if "." in module else "app"
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = _resolve_importfrom(node.module or "", node.level, package)
            if base == "app.services":
                for alias in node.names:
                    aliases.add(alias.asname or alias.name)
    return aliases


def _underscore_attribute_access(source: str, module: str) -> list[str]:
    tree = ast.parse(source)
    aliases = _services_aliases(source, module)
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            root = node
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in aliases:
                violations.append(f"{module}:{node.lineno}: {root.id}.{node.attr}")
    return violations


def test_no_api_import_of_services_privates():
    violations = []
    for path in sorted(API.rglob("*.py")):
        module = _module_name(path)
        source = path.read_text(encoding="utf-8")
        violations += _underscore_imports(source, module)
        violations += _underscore_attribute_access(source, module)
    assert violations == [], f"api -> services private imports remain:\n" + "\n".join(violations)


def test_services_raise_domain_errors_not_http():
    for rel, error_names in DOMAIN_ERROR_MODULES.items():
        tree = ast.parse((APP.parent / rel).read_text(encoding="utf-8"))
        defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for name in error_names:
            assert name in defined, f"{name} must be defined in {rel}"
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is not None:
                func = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
                if isinstance(func, ast.Name) and func.id == "HTTPException":
                    raise AssertionError(f"{rel}:{node.lineno} still raises HTTPException")
                if isinstance(func, ast.Attribute) and func.attr == "HTTPException":
                    raise AssertionError(f"{rel}:{node.lineno} still raises HTTPException")


def test_promoted_names_exist_and_privates_are_gone():
    for rel, old, new in PROMOTED:
        tree = ast.parse((APP.parent / rel).read_text(encoding="utf-8"))
        defined = set()
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        defined.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                defined.add(node.target.id)
        imported = {a.name for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) for a in n.names}
        assert new in defined or new in imported, f"{new} missing from {rel}"
        assert old not in defined, f"{old} still defined in {rel} (should be {new})"


def test_wiki_evaluate_endpoint_maps_value_error_to_400():
    tree = ast.parse((API / "wiki.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "screening_evaluate_endpoint")
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name) \
                and node.type.id == "ValueError":
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Raise) and isinstance(stmt.exc, ast.Call):
                    func = stmt.exc.func
                    if isinstance(func, ast.Name) and func.id == "HTTPException":
                        codes = {kw.value.value for kw in stmt.exc.keywords
                                 if kw.arg == "status_code" and isinstance(kw.value, ast.Constant)}
                        assert 400 in codes
                        return
    raise AssertionError("screening_evaluate_endpoint must map ValueError to HTTP 400")


def test_checker_flags_synthetic_violations():
    source = (
        "from ..services import _secret_mod\n"
        "from ..services.foo import _hidden, public_fn\n"
        "from ..services import bar as bar_svc\n"
        "\n"
        "\n"
        "def f():\n"
        "    return bar_svc._priv()\n"
    )
    violations = _underscore_imports(source, "app.api.fake")
    violations += _underscore_attribute_access(source, "app.api.fake")
    assert len(violations) == 3, violations
    clean = "from ..services.foo import public_fn\n"
    assert _underscore_imports(clean, "app.api.fake") == []
    assert _underscore_attribute_access(clean, "app.api.fake") == []
