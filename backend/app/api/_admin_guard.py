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

from fastapi import Request

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


def admin_guard(request: Request) -> None:
    permission = required_permission(request.method, request.url.path)
    if permission is None:
        return
    from ..services.identity import tokens

    token = _bearer(request)
    if token and tokens.looks_like_access_token(token):
        current_user(request).require(permission)
