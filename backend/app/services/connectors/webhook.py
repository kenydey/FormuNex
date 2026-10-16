"""Webhook publish connector: a signed JSON POST per release event.

The body carries the manifest (not the ZIP); the receiver fetches files through
the API if it needs them. ``X-FormuNex-Signature: sha256=<hex>`` is
HMAC-SHA256 of the raw body with the shared secret. Requests go through the
SSRF-safe client (public addresses only) and are retried on connection errors
and 5xx responses.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable

import httpx

from ...clock import utcnow
from .base import DeliveryPackage, PublishConnector, PublishResult

RETRY_DELAYS = (0.5, 2.0)


def signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _default_client() -> httpx.Client:
    from ..http_safe import ssrf_safe_client

    return ssrf_safe_client(timeout=10.0, follow_redirects=False)


class WebhookConnector(PublishConnector):
    name = "webhook"

    def __init__(self, url: str, secret: str = "", *, client_factory: Callable[[], httpx.Client] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if not url.startswith("https://") and not url.startswith("http://"):
            raise ValueError("webhook URL must be http(s)")
        self.url = url
        self.secret = secret
        self._client_factory = client_factory or _default_client
        self._sleep = sleep

    def _send(self, event: str, payload: dict) -> PublishResult:
        body = json.dumps({"event": event, "sent_at": utcnow().isoformat(), **payload},
                          ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        headers = {"Content-Type": "application/json", "X-FormuNex-Event": event,
                   "X-FormuNex-Delivery": str(uuid.uuid4())}
        if self.secret:
            headers["X-FormuNex-Signature"] = signature(self.secret, body)
        action = event.split(".")[-1]
        last = "not sent"
        status = None
        attempts = 0
        with self._client_factory() as client:
            for delay in (*RETRY_DELAYS, None):
                attempts += 1
                try:
                    resp = client.post(self.url, content=body, headers=headers)
                    status = resp.status_code
                    if resp.status_code < 300:
                        return PublishResult(self.name, action, True, f"HTTP {status}", attempts, status)
                    last = f"HTTP {status}"
                    if resp.status_code < 500:
                        break  # the receiver rejected it; retrying will not help
                except httpx.HTTPError as exc:
                    last = f"{type(exc).__name__}: {exc}"
                if delay is None:
                    break
                self._sleep(delay)
        return PublishResult(self.name, action, False, last, attempts, status)

    def publish(self, package: DeliveryPackage) -> PublishResult:
        return self._send("release.published", {
            "package_id": package.package_id, "product_code": package.product_code, "revision": package.revision,
            "package_hash": package.package_hash, "manifest": package.manifest})

    def withdraw(self, package: DeliveryPackage, reason: str) -> PublishResult:
        return self._send("release.withdrawn", {
            "package_id": package.package_id, "product_code": package.product_code, "revision": package.revision,
            "package_hash": package.package_hash, "reason": reason})
