"""Cross-process SQLite write lock backed by Redis.

SQLite serializes writes at the database level anyway, but two processes
(uvicorn autosave + celery kb_ingest) can both try to write at once and one
loses the busy_timeout race with "database is locked". A Redis lock held across
the write transaction makes the two processes take turns instead of colliding.

Degrades to no-op (proceed unlocked) when Redis is unavailable, so a lost Redis
never takes down writes — it just falls back to busy_timeout + retry.
"""
from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Iterator

from ..services import redis_breaker

logger = logging.getLogger(__name__)

_LOCK_KEY = "formunex:sqlite_write"

_BREAKER_LOG_AT = 0.0


@contextlib.contextmanager
def sqlite_write_lock(
    redis_url: str | None, *, timeout: float = 30.0, blocking_timeout: float = 30.0
) -> Iterator[None]:
    """Hold a cross-process lock for the duration of a SQLite write transaction.

    Reduced default timeout/blocking to 30s to fail fast and surface contention
    rather than stalling callers for five minutes.
    """
    from ..redis_compat import client_kwargs

    if not redis_url:
        yield
        return
    if redis_breaker.is_open():
        # P3-A14: previously this whole branch was silent — every write
        # in the breaker window ran unlocked with no trace. Log at most
        # one line per breaker window (a timestamp race here can only
        # duplicate a log line, never lose the lock semantics).
        global _BREAKER_LOG_AT
        now = time.monotonic()
        if now - _BREAKER_LOG_AT >= redis_breaker.RETRY_AFTER_S:
            _BREAKER_LOG_AT = now
            logger.info(
                "Redis breaker open — SQLite writes proceeding unlocked "
                "(no cross-process lock for this window)"
            )
        yield
        return

    # Acquire outside the yield so an exception raised by the caller's body is
    # never swallowed by the Redis error handler.
    lock = None
    client = None

    def _close_client() -> None:
        # One client per write (the URL can change between writes): close it, or its connection lingers until GC.
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    try:
        import redis

        client = redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=redis_breaker.SOCKET_TIMEOUT_S,
            socket_connect_timeout=redis_breaker.CONNECT_TIMEOUT_S,
            **client_kwargs(),
        )
        lock = client.lock(_LOCK_KEY, timeout=timeout, blocking_timeout=blocking_timeout)
        acquired = lock.acquire(blocking=True, blocking_timeout=blocking_timeout)
    except Exception as exc:
        # Anything that stops us locking means "proceed unlocked"; only a failure to *reach* Redis (or an unknown error
        # from a half-built client) switches it off for everyone - a single slow reply does not.
        if not redis_breaker.is_slow_reply(exc):
            redis_breaker.trip()
        _close_client()
        logger.warning(
            "Redis write lock unavailable (%s) — proceeding unlocked%s",
            exc,
            f", not retrying for {redis_breaker.RETRY_AFTER_S:.0f} s" if redis_breaker.is_open() else "",
        )
        yield
        return

    if not acquired:
        _close_client()
        logger.warning(
            "SQLite write lock timeout after %.0fs — proceeding unlocked", blocking_timeout
        )
        yield
        return

    try:
        yield
    finally:
        try:
            lock.release()
        except Exception as exc:
            # P2-7: redis-py raises LockError when the lock token no
            # longer matches — i.e. the lock EXPIRED mid-transaction
            # (default 30 s) and another process may have written
            # concurrently. That used to be swallowed silently, hiding
            # exactly the mutual-exclusion loss this lock exists to
            # prevent. It still must not raise (release runs in a
            # finally around the caller's transaction), but it is now
            # a warning with the timeout that was exceeded. Callers
            # with known-long transactions should pass a larger
            # timeout instead of relying on the default.
            import redis as _redis

            if isinstance(exc, _redis.exceptions.LockError):
                logger.warning(
                    "SQLite write lock expired before release (timeout=%.0fs) — "
                    "the transaction ran unprotected for its tail",
                    timeout,
                )
            else:
                logger.debug("SQLite write lock release failed: %s", exc)
        _close_client()
