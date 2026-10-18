"""Server-side permission check for administrative writes (方案 §4.4).

The settings centre, dependency installer, skills, embedding models and connector management change the platform
for everyone (LLM keys, installed packages, MCP servers). A signed-in account needs ``admin.llm`` (models, keys,
flags, skills, dependencies) or ``admin.connectors`` (connectors) to change them; reads stay open so every page can
show masked status. A service API key (or the API with auth disabled in development) is an operator credential,
not a person, and keeps working as before.

Registered once as an application-wide dependency so new routes under these prefixes are covered automatically.
"""
from __future__ import annotations

import re

from starlette.requests import HTTPConnection

from ._principal import _bearer, current_user

_READ = {"GET", "HEAD", "OPTIONS"}

# (path pattern, permission, exempt patterns). Exempt: per-chat actions every user takes while working —
# searching a built-in connector, calling an approved MCP tool, approving a tool for their own chat session.
_RULES: list[tuple[re.Pattern[str], str, tuple[re.Pattern[str], ...]]] = [
    (re.compile(r"^/api/(settings|dependencies|embedding-models|skills)(/|$)"), "admin.llm", ()),
    (re.compile(r"^/api/connectors(/|$)"), "admin.connectors", (
        re.compile(r"^/api/connectors/builtin/[^/]+/search$"),
        re.compile(r"^/api/connectors/mcp/call$"),
        re.compile(r"^/api/connectors/mcp/[^/]+/approve-session$"),
    )),
]


def required_permission(method: str, path: str) -> str | None:
    if method.upper() in _READ:
        return None
    for pattern, permission, exempt in _RULES:
        if pattern.match(path):
            return None if any(e.match(path) for e in exempt) else permission
    return None


def admin_guard(request: HTTPConnection) -> None:
    # HTTPConnection (not Request): FastAPI only fills a ``Request``-typed
    # dependency param when the connection *is* a Request, so a WebSocket
    # route would call this with no args. HTTPConnection is the common base of
    # Request and WebSocket, and the guard only needs method / url / headers.
    # WebSocket has no .method: treat it as a read (never blocked here).
    method = getattr(request, "method", "GET")
    permission = required_permission(method, request.url.path)
    if permission is None:
        return
    from ..services.identity import tokens

    token = _bearer(request)
    if token and tokens.looks_like_access_token(token):
        current_user(request).require(permission)
