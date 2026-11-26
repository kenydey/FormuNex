#!/usr/bin/env python3
"""Frontend↔backend API cross-audit (v10+).

Extracts every `/api/...` string literal from frontend src and cross-checks
against the backend's LIVE route table (``app.routes``, including
``include_in_schema=False`` routes) plus the OpenAPI schema. Reports:

  ① frontend calls with NO backend route  (dead / hanging calls)
  ①-b path-prefix helpers (``const row = (...) => `/api/...``) — the literal
      is only a prefix (``row()`` / ``base()``), never fetched alone; listed,
      not counted as calls (their suffixes are out of this scanner's reach).
      A helper that IS a complete live route (``reparsePath(id)`` passed
      straight to ``call()``) counts as a call; a helper matching no live
      route at all counts as dead in ①.
  ② backend routes with NO frontend call (candidate dead routes, informational)
  ③ hidden routes: live but absent from OpenAPI (``include_in_schema=False``),
      with whether the frontend calls them — deleting one is invisible to
      an OpenAPI-only audit, which is exactly the blind spot this section closes
  ④ live-routes-vs-OpenAPI divergence other than the hidden set (stale snapshot)

Exit code: 1 when section ① finds a dead call, or section ④ finds a
divergence outside the hidden set; 0 otherwise. Section ② never fails the
run (many routes are deliberately API-only). The CI job stays warning-only
(``continue-on-error``) until the hidden-route decisions land — the script
itself is the gate, the job is not (yet) the enforcer.

Denoising (each rule exists because the v9 output was 239–295 lines of noise):
- ``*.test.*`` files are skipped (assertion strings are not calls);
- ``frontend/src/api/openapi.json`` and ``schema.d.ts`` are skipped (vendored
  schema text contains ``{param}`` literals that never match a live route);
- doc-comment lines (``/** ...`` / ``* ...`` / ``// ...``) are skipped;
- ``${...}`` interpolations are stripped with a nesting-aware scanner, so
  ``${cond ? `?q=${q}` : ""}`` query appends no longer truncate the match;
- ``{param}`` placeholders (vendored / test-style) normalise to ``{}``;
- matching is segment-wise: a ``{}`` segment matches any literal segment
  (``/file/pdf`` hits ``/file/{kind}``), and a ``{}`` inside a segment
  matches when the static affixes fit (``/export.${fmt}`` hits ``/export.bib``);
- a trailing run of adjacent placeholders (``${qs}`` query-string variables
  glued to the previous placeholder) collapses to one.

Known limitations (audited by hand at v10):
- paths built by concatenating constants are missed (false negative on ②);
- a prefix helper's concatenated suffixes are not resolved (see ①-b);
- ``call()`` wrappers that take the URL from a variable are missed.

Usage:  backend/.venv/bin/python scripts/verify_frontend_api.py
        (run from repo root; requires the backend venv for `app.main`)
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(REPO, "frontend", "src")
BACKEND = os.path.join(REPO, "backend")

sys.path.insert(0, BACKEND)
from app.main import app  # noqa: E402

API_RE = re.compile(r"""['"`](/api/[^'"`\n]+)['"`]""")
# ``const row = (c, r) => `/api/...`` — a prefix helper, never fetched alone.
PREFIX_HELPER_RE = re.compile(r"""^\s*(?:export\s+)?const\s+\w+\s*=\s*\([^)]*\)\s*=>\s*['"`]/api/""")
SKIP_FILES = ("openapi.json", "schema.d.ts")


def _string_end(text: str, i: int) -> int:
    """Index just past the string literal starting at ``text[i]`` (a quote).

    Inside a backtick template, ``${...}`` expressions are skipped with
    nesting-aware brace counting (they may contain nested templates).
    """
    quote = text[i]
    i += 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if quote == "`" and text.startswith("${", i):
            i = _expr_end(text, i + 2)
            continue
        if ch == quote:
            return i + 1
        i += 1
    return len(text)


def _expr_end(text: str, i: int) -> int:
    """Index just past the ``}`` closing the ``${`` expression at ``i - 2``."""
    depth = 1
    while i < len(text):
        ch = text[i]
        if ch in "'\"`":
            i = _string_end(text, i)
        elif ch == "{":
            depth += 1
            i += 1
        elif ch == "}":
            depth -= 1
            i += 1
            if depth == 0:
                return i
        else:
            i += 1
    return len(text)


def strip_interpolations(line: str) -> str:
    """Replace every ``${...}`` in *line* with ``{}`` (nesting-aware).

    A flat ``${...}`` regex (``[^}]*``) cannot do this: query appends look like
    ``${q ? `?q=${q}` : ""}`` — the inner backtick string and its own
    ``${q}`` break both the brace match and the quote match that follows.
    """
    out: list[str] = []
    i, n = 0, len(line)
    while i < n:
        if line.startswith("${", i):
            out.append("{}")
            i = _expr_end(line, i + 2)
        else:
            out.append(line[i])
            i += 1
    return "".join(out)


def norm(p: str) -> str:
    """/api/experiments/${id}/attachments -> /api/experiments/{}/attachments
    query strings stripped: /api/doe?design=x -> /api/doe
    ``{param}`` placeholders normalised: /api/doe/{plan_id} -> /api/doe/{}
    a trailing run of glued placeholders (``${qs}`` query-string variables)
    collapses to one: /api/x/{}{} -> /api/x/{}"""
    p = strip_interpolations(p)
    p = p.split("?", 1)[0]
    p = re.sub(r"\{[^}]*\}", "{}", p)
    parts = p.split("/")
    if parts and re.fullmatch(r"(\{\}){2,}", parts[-1]):
        parts[-1] = "{}"
    return "/".join(parts)


def _segment_matches(front: str, back: str) -> bool:
    """One path segment matches when equal, when either side is a bare
    placeholder, or when a placeholder inside the segment fits the other's
    static affixes (``export.{}`` vs ``export.bib``)."""
    if front == back or front == "{}" or back == "{}":
        return True
    for templ, other in ((front, back), (back, front)):
        if "{}" in templ:
            pieces = templ.split("{}")
            if other.startswith(pieces[0]) and other.endswith(pieces[-1]):
                if len(other) >= sum(len(x) for x in pieces):
                    return True
    return False


def path_matches(front: str, back: str) -> bool:
    """Segment-wise route match (both sides already normalised)."""
    f_parts, b_parts = front.split("/"), back.split("/")
    return len(f_parts) == len(b_parts) and all(
        _segment_matches(f, b) for f, b in zip(f_parts, b_parts)
    )


def scan_frontend() -> tuple[Counter[str], dict[str, list[str]], dict[str, list[str]]]:
    """Walk frontend src; return (calls, sources, prefix_helpers).

    ``sources`` maps each normalised call to ``file:line`` hits (first 3 kept
    by the printer). ``prefix_helpers`` maps each helper literal the same way.
    """
    frontend_calls: Counter[str] = Counter()
    sources: dict[str, list[str]] = {}
    prefix_helpers: dict[str, list[str]] = {}
    for dirpath, _, files in os.walk(FRONTEND):
        if "node_modules" in dirpath:
            continue
        for fn in files:
            if ".test." in fn or fn in SKIP_FILES:
                continue
            if not fn.endswith((".ts", ".tsx", ".js", ".jsx")):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    stripped = line.strip()
                    if (
                        stripped.startswith("*")
                        or stripped.startswith("/*")
                        or stripped.startswith("//")
                    ):
                        continue
                    clean = strip_interpolations(line)
                    for m in API_RE.finditer(clean):
                        call = norm(m.group(1))
                        loc = f"{os.path.relpath(path, REPO)}:{lineno}"
                        if PREFIX_HELPER_RE.match(clean):
                            prefix_helpers.setdefault(call, []).append(loc)
                        else:
                            frontend_calls[call] += 1
                            sources.setdefault(call, []).append(loc)
    return frontend_calls, sources, prefix_helpers


def iter_live_routes(routes, prefix: str = ""):
    """Yield ``(effective_path, route)`` for every mounted route.

    Newer FastAPI stores ``include_router`` entries as ``_IncludedRouter``
    (the real routes live on ``original_router``); the effective path is the
    accumulated include prefixes plus the route's own path (which already
    carries its router prefix, e.g. ``/kb`` + ``/stats`` under ``/api``).
    """
    for r in routes:
        if type(r).__name__ == "_IncludedRouter":
            sub = getattr(r, "original_router", None)
            if sub is None:
                continue
            ctx = getattr(r, "include_context", None)
            sub_prefix = prefix + (getattr(ctx, "prefix", "") or "")
            yield from iter_live_routes(getattr(sub, "routes", []), sub_prefix)
        elif type(r).__name__ == "Mount":
            yield from iter_live_routes(getattr(r, "routes", []), prefix)
        else:
            yield prefix + (getattr(r, "path", "") or ""), r


def collect_backend(application) -> tuple[dict[str, dict], set[str]]:
    """Return (live_routes, openapi_paths), both normalised to ``{}``.

    ``live_routes`` maps path -> ``{"hidden": bool}`` (hidden =
    ``include_in_schema=False`` on every route for that path); ``openapi_paths``
    is the same normalisation over ``app.openapi()["paths"]`` (``/api`` only).
    """
    live: dict[str, dict] = {}
    for path, route in iter_live_routes(application.routes):
        methods = getattr(route, "methods", None)
        if not methods or not path.startswith("/api"):
            continue
        norm_path = re.sub(r"\{[^}]*\}", "{}", path)
        entry = live.setdefault(norm_path, {"hidden": True})
        if getattr(route, "include_in_schema", True):
            entry["hidden"] = False
    openapi_paths = {
        re.sub(r"\{[^}]*\}", "{}", p)
        for p in application.openapi()["paths"]
        if p.startswith("/api")
    }
    return live, openapi_paths


def audit(frontend_calls, live, openapi_paths, prefix_helpers=None) -> dict:
    """Pure audit over already-collected inputs (unit-testable without the app).

    Prefix helpers are reclassified here, where the live routes are known:
    a helper that matches a live route IS a call (``reparsePath(id)`` passed
    straight to ``call()``); a helper that is only a strict prefix of live
    routes stays a prefix helper (``row()`` / ``base()``); a helper matching
    neither is a dead call — its uses build a URL that does not exist.
    """
    prefix_helpers = prefix_helpers or {}
    live_paths = set(live)
    calls = Counter(frontend_calls)
    helper_sources: dict[str, list[str]] = {}
    remaining_helpers: dict[str, list[str]] = {}
    for helper, locs in prefix_helpers.items():
        if any(path_matches(helper, p) for p in live_paths):
            calls[helper] += len(locs)
            helper_sources.setdefault(helper, []).extend(locs)
        elif any(
            len(p.split("/")) > len(helper.split("/"))
            and all(
                _segment_matches(h, b)
                for h, b in zip(helper.split("/"), p.split("/"))
            )
            for p in live_paths
        ):
            remaining_helpers[helper] = locs
        else:
            # Matches no live route and prefixes none: its uses build a URL
            # that does not exist — a dead call, not a prefix helper.
            calls[helper] += len(locs)
            helper_sources.setdefault(helper, []).extend(locs)
    dead = sorted(
        c for c in calls
        if not any(path_matches(c, p) for p in live_paths)
    )
    unused = sorted(
        p for p in live_paths
        if not any(path_matches(c, p) for c in calls)
    )
    hidden = sorted(p for p, v in live.items() if v["hidden"])
    # Exact match on purpose: a param route also segment-matches literal
    # siblings (``/api/sources/{}`` vs the frontend's ``/api/sources/export``),
    # which would wrongly report the hidden route as called.
    hidden_called = {p: p in set(calls) for p in hidden}
    # A live route missing from OpenAPI must be a hidden one, and vice versa.
    divergence = sorted((live_paths - openapi_paths) - set(hidden))
    stale_openapi = sorted(openapi_paths - live_paths)
    return {
        "dead": dead,
        "unused": unused,
        "hidden": hidden,
        "hidden_called": hidden_called,
        "divergence": divergence,
        "stale_openapi": stale_openapi,
        "prefix_helpers": remaining_helpers,
        "sources": helper_sources,
    }


def main() -> int:
    frontend_calls, sources, prefix_helpers = scan_frontend()
    live, openapi_paths = collect_backend(app)
    result = audit(frontend_calls, live, openapi_paths, prefix_helpers)
    sources.update(result["sources"])
    prefix_helpers = result["prefix_helpers"]
    dead, unused = result["dead"], result["unused"]
    hidden, hidden_called = result["hidden"], result["hidden_called"]

    print(f"前端 API 调用（去重）: {len(frontend_calls)}")
    print(f"后端 live 路由: {len(live)}（其中隐藏 {len(hidden)} 条）")
    print(f"后端 OpenAPI 路由: {len(openapi_paths)}\n")

    print("=== ① 前端调用但后端无此路由（死调用/隐患）===")
    for c in dead:
        print(f"  {c}  (引用 {frontend_calls[c]} 处: {', '.join(sources[c][:3])})")
    if not dead:
        print("  无 ✓")

    print("\n=== ①-b 路径前缀辅助量（定义后拼接使用，未直接调用，不计死调用）===")
    for c in sorted(prefix_helpers):
        print(f"  {c}  ({', '.join(prefix_helpers[c][:3])})")
    if not prefix_helpers:
        print("  无 ✓")

    print("\n=== ② 后端路由但前端从不调用（候选死路由，仅供参考）===")
    for p in unused:
        print(f"  {p}")
    if not unused:
        print("  无 ✓")

    print("\n=== ③ 隐藏路由清单（live 但不在 OpenAPI 内）===")
    for p in hidden:
        mark = "前端在调" if hidden_called[p] else "前端未调"
        print(f"  {p}  [{mark}]")
    if not hidden:
        print("  无 ✓")

    print("\n=== ④ live 路由与 OpenAPI 不一致（除隐藏路由外）===")
    for p in result["divergence"]:
        print(f"  live 有、OpenAPI 无（非隐藏）: {p}")
    for p in result["stale_openapi"]:
        print(f"  OpenAPI 有、live 无（快照过期）: {p}")
    if not result["divergence"] and not result["stale_openapi"]:
        print("  无 ✓（live − OpenAPI 恰好等于隐藏路由集）")

    if dead or result["divergence"] or result["stale_openapi"]:
        print(
            f"\n审计发现漂移：死调用 {len(dead)} 条，"
            f"live/OpenAPI 不一致 {len(result['divergence']) + len(result['stale_openapi'])} 条"
        )
        return 1
    print("\n审计通过：无死调用，live 与 OpenAPI 除隐藏路由外一致 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
