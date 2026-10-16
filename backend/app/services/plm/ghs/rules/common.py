"""Shared helpers for the per-hazard-class rules."""
from __future__ import annotations

from dataclasses import dataclass, field

from ...regulatory.hazard_classes import h_code_for
from ..model import Component, Contributor, DataGap, HazardOutcome, TraceEntry


@dataclass
class RuleOutput:
    outcomes: list[HazardOutcome] = field(default_factory=list)
    trace: list[TraceEntry] = field(default_factory=list)
    gaps: list[DataGap] = field(default_factory=list)

    def extend(self, other: RuleOutput) -> None:
        self.outcomes.extend(other.outcomes)
        self.trace.extend(other.trace)
        self.gaps.extend(other.gaps)


def contributor(c: Component, category: str, note: str = "") -> Contributor:
    return Contributor(name=c.name, cas=c.cas, pct=round(c.pct, 4), category=category, note=note)


def outcome(hazard_class: str, category: str, basis: str, effect: str = "") -> HazardOutcome:
    if effect:
        h = [effect]
    else:
        code = h_code_for(hazard_class, category)
        h = [code] if code else []
    return HazardOutcome(hazard_class=hazard_class, category=category, h_codes=h, basis=basis, effect=effect)


def fmt(x: float) -> str:
    return f"{x:.4g}"
