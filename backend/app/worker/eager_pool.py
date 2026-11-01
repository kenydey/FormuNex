"""Process-level bounded pool for eager-mode background work.

Stage 3 / SR-066 (3-13): eager mode used to spawn one unbounded daemon
thread per submission — ``_dispatch._submit_eager_background`` plus the
four ``dispatch_*`` eager branches in ``tasks.py``. A burst of uploads,
KB ingests, wiki compiles or screenings could therefore grow the thread
count without bound. All five sites now submit here instead.

The pool is deliberately zero-dependency (stdlib only): both the API
layer (``app.api._dispatch``) and the worker layer (``app.worker.tasks``)
import it, and importing either of those back would close a cycle.

Bounds: the *thread count* is bounded (``MAX_WORKERS``); the work queue
is not — a burst queues instead of spawning. That is the intended
trade-off: eager mode is the single-process deployment shape, and
queueing beats thread explosion there.

Exit semantics differ from the old daemon threads on purpose: pool
threads are joined at interpreter exit, so in-flight eager work
finishes instead of being abandoned mid-write. A stuck task can delay
exit; eager tasks are the same bounded jobs Celery workers run with
timeouts, so this is the safer default.

Thread names: pool threads are named ``eager_N``; :func:`submit` takes
an optional ``name`` that renames the *current* thread for the duration
of one task (restored afterwards), preserving the per-task log names
the old ``threading.Thread(name=...)`` calls provided.
"""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor

MAX_WORKERS = 8

_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None


def submit(fn, name: str | None = None, *args, **kwargs) -> Future:
    """Run ``fn(*args, **kwargs)`` on the shared bounded pool.

    Returns the future. Exceptions propagate to the future, never to
    the submitting thread — callers that need failure visibility must
    observe the future or, as the eager dispatch sites do, catch
    inside ``fn``.
    """
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="eager")
        pool = _pool

    if name is None:
        return pool.submit(fn, *args, **kwargs)

    def _named() -> object:
        thread = threading.current_thread()
        old_name = thread.name
        thread.name = name
        try:
            return fn(*args, **kwargs)
        finally:
            thread.name = old_name

    return pool.submit(_named)
