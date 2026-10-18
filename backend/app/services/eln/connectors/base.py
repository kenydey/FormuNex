"""The ELN connector interface.

FormuNex is the system of record; an external ELN is a place where lab staff
may also work. A connector can push a lab task out (``push_plan``) and read the
values recorded against it (``pull_measurements``). Values pulled in arrive as
*unreviewed* measurements and go through the same review as values typed into
FormuNex — the release gate never trusts an external record directly.

补齐计划 2-15 签名对齐：
- 规范推送方法为 ``push_plan(plan)``（plan 即任务单 dict，与旧 push_task 同形）；
  ``push_task`` 保留为 deprecated 别名（调用方兼容）。子类侧破坏性说明：子类须实现
  ``push_plan``；仅实现旧 ``push_task`` 的外部子类实例化时会因缺少抽象方法而
  TypeError（明示失败，不静默错行）。
- ``pull_measurements(external_id, *, since=None)``：``since`` 为 ISO 时间戳，
  只拉取该时间之后变更的值。服务端无法按时间过滤的连接器忽略它（须在 docstring
  注明），由 sync.py 的 content_hash 去重兜底；旧调用（无 since）保持可用。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ConnectorError(RuntimeError):
    pass


@dataclass
class RemoteRecord:
    external_id: str
    url: str = ""


@dataclass
class RemoteValue:
    metric: str
    value: float
    unit: str = ""
    method_code: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class ElnConnector(ABC):
    name: str = "base"
    can_push: bool = True

    @abstractmethod
    def health(self) -> dict[str, Any]: ...

    @abstractmethod
    def push_plan(self, plan: dict[str, Any]) -> RemoteRecord:
        """Push a lab task (plan dict) to the ELN. Canonical push method (2-15)."""

    def push_task(self, task: dict[str, Any]) -> RemoteRecord:
        """Deprecated alias of :meth:`push_plan` (2-15, caller-compatible)."""
        import warnings

        warnings.warn("push_task is deprecated, use push_plan", DeprecationWarning, stacklevel=2)
        return self.push_plan(task)

    @abstractmethod
    def pull_measurements(self, external_id: str, *, since: str | None = None) -> list[RemoteValue]:
        """Pull values recorded against ``external_id``.

        ``since`` (ISO timestamp, optional): only values changed after it.
        Connectors whose backend cannot filter by time ignore it and return
        everything (sync.py dedupes via content_hash); document that in the override.
        """

    def link(self, external_id: str) -> str:
        """Human-facing URL of a remote record."""
        return ""
