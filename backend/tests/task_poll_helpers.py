"""Poll GET /api/tasks/{id} to a terminal state (stage 3 async routes).

Shared by the converted 202 + poll tests: document generation, wiki
lint, URL/task/evidence ingest. Eager mode runs the task in a daemon
thread, so the poll observes completion the same way a real worker
would.
"""
from __future__ import annotations

import time


def wait_task(client, task_id: str, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    task: dict = {}
    while time.monotonic() < deadline:
        task = client.get(f"/api/tasks/{task_id}").json()
        if task.get("state") in ("completed", "failed"):
            return task
        time.sleep(0.1)
    return task
