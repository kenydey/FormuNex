"""Stage 4 / 4-3 (SR-021) route-slimming gates.

Routes stay thin: auth deps + input models + service calls + exception
mapping + commit boundaries. The gates below lock the 4-3 moves:

- chat orchestration lives in services/chat_orchestration.py; app/api/chat.py
  only defines the request model, auth glue and two thin wrappers.
- the PLM WebSocket compliance push lives in app/api/plm_ws.py.
- no route file uses the legacy ``session.query(...)`` API (the one
  plm.py use was converted to ``select()`` in this batch).
- line caps, nailed at the post-move sizes: growth fails loudly so the
  next fattening is a deliberate cap update, not silent drift.

Each checker is also run against a deliberately violating snippet, so a
vacuous checker (one that can never fail) fails this file instead.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "app"


def _tree(rel: str) -> ast.AST:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def _session_attrs(tree: ast.AST) -> list[str]:
    """Attribute names accessed on a bare ``session`` name."""
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "session"
        ):
            out.append(node.attr)
    return out


def _top_defs(tree: ast.AST) -> set[str]:
    return {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def test_chat_route_is_thin():
    tree = _tree("api/chat.py")
    assert _session_attrs(tree) == []
    assert _top_defs(tree) == {
        "ChatRequestValidated",
        "_bind_owner",
        "_chat_caller_id",
        "chat",
        "chat_stream",
    }
    # Negative control: the session checker must flag a violating snippet.
    bad = ast.parse("def f(session):\n    return session.query(X).all()\n")
    assert "query" in _session_attrs(bad)


def test_no_session_query_in_route_files():
    for rel in ("api/chat.py", "api/plm.py", "api/plm_ws.py"):
        assert "query" not in _session_attrs(_tree(rel)), rel
    # Negative control: the query checker must flag a violating snippet.
    bad = ast.parse("def f(session):\n    return session.query(X).first()\n")
    assert "query" in _session_attrs(bad)


def test_ws_push_lives_in_its_own_module():
    import app.api.plm as plm
    import app.api.plm_ws as plm_ws

    assert hasattr(plm_ws, "formulation_compliance_ws")
    assert hasattr(plm_ws, "ws_router")
    assert not hasattr(plm, "formulation_compliance_ws")
    assert not hasattr(plm, "ws_router")
    paths = [getattr(r, "path", None) for r in plm_ws.ws_router.routes]
    assert "/ws/formulations/{formulation_id}/compliance" in paths


def test_chat_orchestration_exports():
    import app.api.chat as chat_route
    import app.services.chat_orchestration as orch

    for name in (
        "run_chat",
        "stream_chat_events",
        "_apply_answer_gates",
        "_augment_with_kb",
        "_stream_answer_plan",
        "_apply_ref_docs",
        "sanitize_evidence",
        "clamp_relevance",
        "_finalize_evidence_fields",
        "_sse",
    ):
        assert hasattr(orch, name), name
    # The route wrappers still exist with the same paths.
    assert {getattr(r, "path", None) for r in chat_route.router.routes} == {
        "/chat",
        "/chat/stream",
    }


def test_route_line_caps():
    # Nailed at the post-4-3 sizes: growth fails, shrinkage is fine.
    # 5-2 / SR-045 added one net line to plm.py (exception logging at
    # the template parse/render raise sites); the cap moves with it.
    # 5-6 / SR-100 added the /substances/count endpoint (10 lines);
    # the cap moves with it for the same reason.
    # 5-7 / SR-046 added the docx structure helper plus capped reads
    # (23 lines); the cap moves with it for the same reason.
    # Deep-review fixes (2026-11-04, approved items only: P0-5 hazard
    # validator, P1-C1 lock endpoint, owner pass-through) added 67 lines;
    # cap raised 2031 -> 2098 on Cheng's 2026-11-05 decision.
    # P3-A3 (same batch) added a 9-line docstring to plm_ws.py
    # (WS ?token= exception); cap raised 191 -> 200 on Cheng's
    # 2026-11-07 decision. Both caps were masked behind each other:
    # the test stops at the first overrun, so plm_ws.py only surfaced
    # once plm.py was back under its cap.
    caps = {
        "api/chat.py": 146,
        "api/plm.py": 2098,
        "api/plm_ws.py": 200,
    }
    for rel, cap in caps.items():
        n = len((ROOT / rel).read_text(encoding="utf-8").splitlines())
        assert n <= cap, f"{rel} grew past its cap: {n} > {cap}"
