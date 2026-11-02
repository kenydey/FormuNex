"""HTTP-free dispatch kernel: broker probe, id-first ordering, eager path.

This is the layering-correct home (stage 4 / SR-020) for what used to live
in ``app.api._dispatch``: everything about submitting a Celery task that
does not depend on HTTP. The thin HTTP adapter stays in ``app.api._dispatch``
and only maps :class:`DispatchUnavailable` to a 503 response with the
``X-FormuNex-Task-Id`` header. Services (workbench loop, OCSR availability)
import from here, never from the API layer, so a broker outage surfaces as
a domain exception in background code instead of an HTTP status.

Kept semantics (W1-10, unchanged by the move):

* **Preflight the broker.** A single ``PING`` with a one-second timeout tells
  us what 19 seconds of Celery retries would, without the wait or the
  exception that leaves the app's result consumer needing a restart.
* **Answer with a reason, not a bare failure.** The durable outbox row is
  written before dispatch, so a failed submission is recorded work, not
  lost work — ``dispatcher.recover_stalled`` re-enqueues it once the broker
  is back. The kernel raises :class:`DispatchUnavailable` carrying the
  detail string and the fixed task id; the adapter turns that into a 503.

**Eager mode (2026-09-06 E2E audit P0-1):** with ``FORMUNEX_CELERY_EAGER=true``,
``task.delay()`` runs the *entire* job inline. Wrapping that in a 10s producer
timeout produced false failures (optimize ~15s, loop ~37s) while the work
still finished later, and the error text wrongly blamed Redis. Eager
submissions therefore start a thread on the bounded pool and return
immediately — same contract as ``workbench_loop`` / KB ingest.
"""
from __future__ import annotations

import socket
import uuid
from urllib.parse import urlparse

import logging

logger = logging.getLogger(__name__)

BROKER_PROBE_TIMEOUT_S = 1.0

BROKER_DOWN_DETAIL = (
    "任务队列（Redis）当前不可达，无法提交后台任务。请确认 redis 与 celery "
    "worker 已启动（docker compose ps），服务恢复后本次提交会自动重新入队。"
)

# Producer publish / first-connection stall — not the same as broker down.
DISPATCH_TIMEOUT_DETAIL = (
    "任务提交超时：Celery producer 在时限内未完成入队。请检查 worker 负载"
    "或重启 API 进程；这不代表消息队列一定不可达。"
)

# Outbox record failure — the row is what makes a 503 recoverable, so a
# dispatch whose id never landed is not a dispatch at all (SR-088).
OUTBOX_RECORD_DETAIL = (
    "任务提交失败：任务台账写入失败，提交的任务号无法用于恢复查询。"
    "请检查数据库后重试，不要重复提交。"
)

# Non-timeout failure after a green probe — may be serialisation or a flaky
# broker; do not claim Redis is definitively down (ELN/lab errors use their own copy).
DISPATCH_FAILURE_DETAIL = (
    "任务提交失败：Celery 在入队阶段异常。请查看 API 日志确认原因；"
    "若为台账/ELN 相关错误，请检查 Datalab 而非 Redis。"
)


def _broker_endpoint() -> tuple[str, int] | None:
    """``(host, port)`` for the configured broker, or None if not a TCP URL."""
    from ..config import get_settings

    url = urlparse(get_settings().redis_url)
    if not url.hostname:
        return None
    return url.hostname, url.port or 6379


# Stage 1 / SR-029-SR-068: process-local broker probe failure count
# (same convention as the ops counters: resets on process restart).
# Incremented on every failed probe, read by /api/ops/metrics.
_broker_probe_failures: int = 0


def broker_probe_failure_count() -> int:
    return _broker_probe_failures


def _bump_broker_probe_failure() -> None:
    global _broker_probe_failures
    _broker_probe_failures += 1


def _redis_ping(url: str) -> bool:
    """Speak one Redis command, not just TCP.

    W1-10 ④: a socket connect cannot tell a working broker from a dead one —
    a Redis too old for RESP3 ``HELLO`` accepts the TCP connection and then
    loops on ``unknown command``, while the old probe reported it reachable.
    ``PING`` answers the real question (can this broker serve a command?) in
    one round trip, and the protocol comes from ``redis_compat`` (RESP2 by
    default), so the probe itself never trips the old-server trap.
    """
    try:
        import redis
    except ImportError:
        logger.warning("broker probe: redis-py missing, falling back to TCP connect")
        endpoint = _broker_endpoint()
        if endpoint is None:
            return True
        try:
            with socket.create_connection(endpoint, timeout=BROKER_PROBE_TIMEOUT_S):
                return True
        except OSError as exc:
            logger.warning("broker unreachable at %s:%s (%s)", *endpoint, exc)
            _bump_broker_probe_failure()
            return False
    try:
        from ..redis_compat import client_kwargs

        client = redis.Redis.from_url(
            url, socket_connect_timeout=BROKER_PROBE_TIMEOUT_S,
            socket_timeout=BROKER_PROBE_TIMEOUT_S, **client_kwargs())
        try:
            ok = bool(client.ping())
        finally:
            client.close()
        if not ok:
            _bump_broker_probe_failure()
        return ok
    except Exception as exc:
        logger.warning("broker ping failed for %s (%s)", urlparse(url).hostname, exc)
        _bump_broker_probe_failure()
        return False


