"""eLabFTW connector (REST API v2).

A lab task becomes an eLabFTW experiment whose ``metadata.extra_fields`` has
one number field per requested metric (unit, and the test method code in the
field description). Lab staff fill the fields in eLabFTW; ``pull_measurements``
reads the non-empty ones back. Authentication: the API key in the
``Authorization`` header. The URL is an administrator setting (usually an
intranet host), so the ordinary client is used rather than the public-only one.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from ...http_safe import make_client

from .base import ConnectorError, ElnConnector, RemoteRecord, RemoteValue


class ElabftwConnector(ElnConnector):
    name = "elabftw"

    def __init__(self, base_url: str, api_key: str, *, client_factory: Callable[[], httpx.Client] | None = None,
                 timeout: float = 15.0) -> None:
        if not base_url.startswith(("https://", "http://")):
            raise ConnectorError("eLabFTW URL 必须以 http(s):// 开头")
        if not api_key:
            raise ConnectorError("未配置 eLabFTW API Key")
        self.base = base_url.rstrip("/")
        self._headers = {"Authorization": api_key, "Accept": "application/json"}
        self._factory = client_factory or (lambda: make_client(timeout=timeout))

    def _url(self, path: str) -> str:
        return f"{self.base}/api/v2{path}"

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            with self._factory() as c:
                resp = c.request(method, self._url(path), headers=self._headers, **kw)
        except httpx.HTTPError as exc:
            raise ConnectorError(f"eLabFTW 不可达：{exc}") from exc
        if resp.status_code >= 400:
            raise ConnectorError(f"eLabFTW {method} {path} 返回 HTTP {resp.status_code}")
        return resp

    def health(self) -> dict[str, Any]:
        resp = self._call("GET", "/info")
        body = resp.json() if resp.content else {}
        return {"ok": True, "version": body.get("elabftw_version") or body.get("version")}

    def link(self, external_id: str) -> str:
        return f"{self.base}/experiments.php?mode=view&id={external_id}"

    def push_plan(self, plan: dict[str, Any]) -> RemoteRecord:
        resp = self._call("POST", "/experiments", json={
            "title": f"{plan['task_no']} · {plan.get('sample_no') or ''}".strip(" ·"),
            "tags": ["FormuNex", plan.get("doe_plan_id") or "manual"]})
        location = resp.headers.get("Location") or resp.headers.get("location") or ""
        ext_id = location.rstrip("/").rsplit("/", 1)[-1]
        if not ext_id.isdigit():
            raise ConnectorError("eLabFTW 未返回新实验的编号（Location 头）")
        factors = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in (plan.get("factors") or {}).items())
        extra = {}
        for pos, r in enumerate(plan.get("requested_metrics") or []):
            extra[r["metric"]] = {"type": "number", "value": "", "unit": r.get("unit", ""),
                                  "description": r.get("method_code", ""), "position": pos}
        self._call("PATCH", f"/experiments/{ext_id}", json={
            "body": f"<p>FormuNex 任务单 {plan['task_no']}，样品 {plan.get('sample_no')}</p>"
                    f"<table><tr><th>因子</th><th>设定</th></tr>{factors}</table>",
            "metadata": json.dumps({"extra_fields": extra}, ensure_ascii=False)})
        return RemoteRecord(ext_id, self.link(ext_id))

    def pull_measurements(self, external_id: str, *, since: str | None = None) -> list[RemoteValue]:
        # 补齐计划 2-15: eLabFTW extra_fields 无逐字段时间戳，since 无法在服务端过滤 —— 忽略，
        # 由 sync.py 的 content_hash 去重兜底。
        _ = since
        body = self._call("GET", f"/experiments/{external_id}").json()
        meta = body.get("metadata") or "{}"
        try:
            meta = json.loads(meta) if isinstance(meta, str) else meta
        except ValueError:
            raise ConnectorError(f"eLabFTW 实验 {external_id} 的 metadata 不是 JSON") from None
        out = []
        for metric, f in (meta.get("extra_fields") or {}).items():
            raw = str(f.get("value", "")).strip()
            if not raw:
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            out.append(RemoteValue(metric, value, f.get("unit", "") or "", f.get("description", "") or ""))
        return out
