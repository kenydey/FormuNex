"""Three-library B3: table header -> role mapping (pure post-processing).

Consumes the output of ``embodiment_drafts.parse_markdown_tables``
({headers, rows, ...}) AFTER table parsing; it never changes that parser.
Roles equal to A4 optimisation-run field keys feed
``optimization_run_to_reaction``; any other recognised or unmapped header
becomes a preserved role name in the run dict (ignored by the converter,
never dropped, never forced into a field it does not fit).
"""
from __future__ import annotations

import re

from .chem_split import split_top_level

# Normalised header -> role. Only the first group feeds A4 fields; the
# second group is recognised vocabulary kept verbatim as roles.
_HEADER_ROLES = {
    # A4 optimisation-run field keys.
    "entry": "entry",
    "substance": "reactant_refs",
    "substrate": "reactant_refs",
    "reactant": "reactant_refs",
    "reactants": "reactant_refs",
    "catalyst": "catalyst",
    "ligand": "ligand",
    "electrode": "electrode",
    "current": "current",
    "electrolyte": "electrolyte",
    "solvent": "solvent",
    "temperature": "temperature",
    "time": "duration",
    "duration": "duration",
    "atmosphere": "atmosphere",
    "yield": "yield",
    "result": "yield",
    "yield type": "yield_type",
    "ee": "ee_er",
    "er": "ee_er",
    "ee/er": "ee_er",
    "footnotes": "footnotes",
    "footnote": "footnotes",
    "note": "footnotes",
    "notes": "footnotes",
    # Common abbreviations.
    "temp": "temperature",
    "solv": "solvent",
    "cat": "catalyst",
    # Chinese headers.
    "条目": "entry",
    "底物": "reactant_refs",
    "反应物": "reactant_refs",
    "催化剂": "catalyst",
    "配体": "ligand",
    "电极": "electrode",
    "电流": "current",
    "电解质": "electrolyte",
    "溶剂": "solvent",
    "温度": "temperature",
    "反应温度": "temperature",
    "时间": "duration",
    "反应时间": "duration",
    "气氛": "atmosphere",
    "产率": "yield",
    "结果": "yield",
    # Recognised but not A4 fields: preserved, never forced.
    "ratio": "ratio",
    "measurement": "measurement",
    "counter": "counter",
    "conditions": "conditions",
    "alkyl group": "alkyl_group",
    "r": "R",
    "ar": "Ar",
    "x": "X",
    "y": "Y",
}

_PAREN_RE = re.compile(r"\([^)]*\)")


def _normalize_header(header: str) -> str:
    text = (header or "").strip().lower()
    text = _PAREN_RE.sub("", text)
    return " ".join(text.split())


def map_table_headers(headers: list[str]) -> dict[int, str]:
    """Map each column index to a role.

    Unmapped headers keep their normalised text as the role, so no column
    is ever silently dropped. P2-B21: an empty normalised header gets a
    positional placeholder role (``column_<idx>``) instead of the empty
    string, so its cells do not all collapse under the ``''`` key.
    """
    roles: dict[int, str] = {}
    for idx, header in enumerate(headers or []):
        norm = _normalize_header(header)
        roles[idx] = _HEADER_ROLES.get(norm, norm or f"column_{idx}")
    return roles


def table_to_runs(table: dict) -> list[dict]:
    """One parsed table -> one optimisation-run dict per row.

    The entry comes from an entry column when present, else the 1-based
    row number. Reactant-ref cells split with chem_split's top-level
    splitter (P1-B16: paren-aware, so "poly(acrylic acid, sodium salt)"
    stays one ref). Empty cells are skipped; unmapped columns stay in
    the dict under their preserved role. P1-B15: a second column mapping
    to the same scalar role never silently overwrites the first — the
    first value is kept and the conflict is recorded under
    ``role_conflicts``. P2-B22: cells wider than the header row land in
    ``extra_cells`` instead of being dropped.
    """
    headers = table.get("headers", []) or []
    rows = table.get("rows", []) or []
    roles = map_table_headers(headers)
    runs: list[dict] = []
    for row_idx, row in enumerate(rows):
        run: dict = {}
        for col_idx, cell in enumerate(row):
            text = (cell or "").strip()
            if not text:
                continue
            role = roles.get(col_idx)
            if role is None:
                run.setdefault("extra_cells", []).append(text)
                continue
            if role == "reactant_refs":
                refs = [p.strip() for p in split_top_level(text) if p.strip()]
                if refs:
                    run.setdefault(role, []).extend(refs)
            elif role in run:
                run.setdefault("role_conflicts", []).append(
                    {"role": role, "kept": run[role], "dropped": text}
                )
            else:
                run[role] = text
        if "entry" not in run:
            run["entry"] = str(row_idx + 1)
        runs.append(run)
    return runs
