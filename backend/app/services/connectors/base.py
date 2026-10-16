"""The publish-connector interface.

A connector receives a released package (manifest + delivery ZIP) and pushes it
somewhere — a webhook today; ERP, the document management system and the
company website are reserved (plan item 16: interface only this phase).
Connectors never block a release: their outcome is recorded on the package and
can be retried.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DeliveryPackage:
    package_id: str
    product_code: str
    revision: str
    package_hash: str
    manifest: dict[str, Any]
    zip_bytes: bytes | None = None


@dataclass
class PublishResult:
    connector: str
    action: str  # publish | withdraw
    ok: bool
    detail: str = ""
    attempts: int = 1
    response_status: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PublishConnector(ABC):
    name: str = "base"

    @abstractmethod
    def publish(self, package: DeliveryPackage) -> PublishResult: ...

    @abstractmethod
    def withdraw(self, package: DeliveryPackage, reason: str) -> PublishResult: ...


# Reserved integrations: listed so the settings screen can show them; not implemented this phase.
RESERVED_CONNECTORS: dict[str, str] = {
    "erp": "ERP 物料 / 产品主数据同步（预留）",
    "document_system": "企业文档管理系统归档（预留）",
    "website": "官网 SDS / TDS 下载中心发布（预留）",
}


def configured_connectors() -> list[PublishConnector]:
    from ...config import get_settings
    from .webhook import WebhookConnector

    s = get_settings()
    out: list[PublishConnector] = []
    if s.plm_publish_webhook_url:
        out.append(WebhookConnector(s.plm_publish_webhook_url, s.plm_publish_webhook_secret))
    return out