def broker_reachable() -> bool:
    """Whether a task submitted now would reach a broker.

    Eager mode runs tasks in-process and needs no broker at all, so it is
    always reachable. A unix-socket or otherwise unparseable URL is reported
    reachable too: the probe cannot speak for it, and refusing to submit on
    the strength of a check that did not run would be worse than letting
    Celery try. A ``redis://`` / ``rediss://`` URL gets a real ``PING``;
    any other TCP URL keeps the old connect probe.
    """
    from ..config import get_settings

    if get_settings().celery_eager:
        return True
    scheme = urlparse(get_settings().redis_url).scheme
    if scheme in ("redis", "rediss"):
        return _redis_ping(get_settings().redis_url)
    endpoint = _broker_endpoint()
    if endpoint is None:
        return True
    try:
        with socket.create_connection(endpoint, timeout=BROKER_PROBE_TIMEOUT_S):
            return True
    except OSError as exc:
        logger.warning("broker unreachable at %s:%s (%s)", *endpoint, exc)
        _bump_broker_probe_failure()
        return False


def _record_outbox_task_id(outbox_id: str | None, task_id: str) -> bool:
    """Remember which Celery id the client holds, so recovery can replay under it."""
    from ..db.outbox_store import record_task_id

    return record_task_id(outbox_id, task_id)


class DispatchUnavailable(Exception):
    """Broker unreachable, or a (non-eager) producer publish timed out / failed.

    Carries the user-facing detail string and the fixed task id so the
    HTTP adapter can map it to a 503 with the ``X-FormuNex-Task-Id`` header,
    and so service callers can log the id without understanding HTTP.
    """

    def __init__(self, detail: str, task_id: str):
        super().__init__(detail)
        self.detail = detail
        self.task_id = task_id


