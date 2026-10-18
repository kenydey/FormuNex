"""Server-side permission check for core R&D writes (开发方案 §4.4).

The R&D write endpoints (projects / formulations / experiments / DOE /
optimize / ingest / KB / QC / loop) previously had no per-user RBAC at all —
any authenticated caller could write. This guard is registered once as an
application-wide dependency (like ``admin_guard``) so new routes under these
prefixes are covered automatically.

Semantics (mirroring ``admin_guard``):
- Reads (GET / HEAD / OPTIONS) are never blocked here.
- A service API key (or the API with auth disabled) is an operator credential,
  not a person, and keeps working as before.
- A signed-in personal account needs the route's permission; a missing
  permission is a 403.
- Project scoping: ``/api/projects/{project_id}`` writes check the permission
  in that project (global roles pass; a project-scoped role only passes for
  its own project). Other routes have no project in the path, so they check
  the global permission.

``formulation.read_exact`` is NOT enforced here — it is a read-side redaction
(see ``api/formulations.py``), not a write gate.
"""
from __future__ import annotations

import re

from starlette.requests import HTTPConnection

from ._principal import _bearer, current_user

_WRITE = {"POST", "PUT", "PATCH", "DELETE"}

# (path pattern, permission). Order matters: specific rules before general ones.
_RULES: list[tuple[re.Pattern[str], str]] = [
    # Projects — project-scoped (id is the 3rd path segment).
    (re.compile(r"^/api/projects(/|$)"), "project.write"),
    # Formulations — compute endpoints need read; everything else is a write.
    (re.compile(r"^/api/formulations/(validate|recommend)$"), "formulation.read"),
    (re.compile(r"^/api/formulations(/|$)"), "formulation.write"),
    # Experiments — workbench (lab execution) vs model training (optimize).
    (re.compile(r"^/api/experiments/workbench(/|$)"), "lab.execute"),
    (re.compile(r"^/api/(experiments|train|models)(/|$)"), "optimize.run"),
    # DOE / optimize / loop.
    (re.compile(r"^/api/doe(/|$)"), "doe.write"),
    (re.compile(r"^/api/optimize(/|$)"), "optimize.run"),
    (re.compile(r"^/api/loop(/|$)"), "optimize.run"),
    # Ingest / KB — read-only compute endpoints need read; the rest is a write.
    (re.compile(r"^/api/ingest(/|$)"), "knowledge.write"),
    (re.compile(r"^/api/kb/(hybrid-search|query-test|golden-eval/run)$"), "knowledge.read"),
    (re.compile(r"^/api/kb(/|$)"), "knowledge.write"),
    # QC.
    (re.compile(r"^/api/qc(/|$)"), "qc.write"),
]


def required_permission(method: str, path: str) -> tuple[str | None, str | None]:
    """Return (permission, project_id) for a write, else (None, None)."""
    if method.upper() not in _WRITE:
        return None, None
    for pattern, permission in _RULES:
        if pattern.match(path):
            project_id = None
            parts = path.split("/")
            # /api/projects/{project_id}[...] → parts = ["", "api", "projects", id, ...]
            if len(parts) > 3 and parts[1] == "api" and parts[2] == "projects" and parts[3]:
                project_id = parts[3]
            return permission, project_id
    return None, None


def rbac_guard(request: HTTPConnection) -> None:
    # HTTPConnection (not Request): FastAPI only fills a ``Request``-typed
    # dependency param when the connection *is* a Request, so a WebSocket
    # route would call this with no args. HTTPConnection is the common base of
    # Request and WebSocket, and the guard only needs method / url / headers.
    # WebSocket has no .method: treat it as a read (never blocked here).
    method = getattr(request, "method", "GET")
    permission, project_id = required_permission(method, request.url.path)
    if permission is None:
        return
    from ..services.identity import tokens

    token = _bearer(request)
    if token and tokens.looks_like_access_token(token):
        current_user(request).require(permission, project_id)
