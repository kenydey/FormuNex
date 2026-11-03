"""Three-library B2: R-group vocabulary + substitution (pure rules).

Two sources merged into one constant table: OpenChemIE RGROUP_SYMBOLS and
ChemEAGLE's _apply_table_plan symbols. Substitution is explicit-equation
only: a mapping comes from a parsed ``SYM = fragment`` definition, never
from inferring an equation out of a product table.
"""
from __future__ import annotations

import re

# Variable-group symbols recognised in schemes and tables.
RGROUP_SYMBOLS = frozenset({
    "R", "R1", "R2", "R3", "R4", "R'", "R''",
    "Ar", "X", "Y", "Z",
    "Me", "Et", "Pr", "iPr", "Bu", "tBu", "Ph", "Bn",
    "Ac", "Boc", "TBS", "TMS", "Ms", "Ts", "Tf",
})

_DEFINITION_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9']*)\s*=\s*(.+?)\s*$")


def parse_rgroup_definitions(text: str) -> dict[str, str]:
    """Parse explicit ``SYM = fragment`` definitions (one per line or
    semicolon-separated). Anything else is ignored — never inferred."""
    mapping: dict[str, str] = {}
    if not text:
        return mapping
    for part in re.split(r"[;\n]", text):
        match = _DEFINITION_RE.match(part)
        if not match:
            continue
        symbol, fragment = match.group(1), match.group(2)
        if symbol in RGROUP_SYMBOLS or re.fullmatch(r"R\d+", symbol):
            mapping[symbol] = fragment
    return mapping


def _substitute_string(smiles: str, mapping: dict[str, str]) -> tuple[str, bool]:
    """String-level substitution. Bracketed [SYM] forms always; bare SYM
    only when bounded by non-alphanumerics on both sides (a bare R inside
    OR / CR is ambiguous with real atoms and is NOT substituted).
    Longest symbols first so R1 wins over R."""
    out, changed = smiles, False
    for symbol in sorted(mapping, key=len, reverse=True):
        fragment = mapping[symbol]
        bracketed = f"[{symbol}]"
        if bracketed in out:
            out = out.replace(bracketed, fragment)
            changed = True
        bounded = re.compile(r"(?<![A-Za-z0-9])" + re.escape(symbol) + r"(?![A-Za-z0-9])")
        if bounded.search(out):
            out = bounded.sub(fragment, out)
            changed = True
    return out, changed


def substitute_rgroups(
    smiles: str, mapping: dict[str, str], *, chem=None
) -> tuple[str, list[str]]:
    """Substitute R-group symbols in one SMILES.

    With RDKit the result must parse (SanitizeMol via the parse); on
    failure the original is kept with flag ``rgroup_substitution_failed``.
    Without RDKit this is the string fallback, flagged
    ``rgroup_string_fallback``. Empty mapping is a no-op.
    """
    if not smiles or not mapping:
        return smiles, []
    new_smiles, changed = _substitute_string(smiles, mapping)
    if not changed:
        return smiles, []
    if chem is None:
        return new_smiles, ["rgroup_string_fallback"]
    mol = chem.MolFromSmiles(new_smiles)
    if mol is None:
        return smiles, ["rgroup_substitution_failed"]
    sanitize = getattr(chem, "SanitizeMol", None)
    if sanitize is not None:
        try:
            sanitize(mol)
        except Exception:
            return smiles, ["rgroup_substitution_failed"]
    return new_smiles, []


def graft_rgroup_fragment(
    template_smiles: str, fragment_smiles: str, *, chem
) -> str | None:
    """Replace one dummy atom ([*]) in a template with a fragment.

    RDKit ReplaceSubstructs, replaceAll=False, SanitizeMol-checked.
    Returns the grafted SMILES or None (no dummy, parse failure, or
    sanitisation failure — the caller keeps the original).
    """
    if chem is None:
        return None
    template = chem.MolFromSmiles(template_smiles)
    fragment = chem.MolFromSmiles(fragment_smiles)
    query = chem.MolFromSmiles("[*]")
    if template is None or fragment is None or query is None:
        return None
    try:
        grafted = chem.ReplaceSubstructs(template, query, fragment, replaceAll=False)
        if not grafted:
            return None
        chem.SanitizeMol(grafted[0])
        return chem.MolToSmiles(grafted[0])
    except Exception:
        return None


def apply_rgroup_to_reaction(reaction, mapping: dict[str, str], *, chem=None):
    """Substitute R-group symbols in a reaction's side SMILES (duck-typed:
    no import of the vision models, so no import cycle).

    Call BEFORE the RDKit gate, like the A3 label resolution: substituted
    sides get verified=False and the gate re-verifies them. Returns
    (new_reaction, report, string_fallback) where report maps a side label
    (or its original SMILES) to (old, new) and string_fallback lists the
    labels substituted without RDKit.
    """
    report: dict[str, tuple[str, str]] = {}
    string_fallback: list[str] = []

    def _sub_side(side):
        if not (side.smiles or "").strip():
            return side
        new_smiles, flags = substitute_rgroups(side.smiles, mapping, chem=chem)
        if new_smiles == side.smiles:
            return side
        report[side.label or side.smiles] = (side.smiles, new_smiles)
        if "rgroup_string_fallback" in flags:
            string_fallback.append(side.label or side.smiles)
        return side.model_copy(update={"smiles": new_smiles, "verified": False})

    return (
        reaction.model_copy(update={
            "reactants": [_sub_side(s) for s in reaction.reactants],
            "products": [_sub_side(s) for s in reaction.products],
        }),
        report,
        string_fallback,
    )


def decompose_rgroups(
    core_smiles: str, mol_smiles_list: list[str]
) -> list[dict[str, str]] | None:
    """Thin wrapper over RDKit rdRGroupDecomposition (core with dummy
    atoms + molecule list -> one {label: SMILES} dict per molecule).
    Returns None when RDKit or the decomposition module is unavailable,
    when any SMILES fails to parse, or when Process() fails — never raises.
    (The real API is a class: construct with the core, Add() each mol,
    Process(), then GetRGroupsAsRows() — not a single function call.)"""
    try:
        from rdkit.Chem import rdRGroupDecomposition
    except Exception:
        return None
    try:
        from rdkit import Chem

        core = Chem.MolFromSmiles(core_smiles)
        mols = [Chem.MolFromSmiles(s) for s in mol_smiles_list]
        if core is None or any(m is None for m in mols):
            return None
        decomp = rdRGroupDecomposition.RGroupDecomposition(core)
        for mol in mols:
            decomp.Add(mol)
        if not decomp.Process():
            return None
        return [
            {label: Chem.MolToSmiles(fragment) for label, fragment in row.items()}
            for row in decomp.GetRGroupsAsRows()
        ]
    except Exception:
        return None
