"""Inputs and outputs of the GB 30000 mixture classification engine.

Plain dataclasses, no database types: the engine is a pure function of
(components, mixture properties, rule set), which is what makes a result
reproducible from its ``inputs_hash``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Severity order of categories within one hazard class (most severe first).
CATEGORY_ORDER: dict[str, int] = {
    c: i for i, c in enumerate(
        ["unstable", "1.1", "1.2", "1.3", "1.5", "1.4", "1.6", "A", "B", "C", "D", "E", "F", "G",
         "1A", "1", "1B", "1C", "2A", "2", "2B", "3", "4", "5", "lact", "gas", "refrigerated"]
    )
}


def severity(category: str) -> int:
    return CATEGORY_ORDER.get(category, 99)


@dataclass
class ComponentHazard:
    """One classification of one ingredient (from the substance database or entered by hand)."""

    hazard_class: str
    category: str
    h_code: str | None = None
    # Specific concentration limits: [{"category": "1", "min_pct": 10.0}] means the ingredient
    # alone puts the mixture in category 1 at ≥ 10 % (replaces the generic limit).
    scl: list[dict] | None = None
    m_factor_acute: int | None = None
    m_factor_chronic: int | None = None
    ate_value: float | None = None
    ate_unit: str | None = None  # mg/kg | ppmV | mg/L
    qualifier: str = ""
    source: str = "manual"
    # Skin / eye additivity does not apply (strong acids / bases, Table 3.2.4).
    non_additive: bool = False
    conditional: bool = False

    def scl_for(self, category: str) -> float | None:
        for entry in self.scl or []:
            if str(entry.get("category")) == category and entry.get("min_pct") is not None:
                return float(entry["min_pct"])
        return None

    def effects(self) -> list[str]:
        """STOT SE 3 effects as H codes (H335 respiratory irritation, H336 narcotic effects)."""
        return [h for h in (self.h_code or "").replace(" ", "").split(",") if h in ("H335", "H336")]


@dataclass
class Component:
    name: str
    pct: float
    cas: str | None = None
    substance_id: str | None = None
    hazards: list[ComponentHazard] = field(default_factory=list)
    # False = nothing is known about this ingredient's hazards ("unknown toxicity").
    assessed: bool = True
    via: list[str] = field(default_factory=list)  # materials it was exploded from
    trade_secret: bool = False

    def of(self, hazard_class: str) -> list[ComponentHazard]:
        return [h for h in self.hazards if h.hazard_class == hazard_class]

    def worst(self, hazard_class: str) -> ComponentHazard | None:
        hs = self.of(hazard_class)
        return min(hs, key=lambda h: severity(h.category)) if hs else None

    def label(self) -> str:
        return f"{self.name}（{self.cas}）" if self.cas else self.name


@dataclass
class MixtureProperties:
    phys_state: str = "liquid"  # liquid | solid | gas | aerosol | paste
    flash_point_c: float | None = None  # closed cup
    initial_boiling_point_c: float | None = None
    ph: float | None = None
    kinematic_viscosity_40c: float | None = None  # mm²/s
    metal_corrosion_rate_mm_per_year: float | None = None
    sustained_combustion: bool | None = None  # UN L.2; False exempts 35 < FP ≤ 60 °C
    inhalation_form: str | None = None  # gas | vapour | dust_mist (default from phys_state)
    product_category: str | None = None  # e.g. "paint" (transport entry selection)
    # Where each value came from (measured / supplier / estimated), echoed into the trace.
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def is_liquid(self) -> bool:
        return self.phys_state in ("liquid", "paste")

    def effective_inhalation_form(self) -> str:
        if self.inhalation_form:
            return self.inhalation_form
        return {"gas": "gas", "aerosol": "dust_mist", "solid": "dust_mist"}.get(self.phys_state, "vapour")


@dataclass
class Contributor:
    name: str
    cas: str | None
    pct: float
    category: str
    note: str = ""


@dataclass
class TraceEntry:
    hazard_class: str
    outcome: str | None  # resulting category, or None (not classified)
    rule: str
    standard_ref: str
    explanation: str
    contributors: list[Contributor] = field(default_factory=list)
    numbers: dict[str, Any] = field(default_factory=dict)


@dataclass
class HazardOutcome:
    hazard_class: str
    category: str
    h_codes: list[str]
    basis: str  # calculation | concentration_limit | measured | ph | implied
    effect: str = ""  # STOT SE 3: H335 / H336


@dataclass
class DataGap:
    code: str
    hazard_class: str | None
    message: str
    severity: str = "warning"  # warning | blocking


@dataclass
class ClassificationResult:
    rule_set_version: str
    hazards: list[HazardOutcome] = field(default_factory=list)
    pictograms: list[dict[str, Any]] = field(default_factory=list)
    signal_word: str | None = None
    h_codes: list[str] = field(default_factory=list)
    p_codes: list[str] = field(default_factory=list)
    label_p_codes: list[str] = field(default_factory=list)
    unknown_toxicity_pct: dict[str, float] = field(default_factory=dict)
    data_gaps: list[DataGap] = field(default_factory=list)
    trace: list[TraceEntry] = field(default_factory=list)
    disclosure: list[dict[str, Any]] = field(default_factory=list)
    transport: dict[str, Any] | None = None
    inputs_hash: str = ""

    @property
    def classified(self) -> bool:
        return bool(self.hazards)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["classified"] = self.classified
        return d