def dispatch_task_kernel(
    task,
    payload: dict,
    kind: str,
    *,
    outbox_id: str | None = None,
    task_id: str | None = None,
) -> str:
    """Dispatch ``task`` with ``payload``; return the Celery task id.

    The same dispatch the HTTP ``submit`` performs (broker probe, P2-1
    id-first ordering, eager pool thread, failure mapping), minus the HTTP
    response — for service callers such as the workbench loop (W1-10 ②)
    that need the id itself for SSE tracking instead of a 202 body. A caller that fixes
    ``task_id`` (the loop does, so it can register with the task manager
    before the eager thread starts) gets that id published and recorded.

    Raises:
        DispatchUnavailable: when the broker is unreachable or (non-eager)
            producer publish times out / fails. The outbox row (if one was
            written) stays PENDING and is recovered by
            ``dispatcher.recover_stalled``. The HTTP adapter maps this to a
            503 carrying the task id in the ``X-FormuNex-Task-Id`` header.

    W1-10 ① claim-by-id: a thread running a blocked publish cannot be
    cancelled in Python, so a publish that lands after the producer timeout
    is unavoidable in the residual hung-socket case — but it must never be
    *unclaimable*. The task id is therefore fixed first, recorded on the
    outbox row before the probe runs, and carried on every 503 in the
    ``X-FormuNex-Task-Id`` header: a client that gets a 503 polls that id
    instead of blindly resubmitting (which would duplicate the work if the
    timed-out publish did land). The row carries the same id, so startup
    recovery replays under exactly the id the client holds. Stage 5 /
    SR-088 hardens the gate: if the record itself fails, the dispatch
    fails here instead of publishing an id recovery cannot replay.
    Claim-by-id is about traceability, not deduplication: execution stays
    at-least-once (D-4, stage 1 / SR-015-SR-016), and a late publish plus
    a replay can still run the payload twice. ``detail``
    stays a plain string — the frontend parses error bodies as JSON with a
    string detail, so the id travels in a header, not in the body.
    """
    from ..config import get_settings

    # The id is the source of truth, fixed before anything else: it is
    # recorded on the outbox row first (so even a probe failure leaves a
    # recoverable row under the id the 503 header will claim), then
    # published under exactly that id.
    task_id = task_id or str(uuid.uuid4())
    if outbox_id and not _record_outbox_task_id(outbox_id, task_id):
        # Stage 5 / SR-088: a 503 whose id never landed on the row is
        # unrecoverable — the client would poll an id recovery cannot
        # replay under. Fail the dispatch here, before the probe, instead
        # of warning and publishing an orphan id.
        logger.error(
            "outbox %s: task id %s not recorded; failing dispatch",
            outbox_id,
            task_id,
        )
        raise DispatchUnavailable(OUTBOX_RECORD_DETAIL, task_id)
    if not broker_reachable():
        raise DispatchUnavailable(BROKER_DOWN_DETAIL, task_id)

    settings = get_settings()
    if settings.celery_eager:
        # Register PENDING *before* starting the daemon thread so a fast
        # RUNNING progress write cannot be overwritten by a late register_pending.
        _submit_eager_background(task, payload, kind, task_id=task_id)
        return task_id

    try:
        _delay_with_timeout(task, payload, task_id=task_id)
    except DispatchTimeout as exc:
        # 2026-09-05: uvicorn 进程内 celery producer 首建曾无限卡(所有 submit
        # 端点挂起→前端 502)。预热(lifespan)已把首建移出请求路径; 此处兜底
        # 超时, 宁可明确 503 也不让请求无限挂。
        # 2026-09-06: 文案与 broker-down 拆开，避免把 producer 超时误报成 Redis 不可达。
        # W1-10 ①: the abandoned thread may still publish under this id —
        # the header + the row above are what make that outcome claimable
        # instead of a silent duplicate.
        logger.error("celery dispatch timed out for %s (task_id=%s): %s", kind, task_id, exc)
        raise DispatchUnavailable(
            f"{DISPATCH_TIMEOUT_DETAIL}（{exc}）", task_id) from exc
    except Exception as exc:
        # The probe passed a moment ago, so this is a broker that died mid-
        # submission or a payload Celery could not serialise. Do not always
        # claim "Redis is down" — that mislabels ELN/serialisation failures.
        # The row already carries task_id (recorded above), so recovery
        # replays under the id the client would have held.
        logger.exception("celery dispatch failed for %s (task_id=%s)", kind, task_id)
        raise DispatchUnavailable(
            f"{DISPATCH_FAILURE_DETAIL}（{exc}）", task_id) from exc
    return task_id


def _submit_eager_background(
    task, payload: dict, kind: str, *, task_id: str | None = None
) -> str:
    """Run the Celery task body on the shared bounded eager pool; return a client task_id now.

    Under ``task_always_eager``, ``task.delay()`` blocks until the job finishes.
    HTTP handlers must not wait on that — return 202 and let SSE/status poll
    observe progress the same way a real worker would.

    Caller should register the task id (PENDING) before invoking this so a
    fast thread cannot race ahead of registration.
    """
    from ..worker.eager_pool import submit as _eager_submit

    task_id = task_id or str(uuid.uuid4())

    def _run() -> None:
        try:
            # apply(..., task_id=...) sets self.request.id so progress/publish
            # paths that key off the Celery request id keep working.
            task.apply(args=(payload,), task_id=task_id)
        except Exception:
            logger.exception("eager background task failed kind=%s id=%s", kind, task_id)

    _eager_submit(_run, name=f"eager-{kind}-{task_id[:8]}")
    return task_id


class DispatchTimeout(Exception):
    """Producer publish exceeded its timeout (stage 4 / SR-054 public API).

    Raised by the dispatch kernel; the HTTP adapter maps it to a 503
    carrying the task id. Tests construct it from the adapter module.
    """


def _delay_with_timeout(task, payload: dict, task_id: str | None = None, timeout_s: float = 10.0):
    """Publish in a worker thread with a hard cap (non-eager only).

    Celery's producer pool can block for minutes on first use inside a
    long-lived uvicorn process; the request must never hang on it.

    When *task_id* is given the publish uses ``apply_async`` with exactly
    that id (P2-1: the id recorded on the outbox row before publishing);
    without it the call falls back to ``task.delay`` and Celery assigns
    the id, as before.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        if task_id is not None:
            fut = pool.submit(task.apply_async, args=(payload,), task_id=task_id)
        else:
            fut = pool.submit(task.delay, payload)
        try:
            return fut.result(timeout=timeout_s)
        except FuturesTimeout:
            raise DispatchTimeout("task.delay > {}s".format(timeout_s)) from None
    finally:
        pool.shutdown(wait=False)  # 卡死线程不等待(超时兜底必须立即返回)
