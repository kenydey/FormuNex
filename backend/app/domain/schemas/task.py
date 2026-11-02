"""Async task DTOs: states, statuses, accepted-task handles."""
from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel



class TaskState(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"

class TaskStatus(BaseModel):
    task_id: str
    kind: str
    state: TaskState
    progress: float = 0.0
    message: str = ""
    result: dict[str, Any] | None = None
    stream_url: str = ""
    # P2：可观测
    stage: str = ""
    elapsed_ms: int | None = None
    owner_id: str | None = None

class AsyncTaskAccepted(BaseModel):
    """HTTP 202 response when a Celery task is enqueued."""

    task_id: str
    stream_url: str
    status_url: str
    outbox_id: str | None = None

TaskHandle = AsyncTaskAccepted
