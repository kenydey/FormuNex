"""Bearer API token authentication — enabled by default for public deployments."""
from __future__ import annotations

import dataclasses
import logging
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from ..config import Settings, api_tokens_json, get_settings, multi_user_enabled

logger = logging.getLogger(__name__)

_TOKEN_PATH = Path("data/.api_token")
_DEV_TOKEN_CACHE: str | None = None

# Paths reachable without a token (health probes, OpenAPI docs).
_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/auth/status",
    # Account sign-in: the credential is the password (login) or the httpOnly refresh cookie.
    "/api/identity/login",
    "/api/identity/refresh",
    "/api/identity/logout",
)
# Public paths matched exactly only (sub-paths still require auth), so that
# /health/detailed (which exposes infra details) stays behind auth.
_PUBLIC_EXACT: tuple[str, ...] = (
    "/health",
)


def _is_public_path(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _PUBLIC_PREFIXES)


def resolve_api_token(settings: Settings) -> str | None:
    """Return the active API token, or None when auth is disabled."""
    global _DEV_TOKEN_CACHE
    if not settings.api_auth_enabled:
        return None
    # UI 设置的 token 走运行时 overlay（立即生效，无需重启）；未通过 UI 设置时回落到 env / Settings。
    from ..services.runtime_secrets import effective_setting

    configured = effective_setting(settings, "api_token")
    if configured:
        return str(configured).strip()
    env = settings.environment.strip().lower()
    if env in ("production", "prod"):
        raise RuntimeError(
            "FORMUNEX_API_TOKEN is required when FORMUNEX_API_AUTH_ENABLED=true in production"
        )
    if _DEV_TOKEN_CACHE:
        return _DEV_TOKEN_CACHE
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic create-with-O_EXCL avoids the race where two workers both write a
    # new token and clobber each other. If the file already exists, reuse it.
    token = secrets.token_urlsafe(32)
    try:
        fd = os.open(
            str(_TOKEN_PATH),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            os.write(fd, (token + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(_TOKEN_PATH, 0o600)
        _DEV_TOKEN_CACHE = token
        logger.warning(
            "API auth: generated development token at %s — set FORMUNEX_API_TOKEN to override",
            _TOKEN_PATH,
        )
        return token
    except FileExistsError:
        existing = _TOKEN_PATH.read_text(encoding="utf-8").strip()
        if existing:
            os.chmod(_TOKEN_PATH, 0o600)
            _DEV_TOKEN_CACHE = existing
            logger.warning(
                "API auth: using dev token from %s (set FORMUNEX_API_TOKEN for a stable secret)",
                _TOKEN_PATH,
            )
            return existing
        # Empty file from a failed/partial write: remove and regenerate once.
        try:
            _TOKEN_PATH.unlink(missing_ok=True)
        except OSError as exc:
            logger.error("API auth: cannot clear empty token file %s: %s", _TOKEN_PATH, exc)
            return None
        logger.warning(
            "API auth: empty token file at %s — regenerating",
            _TOKEN_PATH,
        )
        try:
            fd = os.open(
                str(_TOKEN_PATH),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            try:
                os.write(fd, (token + "\n").encode("utf-8"))
            finally:
                os.close(fd)
            os.chmod(_TOKEN_PATH, 0o600)
            _DEV_TOKEN_CACHE = token
            return token
        except FileExistsError:
            # Another worker won the recreate race — reuse whatever they wrote.
            existing = _TOKEN_PATH.read_text(encoding="utf-8").strip()
            if existing:
                _DEV_TOKEN_CACHE = existing
                return existing
            return None


def reset_dev_token_cache() -> None:
    """Test helper — clear cached auto-generated dev token."""
    global _DEV_TOKEN_CACHE
    _DEV_TOKEN_CACHE = None


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _extract_token(request: Request) -> str | None:
    """Bearer token from the Authorization header, nothing else.

    Stage 5 / SR-081 removed the old query-token branch: a bearer token in
    a URL lands in browser history, proxy logs and Referer headers. Task
    streams for header-less clients (native EventSource) now use the
    single-use tickets from :func:`mint_task_ticket` instead, accepted by
    the middleware gate — not by this extractor, so no other code path
    can mistake a ticket for a bearer token.
    """
    return _extract_bearer(request)


def _safe_eq(a: str, b: str) -> bool:
    """Constant-time equality for tokens. Bytes, not str: ``compare_digest`` raises TypeError on a non-ASCII str, which
    an unauthenticated caller can send (``Authorization: Bearer é``) and turn into a 500."""
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _owner_tokens() -> dict[str, str]:
    """``FORMUNEX_API_TOKENS_JSON`` as ``{owner: token}`` (empty when unset or malformed).

    Only this direction is accepted: the keys are owner *names*, which are not secrets (they appear in responses and
    logs), so a key must never be usable as a credential.
    """
    raw = api_tokens_json()
    if not raw:
        return {}
    try:
        import json

        mapping = json.loads(raw)
    except ValueError:
        logger.warning("FORMUNEX_API_TOKENS_JSON is not valid JSON; ignoring it")
        return {}
    if not isinstance(mapping, dict):
        return {}
    return {str(owner): tok for owner, tok in mapping.items() if isinstance(tok, str) and tok}


def _owner_of_token(provided: str) -> str | None:
    """The owner whose token is *provided*, or None. Checks every entry so timing does not reveal which one matched."""
    found: str | None = None
    for owner, tok in _owner_tokens().items():
        if _safe_eq(provided, tok):
            found = owner
    return found


def _is_valid_access_token(provided: str) -> bool:
    from ..services.identity import tokens

    if not tokens.looks_like_access_token(provided):
        return False
    try:
        tokens.decode_access_token(provided)
    except (tokens.InvalidToken, tokens.SigningKeyMissing):
        return False
    return True


# ── Stage 5 / SR-081: one-time task-stream tickets ─────────────────────────
# A ticket is minted by an already-authenticated caller (POST
# /api/tasks/{id}/ticket) and lets a header-less client open that one task's
# SSE stream exactly once, within a short window. Unlike the bearer token it
# replaces in URLs, a leaked ticket is useless after first use, is bound to
# one task id, and carries no login identity beyond the owner recorded here.
_TASK_TICKET_TTL_S = 300.0


@dataclasses.dataclass
class _TaskTicket:
    task_id: str
    owner: str
    expires_at: float  # monotonic clock


_TICKETS: dict[str, _TaskTicket] = {}
_TICKET_LOCK = threading.Lock()


def mint_task_ticket(task_id: str, owner: str, *, ttl_s: float = _TASK_TICKET_TTL_S) -> str:
    """Mint a single-use stream ticket for *task_id*, owned by *owner*."""
    ticket = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _TICKET_LOCK:
        for key in [k for k, v in _TICKETS.items() if v.expires_at <= now]:
            del _TICKETS[key]
        _TICKETS[ticket] = _TaskTicket(task_id=task_id, owner=owner, expires_at=now + ttl_s)
    return ticket


def consume_task_ticket(ticket: str, task_id: str) -> str | None:
    """Consume *ticket*: return its owner iff live and bound to *task_id*.

    Consumption is unconditional — a wrong-task or expired guess burns the
    ticket, so a stolen ticket cannot be probed against other tasks.
    """
    with _TICKET_LOCK:
        rec = _TICKETS.pop(ticket, None)
    if rec is None or rec.task_id != task_id or rec.expires_at <= time.monotonic():
        return None
    return rec.owner


def _accept_task_ticket(request: Request) -> str | None:
    """Owner for a valid stream ticket on this request, else None."""
    if request.method != "GET":
        return None
    parts = request.url.path.split("/")
    if len(parts) != 5 or parts[1] != "api" or parts[2] != "tasks" or parts[4] != "stream":
        return None
    ticket = request.query_params.get("ticket")
    if not ticket:
        return None
    return consume_task_ticket(ticket.strip(), parts[3])


class ApiAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # CORS preflight must reach CORSMiddleware (outer layer); never 401.
        if request.method == "OPTIONS":
            return await call_next(request)
        settings = get_settings()
        if not settings.api_auth_enabled or _is_public_path(request.url.path):
            return await call_next(request)
        ticket_owner = _accept_task_ticket(request)
        if ticket_owner is not None:
            request.state.task_ticket_owner = ticket_owner
            return await call_next(request)
        provided = _extract_token(request)
        # Signed-in user (方案 §4.4): a valid access token passes; revocation and permissions are
        # checked by the endpoint's principal dependency, which has a database session.
        if provided and _is_valid_access_token(provided):
            return await call_next(request)
        # Phase 2 多用户：JSON 映射中的任意 owner token
        if multi_user_enabled() and provided and _owner_of_token(provided) is not None:
            return await call_next(request)
        token = resolve_api_token(settings)
        # Auth is enabled (checked above). A missing token must never open the
        # gate — empty-file / misconfig used to return None and silently pass.
        if token is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "API auth enabled but no token is configured"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not provided or not _safe_eq(provided, token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API token"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


def install_api_auth(app) -> None:
    app.add_middleware(ApiAuthMiddleware)


def get_current_owner(request: Request) -> str:
    """Phase 1 预埋：单 token 模式恒返回 ``default``，多用户时解析 token→owner.

    - 当 ``FORMUNEX_MULTI_USER != true``：恒 ``default``，所有校验 no-op。
    - 多用户：``FORMUNEX_API_TOKENS_JSON='{"alice": "tok1", ...}'`` 把 token 映射成 owner。
      未配置映射时（仅开发联调），owner 取 token 的 SHA-256 前缀 —— 从不取 token 本身的任何字符，
      owner 会写进数据库、响应和日志。映射已配置但 token 不在其中（鉴权关闭时才会走到这里）视为 ``default``。
    """
    if not multi_user_enabled():
        return "default"
    provided = _extract_token(request)
    if not provided:
        return "default"
    if _owner_tokens():
        return _owner_of_token(provided) or "default"
    import hashlib

    return "u-" + hashlib.sha256(provided.encode("utf-8")).hexdigest()[:12]


def request_owner(request: Request) -> str:
    """Stage 2 / SR-014 (D-3): task-result ownership prefers the JWT login id.

    ``get_current_owner`` falls back to ``"default"`` in single-user
    mode, so every task snapshot was owned by ``"default"`` and
    ``assert_owner`` was a no-op even for signed-in users. When the
    request carries a valid JWT access token, the owner is that token's
    user id (revocation is enforced: a revoked/expired token falls back
    to the mapping below instead of attributing to its user). Service
    keys and multi-user token mappings keep the ``get_current_owner``
    behaviour, and legacy ``"default"`` / owner-less rows keep passing
    ``assert_owner`` unchanged.
    """
    from ..services.identity import tokens

    ticket_owner = getattr(request.state, "task_ticket_owner", None)
    if ticket_owner:
        return ticket_owner
    provided = _extract_token(request)
    if provided and tokens.looks_like_access_token(provided):
        try:
            from ..db.database import default_session_factory
            from ..services.identity import accounts

            with default_session_factory()() as session:
                user, _sid = accounts.active_user_for_token(session, provided)
            return user.id
        except Exception:  # noqa: BLE001 — fall back to the token mapping
            pass
    return get_current_owner(request)


def assert_owner(resource_owner: str | None, current_owner: str) -> None:
    """Phase 1 软校验：单 token (current=default) 或资源无 owner 时恒过。

    Phase 2 强校验：当 ``FORMUNEX_MULTI_USER=true`` 且资源有 owner 时，
    owner 不一致则 403。当前 Phase 1 仅 debug 日志，不阻断。

    Stage 2 / SR-014: a resource owned by ``"default"`` counts as
    owner-less — those rows were written before JWT ownership existed
    (single-user mode), and signed-in users must keep reaching them
    exactly as the pre-stage-2 list/get endpoints served them to anyone.
    """
    if current_owner == "default" or not resource_owner or resource_owner == "default":
        logger.debug("owner check pass (soft): resource=%s current=%s", resource_owner, current_owner)
        return
    if resource_owner != current_owner:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Forbidden: resource not owned by caller")
