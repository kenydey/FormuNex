"""Independent HTTP audit middleware (Phase 2D 2-25).

Records every state-changing API request (POST / PUT / PATCH / DELETE) into
the hash-chained ``audit_log`` — uniformly, without depending on each
service remembering to call ``hash_chain.append``. A request that bypasses
the service layer (or a service that simply forgets) is still audited.

Deliberate scope:
- Reads (GET / HEAD / OPTIONS) are NOT audited — the audit trail is a
  mutation log, not an access log.
- No request bodies, no query strings: bodies may carry passwords / tokens,
  and SSE passes ``?token=`` in the query string. Only method + path +
  status are recorded.
- Service-level semantic entries (``revision.winning``, ``release.submit``,
  …) are untouched and remain the primary record; middleware entries use the
  ``http.<METHOD>`` action prefix so the two layers are distinguishable.
- Fail-open: any audit failure is logged and swallowed; it must never break
  the request it audits.

The middleware is registered outermost (after CORS) so it also sees
requests rejected by auth (401) and rate limiting (429) — failed attempts
are audited with actor ``anonymous``.
"""
from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

_SKIP_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json", "/api/openapi.json", "/api/docs")
_SKIP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _should_audit(request: Request) -> bool:
    if request.method in _SKIP_METHODS:
        return False
    path = request.url.path
    if not path.startswith("/api/"):
        return False
    return not path.startswith(_SKIP_PREFIXES)


def _actor(request: Request) -> str:
    """Owner of the request's token, or ``anonymous`` when no token was presented."""
    if not request.headers.get("authorization"):
        return "anonymous"
    try:
        from .api_auth import get_current_owner

        return get_current_owner(request)
    except Exception:
        return "anonymous"


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        if not _should_audit(request):
            return response
        try:
            from ..db.database import default_session_factory
            from ..db.session_utils import commit_session
            from ..services.audit import hash_chain

            actor = _actor(request)
            with commit_session(default_session_factory()) as session:
                hash_chain.append(
                    session,
                    actor=actor,
                    action=f"http.{request.method}",
                    entity_type="http",
                    entity_id=request.url.path,
                    detail={
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": response.status_code,
                    },
                )
        except Exception:
            logger.warning("audit middleware failed (fail-open)", exc_info=True)
        return response
