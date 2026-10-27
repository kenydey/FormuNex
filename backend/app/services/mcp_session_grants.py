"""Session-level MCP writeish tool grants (not global permanent).

W0 (2026-10-27) semantics — the pre-W0 design stored ``expires=None`` for
``until_session_end=True`` grants, which never expired, survived restarts, had
no revoke path, and bound nothing to the granting user:

- Every grant now carries an owner (the granting login's user id, recorded by
  the API layer) and a finite expiry. ``until_session_end=True`` grants live
  ``SESSION_GRANT_TTL_S`` (24 h): this codebase has no session-end event —
  chat sessions are persistent database rows — so a bounded lifetime plus an
  explicit revoke endpoint is the fail-closed operationalisation of
  "session end". ``until_session_end=False`` keeps its 1 h expiry.
- Legacy on-disk entries (plain ``float | None``) are converted on load, and
  legacy permanent entries (``expires=None``) are dropped: the old bug's
  grants do not survive the upgrade.
- Expired entries are purged on first load after process start (the startup
  cleanup) and on every grant read/write.
- ``is_granted`` honours the owner binding when the caller supplies one; the
  chat execution path carries no identity (it runs through the identity-less
  chat endpoint), so there the session_id itself — an unguessable chat
  session id — remains the capability, and binding is enforced at
  approve / revoke time.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
# In-memory: session_id -> {server_id: {tool_name: grant}}
# grant = {"expires": float, "owner": str | None, "created": float}
_GRANTS: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}

SESSION_GRANT_TTL_S = 24 * 3600
SHORT_GRANT_TTL_S = 3600


def _persist_path() -> Path:
    return Path("./data").resolve() / "mcp_session_grants.json"


def _normalise_entry(raw: Any) -> dict[str, Any] | None:
    """Convert one stored entry to the W0 shape; None means drop it.

    Legacy permanent entries (``expires=None``) are dropped, not honoured:
    they are exactly the pre-W0 bug (a grant that never expired).
    """
    if isinstance(raw, dict):
        expires = raw.get("expires")
        if expires is None:
            return None
        try:
            expires_f = float(expires)
        except (TypeError, ValueError):
            return None
        return {
            "expires": expires_f,
            "owner": raw.get("owner"),
            "created": float(raw.get("created") or 0.0),
        }
    if isinstance(raw, (int, float)):
        # Pre-W0 shape: the expiry float itself. None was handled above by
        # the dict branch's absence; a bare number is a finite expiry.
        return {"expires": float(raw), "owner": None, "created": 0.0}
    return None


def _load_disk() -> None:
    path = _persist_path()
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            now = time.time()
            with _LOCK:
                for sid, servers in raw.items():
                    if not isinstance(servers, dict):
                        continue
                    for srv, tools in servers.items():
                        if not isinstance(tools, dict):
                            continue
                        for tool, entry in tools.items():
                            norm = _normalise_entry(entry)
                            if norm is None:
                                logger.warning("mcp session grant dropped on load (legacy permanent): %s/%s", sid, srv)
                                continue
                            if norm["expires"] <= now:
                                continue
                            _GRANTS.setdefault(sid, {}).setdefault(srv, {})[tool] = norm
    except Exception as exc:  # noqa: BLE001
        logger.debug("mcp grants load skipped: %s", exc)


def _save_disk() -> None:
    path = _persist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        path.write_text(json.dumps(_GRANTS, ensure_ascii=False, indent=2), encoding="utf-8")


_loaded = False


def _purge_expired_locked(now: float) -> int:
    dropped = 0
    for sid in list(_GRANTS):
        for srv in list(_GRANTS[sid]):
            for tool in list(_GRANTS[sid][srv]):
                if _GRANTS[sid][srv][tool]["expires"] <= now:
                    del _GRANTS[sid][srv][tool]
                    dropped += 1
            if not _GRANTS[sid][srv]:
                del _GRANTS[sid][srv]
        if not _GRANTS[sid]:
            del _GRANTS[sid]
    return dropped


def purge_expired() -> int:
    """Drop expired grants (startup cleanup; also runs on every access)."""
    _ensure_loaded()
    with _LOCK:
        dropped = _purge_expired_locked(time.time())
    if dropped:
        _save_disk()
    return dropped


def _ensure_loaded() -> None:
    global _loaded
    if not _loaded:
        _load_disk()
        # Startup cleanup: expired entries never come back, even if the file
        # still lists them (the file is rewritten on the next save).
        with _LOCK:
            _purge_expired_locked(time.time())
        _loaded = True


def approve_session(
    session_id: str,
    server_id: str,
    *,
    tool_name: str | None = None,
    until_session_end: bool = True,
    owner_user_id: str | None = None,
) -> dict[str, Any]:
    """Grant writeish tool(s) for this chat session, bound to the granting user."""
    _ensure_loaded()
    sid = (session_id or "").strip()
    srv = (server_id or "").strip()
    if not sid or not srv:
        raise ValueError("session_id and server_id required")
    tool = (tool_name or "*").strip() or "*"
    now = time.time()
    expires = now + (SESSION_GRANT_TTL_S if until_session_end else SHORT_GRANT_TTL_S)
    with _LOCK:
        bucket = _GRANTS.setdefault(sid, {})
        tools = bucket.setdefault(srv, {})
        tools[tool] = {"expires": expires, "owner": owner_user_id, "created": now}
    _save_disk()
    return {
        "ok": True,
        "session_id": sid,
        "server_id": srv,
        "tool_name": tool,
        "until_session_end": until_session_end,
    }


def is_granted(
    session_id: str,
    server_id: str,
    tool_name: str,
    *,
    owner_user_id: str | None = None,
) -> bool:
    _ensure_loaded()
    sid = (session_id or "").strip()
    srv = (server_id or "").strip()
    tool = (tool_name or "").strip()
    if not sid or not srv or not tool:
        return False
    with _LOCK:
        _purge_expired_locked(time.time())
        tools = (_GRANTS.get(sid) or {}).get(srv) or {}
        for key in (tool, "*"):
            entry = tools.get(key)
            if entry is None:
                continue
            if entry["expires"] <= time.time():
                continue
            owner = entry.get("owner")
            if owner is not None and owner_user_id is not None and owner != owner_user_id:
                continue
            return True
    return False


def revoke_session(
    session_id: str,
    server_id: str,
    *,
    tool_name: str | None = None,
    owner_user_id: str | None = None,
    is_admin: bool = False,
) -> dict[str, Any]:
    """Revoke a session's grant(s). Owner-or-admin only when a grant exists.

    Revoking a nonexistent grant is a no-op success (idempotent, and it
    leaks nothing about whether a grant existed). When a grant does exist
    and the caller is neither its owner nor an admin, the call is refused.
    """
    _ensure_loaded()
    sid = (session_id or "").strip()
    srv = (server_id or "").strip()
    if not sid or not srv:
        raise ValueError("session_id and server_id required")
    tool = (tool_name or "*").strip() or "*"
    with _LOCK:
        _purge_expired_locked(time.time())
        tools = (_GRANTS.get(sid) or {}).get(srv) or {}
        targets = [tool] if tool != "*" else list(tools)
        existing = [t for t in targets if t in tools]
        if existing:
            for t in existing:
                owner = tools[t].get("owner")
                if owner is not None and not is_admin and owner_user_id != owner:
                    return {"ok": False, "error": "not_authorized", "revoked": 0}
            for t in existing:
                del tools[t]
            if not tools:
                _GRANTS[sid].pop(srv, None)
            if not _GRANTS.get(sid):
                _GRANTS.pop(sid, None)
            revoked = len(existing)
        else:
            revoked = 0
    if revoked:
        _save_disk()
    return {"ok": True, "revoked": revoked}
