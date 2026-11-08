"""Three-library B4: parenthesis-aware chemical splitting + quantity
extraction + abbreviation normalisation (pure rules).

Brackets: commas / semicolons inside parentheses do NOT split
("poly(acrylic acid, sodium salt)" stays one name). A trailing
parenthesis is a quantity only when its content matches the quantity
pattern ("(2 equiv)", "(10 mmol)"); anything else ("(anhydrous)")
stays part of the name. A bare quantity segment attaches to the
previous entry — or, when leading, to the next entry — instead of
becoming a phantom chemical.

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
    r"(equiv|eq|mol|mmol|μmol|umol|nmol|g|mg|kg|mL|L|μL|uL|%|wt%|vol%)\.?\s*$",
    re.IGNORECASE,
)
# Deliberately NOT quantity units (2026-11-04 scheme, P2-B20): mM and M
# are concentrations, not amounts — "NaCl (10 mM)" keeps its unit in
# the name rather than being filed as a quantity. If solution-prep
# contexts later need concentrations as quantities, that is a product
# decision, not a silent table extension. (μmol/nmol ARE amounts, like
# mmol, so they belong in the table above.)


def split_top_level(text: str, *, report: list | None = None) -> list[str]:
    """Split on ',' / ';' at parenthesis AND bracket depth 0.

    P2-B17: the old splitter tracked only ``()`` depth, so
    ``sodium [1,2-dichloro], ethanol`` was torn at the bracket comma.

    P3-B27: unbalanced closers used to be silently clamped
    (``depth = max(0, ...)``), leaving the stray ``)`` in a name
    (``"A), B"`` -> ``"A)"``) with no trace. Splitting is unchanged,
    but when ``report`` is supplied an ``unbalanced_parens`` entry is
    appended (closers without an opener, or unclosed openers at end).
    """
    parts: list[str] = []
    depth_paren = 0
    depth_brack = 0
    unbalanced = 0
    buf: list[str] = []
    for ch in text or "":
        if ch == "(":
            depth_paren += 1
            buf.append(ch)
        elif ch == ")":
            if depth_paren == 0:
                unbalanced += 1
            else:
                depth_paren -= 1
            buf.append(ch)
        elif ch == "[":
            depth_brack += 1
            buf.append(ch)
        elif ch == "]":
            if depth_brack == 0:
                unbalanced += 1
            else:
                depth_brack -= 1
            buf.append(ch)
        elif ch in ",;" and depth_paren == 0 and depth_brack == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    if report is not None and (unbalanced or depth_paren or depth_brack):
        report.append({
            "reason": "unbalanced_parens",
            "stray_closers": unbalanced,
            "unclosed_paren": depth_paren,
            "unclosed_brack": depth_brack,
        })
    return [p.strip() for p in parts if p.strip()]


_split_top_level = split_top_level


def _split_name_quantity(segment: str) -> tuple[str, str | None]:
    """One segment -> (name, quantity).

    Trailing quantity parens as before; P2-B18 also recognises a
    quantity paren mid-segment ("A (2 equiv) in THF" -> name
    "A in THF", quantity "2 equiv") instead of leaving the quantity
    inside the name. Non-quantity parens ("(anhydrous)") stay put.
    """
    if segment.endswith(")") and "(" in segment:
        open_idx = segment.rfind("(")
        inner = segment[open_idx + 1 : -1]
        if _QUANTITY_RE.match(inner):
            return segment[:open_idx].strip(), inner.strip()
    match = re.search(r"\(([^()]*)\)", segment)
    if (
        match
        and _QUANTITY_RE.match(match.group(1))
        # Nested parens stay untouched: "A ((2 equiv))" is a verified
        # keep-as-name case (§6), not a mid-segment quantity.
        and (match.start() == 0 or segment[match.start() - 1] != "(")
        and (match.end() >= len(segment) or segment[match.end()] != ")")
    ):
        inner = match.group(1).strip()
        name = (segment[: match.start()] + " " + segment[match.end() :]).strip()
        name = " ".join(name.split())
        return name, inner
    return segment, None


def split_chemical_list(text: str) -> list[dict]:
    """Chemical list -> [{name, quantity, label_like}].

    label_like marks digit+letter-suffix tokens (A3 guard) so callers
    route them to label resolution instead of treating them as names.
    """
    entries: list[dict] = []
    pending_quantity: str | None = None
    for segment in split_top_level(text):
        # Lone quantity paren "(2 equiv)" is a bare quantity, not a name.
        lone = (
            segment.startswith("(")
            and segment.endswith(")")
            and _QUANTITY_RE.match(segment[1:-1])
        )
        if _QUANTITY_RE.match(segment) or lone:
            quantity = segment[1:-1].strip() if lone else segment.strip()
            if entries:
                # P2-B19: a second bare quantity never silently
                # overwrites the first — keep the first, record the
                # conflict.
                if entries[-1].get("quantity") is None:
                    entries[-1]["quantity"] = quantity
                else:
                    entries[-1]["quantity_conflict"] = quantity
            elif pending_quantity is None:
                pending_quantity = quantity
            continue
        name, quantity = _split_name_quantity(segment)
        if not name:
            # P1-B19: a segment that yields an empty name (e.g. a
            # leading "(2 equiv)" that slipped past the lone-paren
            # check) never becomes a phantom entry.
            continue
        if pending_quantity is not None:
            if quantity is None:
                # P1-B19: a leading bare quantity attaches to the next
                # entry instead of becoming a phantom chemical.
                quantity = pending_quantity
                entry_extra = {"quantity_from_leading": True}
            # Either way the pending quantity belongs to at most the
            # immediately following entry; never carried further.
            pending_quantity = None
        else:
            entry_extra = {}
        entries.append({
            "name": name,
            "quantity": quantity,
            "label_like": is_label_like(name),
            **entry_extra,
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
