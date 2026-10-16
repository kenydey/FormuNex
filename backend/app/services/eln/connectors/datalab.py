"""Datalab connector (read-only): values recorded in a Datalab training item's block.

Kept for installations that used Datalab as their ELN (方案 v1.1: demoted to a
connector). Pushing tasks to Datalab is not supported — migrate instead
(``scripts/migrate_datalab_to_native.py``).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from ...http_safe import make_client

from .base import ConnectorError, ElnConnector, RemoteRecord, RemoteValue

TRAINING_BLOCK = "formumind_training"


class DatalabConnector(ElnConnector):
    name = "datalab"
    can_push = False

    def __init__(self, base_url: str, *, headers: dict[str, str] | None = None,
                 client_factory: Callable[[], httpx.Client] | None = None, timeout: float = 15.0) -> None:
        self.base = base_url.rstrip("/")
        self._headers = headers or {}
        self._factory = client_factory or (lambda: make_client(timeout=timeout))

    def _get(self, path: str) -> dict[str, Any]:
        try:
            with self._factory() as c:
                resp = c.get(f"{self.base}{path}", headers=self._headers)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"Datalab 不可达：{exc}") from exc
        if resp.status_code >= 400:
            raise ConnectorError(f"Datalab GET {path} 返回 HTTP {resp.status_code}")
        return resp.json()

    def health(self) -> dict[str, Any]:
        self._get("/info/")
        return {"ok": True}

    def push_task(self, task: dict[str, Any]) -> RemoteRecord:
        raise ConnectorError("Datalab 连接器只读：请把数据迁移到 FormuNex（scripts/migrate_datalab_to_native.py）")

    def item(self, item_id: str) -> dict[str, Any]:
        body = self._get(f"/get-item-data/{item_id}")
        return body.get("item_data") or body

    def pull_measurements(self, external_id: str) -> list[RemoteValue]:
        data = (self.item(external_id).get("blocks_obj") or {}).get(TRAINING_BLOCK, {}).get("data") or {}
        out = []
        for metric, value in (data.get("measured") or {}).items():
            if isinstance(value, (int, float)):
                out.append(RemoteValue(metric, float(value)))
        return out
