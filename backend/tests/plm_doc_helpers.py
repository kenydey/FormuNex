"""Shared helper for the async document-generation route (stage 3 / SR-026).

POST /api/plm/revisions/.../documents returns 202 + task_id now; these
helpers post, poll GET /api/tasks/{id} to a terminal state, and return
the task (or its result on success) so converted tests keep reading the
same response shape they asserted on the old sync 201 body.
"""
from __future__ import annotations

import time


def gen_doc(client, rid, body, headers=None, timeout=120):
    r = client.post(f"/api/plm/revisions/{rid}/documents", json=body, headers=headers)
    assert r.status_code == 202, r.text
    task_id = r.json()["task_id"]
    deadline = time.monotonic() + timeout
    task: dict = {}
    while time.monotonic() < deadline:
        task = client.get(f"/api/tasks/{task_id}", headers=headers).json()
        if task.get("state") in ("completed", "failed"):
            return task
        time.sleep(0.1)
    return task


def gen_doc_ok(client, rid, body, headers=None, timeout=120):
    task = gen_doc(client, rid, body, headers=headers, timeout=timeout)
    assert task.get("state") == "completed", task
    return task["result"]
