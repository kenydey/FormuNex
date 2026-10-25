"""Aqueous-mixture physical-hazard suppression (G3).

Ported from MSDS-Generator ``aqueous_mixture_rules``: when water is the
strictly largest single component of the formula, solute physical hazards
must not be inherited into the mixture — unless the mixture itself has a
measured flash point below 60 °C, in which case flammability stands.

FormuNex adaptation (deliberate, documented): FormuNex physical outcomes
are measurement-based, never inherited from ingredient hazards, so there
is no inherited H set to filter. The only ingredient-physical surface in
this engine is the physical *screening* gaps (ingredients in a physical
class at or above the screening threshold ask for a test). The aqueous
rule therefore suppresses screening gaps for the MSDS suppress set,
mapped to screening classes:

- MSDS suppress set: explosive H200–H205, flammable H220–H228,
  oxidizer H270–H272.
- Screening classes in that set: ``explosive``, ``flammable_solid``,
  ``oxidizing_liquid``, ``oxidizing_solid``. (Flammable gas / liquid and
  oxidizing gas never surface as screening gaps; self-reactive,
  organic peroxide, pyrophoric, self-heating and water-reactive are NOT
  in the MSDS set and are never suppressed.)

Measured outcomes are NEVER suppressed by this rule: a flammable-liquid
category from a measured mixture flash point stands whatever the water
content. Known divergence from MSDS: MSDS's final-H filter also drops
H227 (flammable cat 4, FP 60–93 °C) for aqueous mixtures at FP ≥ 60 °C;
FormuNex keeps a measured cat 4, because the flash point is a mixture
measurement, not inheritance. Ties (water == another component) do not
suppress, per MSDS's conservative rule.
"""
from __future__ import annotations

from .model import Component, MixtureProperties

WATER_CAS = "7732-18-5"
_WATER_NAMES = {"水", "纯水", "去离子水", "蒸馏水", "超纯水", "water", "deionized water", "distilled water"}

# Screening classes covered by the MSDS suppress set (see module docstring).
SUPPRESSED_SCREENING_CLASSES = frozenset(
    {"explosive", "flammable_solid", "oxidizing_liquid", "oxidizing_solid"})

FLASH_POINT_THRESHOLD_C = 60.0


def _is_water(component: Component) -> bool:
    cas = (component.cas or "").strip()
    if cas == WATER_CAS:
        return True
    name = (component.name or "").strip().lower()
    return name in _WATER_NAMES


def is_aqueous(components: list[Component]) -> bool:
    """Water is the strictly largest single component (ties do not count)."""
    if not components:
        return False
    water_pct = sum(c.pct for c in components if _is_water(c))
    if water_pct <= 0:
        return False
    max_other = max((c.pct for c in components if not _is_water(c)), default=0.0)
    return water_pct > max_other


def suppressed_screening_classes(components: list[Component], props: MixtureProperties) -> frozenset[str]:
    """Screening classes to suppress for this mixture (empty when the rule
    does not apply): aqueous AND (no measured flash point OR FP >= 60 °C).
    A measured flash point below 60 °C keeps flammability standing."""
    if not is_aqueous(components):
        return frozenset()
    fp = props.flash_point_c
    if fp is not None and fp < FLASH_POINT_THRESHOLD_C:
        return frozenset()
    return SUPPRESSED_SCREENING_CLASSES
