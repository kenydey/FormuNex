"""X-P1-2 (review 2026-11-26): api-contract audit script, fixed.

The v9 script was blind and toothless: hidden routes (``include_in_schema=False``)
never appeared, ``main()`` always returned 0, and 239+ lines of noise buried
the signal. These tests pin the v10 denoising + dual-source audit + non-zero
exit-code semantics — without importing the real app for the pure parts.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "verify_frontend_api.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("verify_frontend_api", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_frontend_api"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script()


# ── norm / interpolation stripping ──────────────────────────────────────────


def test_norm_strips_query_and_placeholders(script) -> None:
    assert script.norm("/api/doe?design=x") == "/api/doe"
    assert script.norm("/api/doe/${id}/attachments") == "/api/doe/{}/attachments"
    assert script.norm("/api/doe/{plan_id}") == "/api/doe/{}"
    assert script.norm("/api/wiki/literature/${id}/export.${fmt}") == (
        "/api/wiki/literature/{}/export.{}"
    )


def test_norm_collapses_trailing_query_variable(script) -> None:
    # ``${qs}`` holds "?ratio=..." — glued to the previous placeholder, it is
    # a query string, not a path segment.
    assert script.norm("/api/kg/neo4j/formulations/{}/compounds/{}{}") == (
        "/api/kg/neo4j/formulations/{}/compounds/{}"
    )


def test_strip_interpolations_handles_nested_templates(script) -> None:
    line = "call(\"GET\", `/api/experiments/search${q ? `?q=${q}` : \"\"}`)"
    stripped = script.strip_interpolations(line)
    assert stripped == "call(\"GET\", `/api/experiments/search{}`)"
    # The leftover "{}" is the query append, not a segment: segment matching
    # (not norm) is what pairs it with the bare route.
    assert script.path_matches(script.norm("/api/experiments/search{}"), "/api/experiments/search")


def test_strip_interpolations_handles_ternary_literals(script) -> None:
    line = "call(\"GET\", `/api/chemical/${s.mode === \"scaffold\" ? \"scaffold-substitutes\" : \"substructure\"}?${p}`)"
    assert script.strip_interpolations(line) == "call(\"GET\", `/api/chemical/{}?{}`)"
    assert script.norm("/api/chemical/{}?{}") == "/api/chemical/{}"


# ── segment-wise matching ───────────────────────────────────────────────────


def test_path_matches_literal_vs_param(script) -> None:
    assert script.path_matches("/api/plm/documents/{}/file/pdf", "/api/plm/documents/{}/file/{}")
    assert script.path_matches("/api/plm/import/{}", "/api/plm/import/companies")
    assert script.path_matches("/api/plm/products/{}/{}", "/api/plm/products/{}/archive")
    assert script.path_matches("/api/wiki/literature/{}/export.{}", "/api/wiki/literature/{}/export.bib")
    assert script.path_matches("/api/kb/sources/{}/reparse", "/api/kb/sources/{}/reparse")
    assert not script.path_matches("/api/kb/sources/{}", "/api/kb/sources/{}/reparse")
    # A variable action segment may be "abort" — placeholder matches literal.
    assert script.path_matches("/api/doe/{}/{}", "/api/doe/{}/abort")
    assert not script.path_matches("/api/doe/{}/abort", "/api/doe/{}/export")
    assert not script.path_matches("/api/chat/stream", "/api/chat")


# ── audit() on synthetic inputs ─────────────────────────────────────────────


def _live(*paths: str, hidden: tuple[str, ...] = ()) -> dict:
    return {p: {"hidden": p in hidden} for p in paths}


def test_audit_dead_call_detected(script) -> None:
    result = script.audit(
        {"/api/gone/{}": 1, "/api/ok": 2},
        _live("/api/ok"),
        {"/api/ok"},
    )
    assert result["dead"] == ["/api/gone/{}"]
    assert result["unused"] == []


def test_audit_hidden_route_called_uses_exact_match(script) -> None:
    # ``/api/sources/export`` is a real call, but it must NOT mark the hidden
    # param route ``/api/sources/{}`` as called — segment matching would.
    result = script.audit(
        {"/api/sources/export": 1},
        _live("/api/sources/export", "/api/sources/{}", hidden=("/api/sources/{}",)),
        {"/api/sources/export"},
    )
    assert result["hidden"] == ["/api/sources/{}"]
    assert result["hidden_called"] == {"/api/sources/{}": False}
    assert result["dead"] == []


def test_audit_deleting_a_called_hidden_route_is_dead(script) -> None:
    # A2 acceptance: deleting a hidden route the frontend calls must fail
    # the audit — the call has nowhere to go.
    result = script.audit(
        {"/api/kb/products": 1},
        _live("/api/kb/other"),
        {"/api/kb/other"},
    )
    assert result["dead"] == ["/api/kb/products"]


def test_audit_divergence_outside_hidden_set(script) -> None:
    result = script.audit(
        {},
        _live("/api/visible", "/api/sneaky", hidden=("/api/sneaky",)),
        {"/api/visible"},
    )
    assert result["divergence"] == []
    assert result["stale_openapi"] == []
    result = script.audit(
        {},
        _live("/api/visible", "/api/sneaky"),
        {"/api/visible"},
    )
    assert result["divergence"] == ["/api/sneaky"]
    result = script.audit(
        {},
        _live("/api/visible"),
        {"/api/visible", "/api/stale"},
    )
    assert result["stale_openapi"] == ["/api/stale"]


def test_audit_prefix_helper_reclassification(script) -> None:
    live = _live(
        "/api/experiments/workbench/{}/rows/{}/attachments",
        "/api/kb/sources/{}/reparse",
        "/api/reviews/runs/{}",
    )
    result = script.audit(
        {},
        live,
        set(live),
        prefix_helpers={
            "/api/experiments/workbench/{}/rows/{}": ["row.ts:11"],
            "/api/kb/sources/{}/reparse": ["reparse.ts:10"],
            "/api/reviews/runs/{}": ["Reviewer.tsx:14"],
            "/api/nope/{}": ["ghost.ts:1"],
        },
    )
    # Complete live routes reached through a helper count as calls.
    assert "/api/kb/sources/{}/reparse" not in result["dead"]
    assert "/api/reviews/runs/{}" not in result["dead"]
    assert "/api/kb/sources/{}/reparse" not in result["unused"]
    assert "/api/reviews/runs/{}" not in result["unused"]
    # A pure prefix stays a prefix helper, never a dead call.
    assert result["prefix_helpers"] == {
        "/api/experiments/workbench/{}/rows/{}": ["row.ts:11"],
    }
    assert result["dead"] == ["/api/nope/{}"]


# ── dual-source collection on a synthetic app ───────────────────────────────


def test_collect_backend_sees_hidden_routes() -> None:
    from fastapi import APIRouter, FastAPI

    sub = APIRouter(prefix="/kb")
    sub.get("/stats")(lambda: {})
    sub.get("/products", include_in_schema=False)(lambda: {})

    fake = FastAPI()
    fake.include_router(sub, prefix="/api")
    live, openapi_paths = _load_script().collect_backend(fake)
    assert live["/api/kb/stats"] == {"hidden": False}
    assert live["/api/kb/products"] == {"hidden": True}
    assert "/api/kb/stats" in openapi_paths
    assert "/api/kb/products" not in openapi_paths


# ── the real app at HEAD ────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (REPO / "frontend" / "src").is_dir(), reason="frontend sources not present"
)
def test_real_app_audit_has_no_dead_calls(script) -> None:
    from app.main import app

    frontend_calls, _sources, prefix_helpers = script.scan_frontend()
    live, openapi_paths = script.collect_backend(app)
    result = script.audit(frontend_calls, live, openapi_paths, prefix_helpers)
    assert result["dead"] == []
    assert result["divergence"] == []
    assert result["stale_openapi"] == []
    assert set(result["hidden"]) == {
        "/api/experiments/hooks/convergence",
        "/api/experiments/hooks/doecyle-status/{}",
        "/api/kb/ingest",
        "/api/kb/products",
        "/api/kg/calibration",
        "/api/sources/{}",
        "/api/templates/{}",
        "/api/train",
    }
    assert result["hidden_called"]["/api/kb/products"] is True
    assert result["hidden_called"]["/api/kg/calibration"] is True
