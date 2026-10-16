"""The ELN connector interface.

FormuNex is the system of record; an external ELN is a place where lab staff
may also work. A connector can push a lab task out (``push_task``) and read the
values recorded against it (``pull_measurements``). Values pulled in arrive as
*unreviewed* measurements and go through the same review as values typed into
FormuNex — the release gate never trusts an external record directly.
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
    def push_task(self, task: dict[str, Any]) -> RemoteRecord: ...

    @abstractmethod
    def pull_measurements(self, external_id: str) -> list[RemoteValue]: ...

    def link(self, external_id: str) -> str:
        """Human-facing URL of a remote record."""
        return ""
