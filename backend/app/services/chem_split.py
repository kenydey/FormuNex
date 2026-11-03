"""Three-library B4: parenthesis-aware chemical splitting + quantity
extraction + abbreviation normalisation (pure rules).

Brackets: commas / semicolons inside parentheses do NOT split
("poly(acrylic acid, sodium salt)" stays one name). A trailing
parenthesis is a quantity only when its content matches the quantity
pattern ("(2 equiv)", "(10 mmol)"); anything else ("(anhydrous)")
stays part of the name. A bare quantity segment attaches to the
previous entry instead of becoming a phantom chemical.

Abbreviations: SURFACE_TREATMENT_ALIASES is deliberately EMPTY in this
revision. The seed table must come from an offline pass over the
production substance library (131k rows; the dev DB holds only 2 seed
rows and no abbreviation entries to verify against), followed by manual
review — entries are never invented here. Callers pass their reviewed
table; tests inject tables to prove the mechanism.
"""
from __future__ import annotations

import re

from .chem_extract import is_label_like

# Deliberately empty pending the offline production-library pass (see above).
SURFACE_TREATMENT_ALIASES: dict[str, str] = {}

_QUANTITY_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*"
    r"(equiv|eq|mol|mmol|g|mg|kg|mL|L|μL|uL|%|wt%|vol%)\.?\s*$",
    re.IGNORECASE,
)


def _split_top_level(text: str) -> list[str]:
    """Split on ',' / ';' at parenthesis depth 0."""
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in text or "":
        if ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch in ",;" and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _split_name_quantity(segment: str) -> tuple[str, str | None]:
    """One segment -> (name, quantity). Trailing quantity parens only."""
    if segment.endswith(")") and "(" in segment:
        open_idx = segment.rfind("(")
        inner = segment[open_idx + 1 : -1]
        if _QUANTITY_RE.match(inner):
            return segment[:open_idx].strip(), inner.strip()
    return segment, None


def split_chemical_list(text: str) -> list[dict]:
    """Chemical list -> [{name, quantity, label_like}].

    label_like marks digit+letter-suffix tokens (A3 guard) so callers
    route them to label resolution instead of treating them as names.
    """
    entries: list[dict] = []
    for segment in _split_top_level(text):
        if _QUANTITY_RE.match(segment) and entries:
            # Bare quantity: attach to the previous entry.
            entries[-1]["quantity"] = segment.strip()
            continue
        name, quantity = _split_name_quantity(segment)
        entries.append({
            "name": name,
            "quantity": quantity,
            "label_like": is_label_like(name),
        })
    return entries


def normalize_abbreviation(
    name: str, alias_table: dict[str, str] | None = None
) -> str | None:
    """Expand an abbreviation from a reviewed table (exact match only;
    abbreviations are case-sensitive). None when there is no hit."""
    table = alias_table if alias_table is not None else SURFACE_TREATMENT_ALIASES
    return table.get((name or "").strip())


def split_and_normalize(
    text: str, alias_table: dict[str, str] | None = None
) -> list[dict]:
    """Split + quantity extraction + alias expansion in one pass.
    alias_expansion is None when the name has no table hit."""
    out: list[dict] = []
    for entry in split_chemical_list(text):
        entry = dict(entry)
        entry["alias_expansion"] = normalize_abbreviation(
            entry["name"], alias_table
        )
        out.append(entry)
    return out


def opsin_parse(name: str) -> str | None:
    """IUPAC name -> SMILES via py2opsin when Java + py2opsin are
    available; None otherwise (never raises, never a second resolver)."""
    try:
        from py2opsin import py2opsin as _py2opsin

        return _py2opsin(name)
    except Exception:
        return None
