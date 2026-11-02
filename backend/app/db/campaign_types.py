"""Workbench row types — decoupled from SQLAlchemy (rows embed in Campaign.sample_refs)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkbenchRow:
    """One AG-Grid execution row (``item_id`` is its stable local label)."""

    id: int
    campaign_id: int
    item_id: str
    status: str = "Pending"
    planned_params: dict[str, Any] = field(default_factory=dict)
    actual_params: dict[str, float] = field(default_factory=dict)
    measurements: dict[str, Any] = field(default_factory=dict)
    # ── Phase 2 additions ──
    note: str | None = None
    tags: list[str] = field(default_factory=list)
    parent_sample_id: str | None = None
    parent_campaign_id: int | None = None
    # P3: platform refcode (test:XXXX) — 版本历史 API 以 refcode 寻址
    refcode: str | None = None
