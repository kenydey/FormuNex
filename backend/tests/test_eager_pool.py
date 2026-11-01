"""Stage 3 / SR-066 (3-13): eager-mode background work runs on one bounded pool."""

from __future__ import annotations

import threading

from app.worker import eager_pool


def test_pool_bounds_concurrency():
    """16 blocking tasks on an 8-worker pool never exceed 8 concurrent."""
    started = threading.Event()
    release = threading.Event()
    active = 0
    peak = 0
    lock = threading.Lock()

    def _work():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        started.set()
        release.wait(timeout=10)
        with lock:
            active -= 1

    futures = [eager_pool.submit(_work) for _ in range(16)]
    assert started.wait(timeout=10)
    release.set()
    for fut in futures:
        fut.result(timeout=10)
    assert peak == eager_pool.MAX_WORKERS


def test_named_submit_renames_thread_for_task_duration():
    seen: list[str] = []

    def _work():
        seen.append(threading.current_thread().name)

    eager_pool.submit(_work, name="file-ingest").result(timeout=10)
    assert seen == ["file-ingest"]
    # The pool thread's name is restored afterwards (no leakage to the next task).
    eager_pool.submit(_work).result(timeout=10)
    assert seen[1].startswith("eager_")


def test_failed_task_does_not_kill_pool():
    def _boom():
        raise RuntimeError("boom")

    fut = eager_pool.submit(_boom)
    try:
        fut.result(timeout=10)
    except RuntimeError:
        pass
    else:  # pragma: no cover - the future must carry the failure
        raise AssertionError("expected RuntimeError")
    assert eager_pool.submit(lambda: 42).result(timeout=10) == 42
