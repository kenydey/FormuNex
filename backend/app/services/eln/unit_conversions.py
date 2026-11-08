"""ELN unit conversion table (stage 1 / SR-018, D-5).

Pulled ELN values are stored in the *method* unit, not the remote unit:
a value that arrives in a convertible unit is converted first, and only
an unconvertible pair fails (into the partial-failure path, for manual
adjudication). This table is data, not a chain of ``if`` statements in
the sync code, so adding a unit means adding a row here.

Linear dimensions convert through a base unit (``value_in_base =
value * factor``); temperature is affine and handled explicitly.
``convert_unit`` returns None for an unknown pair — the caller turns
that into the remedial error, it never guesses a factor.

Supported dimensions: temperature (°C/°F/K), mass concentration
(g/L family), length (m family), mass (g family), volume (L family),
viscosity (Pa·s family) and time (s family). Dimensionless values
(%, pH) convert only to themselves: a % arriving as a fraction, or a
pH arriving in any other unit, is unconvertible by design.
"""
from __future__ import annotations

# dimension -> unit -> factor to the dimension's base unit.
_LINEAR: dict[str, dict[str, float]] = {
    "concentration": {"g/L": 1.0, "mg/L": 1e-3, "µg/L": 1e-6, "ng/L": 1e-9},
    "length": {"m": 1.0, "cm": 1e-2, "mm": 1e-3, "µm": 1e-6, "nm": 1e-9},
    "mass": {"g": 1.0, "kg": 1e3, "mg": 1e-3, "µg": 1e-6, "ng": 1e-9},
    "volume": {"L": 1.0, "mL": 1e-3, "µL": 1e-6},
    "viscosity": {"Pa·s": 1.0, "mPa·s": 1e-3, "cP": 1e-3},
    "time": {"s": 1.0, "min": 60.0, "h": 3600.0},
}

# Canonical aliases: the micro sign (U+00B5) and Greek mu (U+03BC) are
# both seen in ELN exports (folded first, so every μ form lands on µ);
# litres accept a lowercase l; ASCII micro (u) and lowercase-litre
# spellings fold to canonical units. P2-C19: ml/ul/ug/um and their /L
# combinations were previously unconvertible.
_ALIASES = {
    "l": "L",
    "µl": "µL",
    "ml": "mL",
    "ul": "µL",
    "uL": "µL",
    "ug": "µg",
    "um": "µm",
    "mg/l": "mg/L",
    "g/l": "g/L",
    "ug/l": "µg/L",
    "ng/l": "ng/L",
    "ug/L": "µg/L",
}


def normalize_unit(unit: str | None) -> str:
    """Strip whitespace and fold known aliases to canonical units."""
    if not unit:
        return ""
    u = unit.strip().replace("μ", "µ")
    if u in _ALIASES:
        return _ALIASES[u]
    return u


def _temperature(value: float, from_unit: str, to_unit: str) -> float | None:
    to_celsius = {"°C": lambda v: v, "°F": lambda v: (v - 32.0) * 5.0 / 9.0, "K": lambda v: v - 273.15}
    from_celsius = {"°C": lambda v: v, "°F": lambda v: v * 9.0 / 5.0 + 32.0, "K": lambda v: v + 273.15}
    if from_unit not in to_celsius or to_unit not in from_celsius:
        return None
    return from_celsius[to_unit](to_celsius[from_unit](value))


def convert_unit(value: float, from_unit: str | None, to_unit: str | None) -> float | None:
    """Convert *value* from *from_unit* to *to_unit*, or None if unconvertible.

    Same unit (after normalisation) is the identity. Cross-dimension
    pairs (length to mass, % to anything) are never converted.
    """
    src, dst = normalize_unit(from_unit), normalize_unit(to_unit)
    if not src or not dst:
        return None
    if src == dst:
        return value
    if src in ("°C", "°F", "K") or dst in ("°C", "°F", "K"):
        return _temperature(value, src, dst)
    for factors in _LINEAR.values():
        if src in factors and dst in factors:
            return value * factors[src] / factors[dst]
    return None
