"""Stage 4 / SR-058: domain/schemas.py split by boundary.

``app/domain/schemas.py`` (1265 lines) is now the package
``app/domain/schemas/{chat,doe,research,task}.py``; ``schemas/__init__.py``
is a compatibility re-export shim only. These tests pin:

- every DTO/helper/constant the old module exposed is still importable
  from ``app.domain.schemas`` and is the identical object as the one in
  its boundary module (no name lost, no copy);
- the shim defines no classes or functions of its own (new DTOs must go
  in a boundary module, never back into the shim);
- the boundary import DAG stays acyclic: chat is a leaf, doe may use
  chat, research may use chat/doe, task is a leaf;
- tradeoff_schemas depends on the doe boundary directly (``.schemas.doe``),
  not on the shim, so the package __init__ can load .doe first.
"""

import ast
import importlib
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
SCHEMAS = APP / "domain" / "schemas"
BOUNDARIES = ("chat", "doe", "research", "task")
TRADEOFF_NAMES = ("TradeOffAnalysis", "VerificationDoe")


def _top_level_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.FunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _boundary_modules() -> dict[str, object]:
    return {name: importlib.import_module(f"app.domain.schemas.{name}") for name in BOUNDARIES}


def test_shim_re_exports_every_boundary_name_identically():
    import app.domain.schemas as shim

    modules = _boundary_modules()
    for boundary, module in modules.items():
        for name in _top_level_names(SCHEMAS / f"{boundary}.py"):
            assert hasattr(shim, name), f"schemas shim lost {name} (from {boundary})"
            assert getattr(shim, name) is getattr(module, name), f"{name} is a copy, not a re-export"
    import app.domain.tradeoff_schemas as tradeoff

    for name in TRADEOFF_NAMES:
        assert getattr(shim, name) is getattr(tradeoff, name)
    public = {n for module in modules.values() for n in _top_level_names(
        SCHEMAS / f"{module.__name__.rsplit('.', 1)[-1]}.py") if not n.startswith("_")}
    public |= set(TRADEOFF_NAMES)
    assert set(shim.__all__) == public


def test_shim_defines_no_dtos_itself():
    tree = ast.parse((SCHEMAS / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        assert not isinstance(node, (ast.ClassDef, ast.FunctionDef)), (
            "new DTOs must go in a boundary module, not the schemas shim")


def test_boundary_import_dag_is_acyclic():
    allowed = {
        "chat": set(),
        "doe": {"chat"},
        "research": {"chat", "doe"},
        "task": set(),
    }
    for boundary in BOUNDARIES:
        tree = ast.parse((SCHEMAS / f"{boundary}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module in BOUNDARIES:
                assert node.module in allowed[boundary], (
                    f"schemas.{boundary} must not import schemas.{node.module}")


def test_tradeoff_schemas_depends_on_doe_boundary_not_the_shim():
    source = (APP / "domain" / "tradeoff_schemas.py").read_text(encoding="utf-8")
    assert "from .schemas.doe import ObjectiveSpec" in source
    assert "from .schemas import ObjectiveSpec" not in source
