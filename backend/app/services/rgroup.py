"""Three-library B2: R-group vocabulary + substitution (pure rules).

Two sources merged into one constant table: OpenChemIE RGROUP_SYMBOLS and
ChemEAGLE's _apply_table_plan symbols. Substitution is explicit-equation
only: a mapping comes from a parsed ``SYM = fragment`` definition, never
from inferring an equation out of a product table.
"""
from __future__ import annotations

import copy
import re

# Variable-group symbols recognised in schemes and tables.
RGROUP_SYMBOLS = frozenset({
    "R", "R1", "R2", "R3", "R4", "R'", "R''",
    "Ar", "X", "Y", "Z",
    "Me", "Et", "Pr", "iPr", "Bu", "tBu", "Ph", "Bn",
    "Ac", "Boc", "TBS", "TMS", "Ms", "Ts", "Tf",
})

_DEFINITION_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9']*)\s*=\s*(.+?)\s*$")


def parse_rgroup_definitions(
    text: str, *, ignored: list | None = None
) -> dict[str, str]:
    """Parse explicit ``SYM = fragment`` definitions (one per line or
    semicolon-separated). Anything else is ignored — never inferred.

    P2-B16: silently dropped definitions are now reported — when an
    ``ignored`` list is supplied, each skipped definition line is
    appended as (symbol, line). The gate also accepts ``Ar\\d+``
    (Ar1, Ar2 — aryl variants); anything else outside RGROUP_SYMBOLS
    / R\\d+ still does not become a mapping.
    """
    mapping: dict[str, str] = {}
    if not text:
        return mapping
    # P3-B22 (record only, verified): \r\n needs no explicit split — the
    # definition regex anchors (\s* ... \s*$) swallow a stray \r on
    # either side of a definition, so CRLF input parses like LF input.
    for part in re.split(r"[;\n]", text):
        match = _DEFINITION_RE.match(part)
        if not match:
            continue
        symbol, fragment = match.group(1), match.group(2)
        if (
            symbol in RGROUP_SYMBOLS
            or re.fullmatch(r"R\d+", symbol)
            or re.fullmatch(r"Ar\d+", symbol)
        ):
            mapping[symbol] = fragment
        elif ignored is not None:
            ignored.append((symbol, part.strip()))
    return mapping


def _substitute_string(smiles: str, mapping: dict[str, str]) -> tuple[str, bool]:
    """String-level substitution. Bracketed [SYM] forms always; bare SYM
    only when bounded by non-alphanumerics on both sides (a bare R inside
    OR / CR is ambiguous with real atoms and is NOT substituted).
    Longest symbols first so R1 wins over R.

    P2-B14: single-pass. Each matched occurrence is first replaced by
    a NUL placeholder and fragments are swapped in only at the end,
    so a fragment that itself contains a symbol is never rescanned:
    {"R1": "[R]", "R": "C"} on "[R1]" yields "[R]", not "C".
    """
    out, changed = smiles, False
    placeholders: dict[str, str] = {}
    for idx, symbol in enumerate(sorted(mapping, key=len, reverse=True)):
        fragment = mapping[symbol]
        token = f"\x00RG{idx}\x00"
        hit = False
        bracketed = f"[{symbol}]"
        if bracketed in out:
            out = out.replace(bracketed, token)
            hit = True
        bounded = re.compile(r"(?<![A-Za-z0-9])" + re.escape(symbol) + r"(?![A-Za-z0-9])")
        if bounded.search(out):
            # P0 fix (2026-11-04 deep review): the replacement string must
            # not pass through re.sub's escape processing — a fragment with
            # a backslash (legal SMILES stereochemistry, e.g. C\C=C\C)
            # raised re.error: bad escape. A lambda returns it verbatim.
            # (Here the lambda returns only the placeholder token, which
            # carries no backslash; the fragment itself is swapped in
            # with str.replace below, so it stays verbatim too.)
            out = bounded.sub(lambda m, t=token: t, out)
            hit = True
        if hit:
            placeholders[token] = fragment
            changed = True
    for token, fragment in placeholders.items():
        out = out.replace(token, fragment)
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


def _dummy_count(mol, smi: str) -> int:
    """Dummy-atom count for a mol, with a SMILES-text fallback.

    Real RDKit mols expose GetAtoms (atomic num 0); fake/test chem
    objects do not, so fall back to counting "[*]" plus bare "*"
    markers in the SMILES text.
    """
    get_atoms = getattr(mol, "GetAtoms", None)
    if callable(get_atoms):
        try:
            return sum(1 for a in get_atoms() if a.GetAtomicNum() == 0)
        except Exception:
            pass
    return smi.count("[*]") + len(re.findall(r"(?<!\[)\*(?!\])", smi))


def graft_rgroup_fragment(
    template_smiles: str, fragment_smiles: str, *, chem
) -> str | None:
    """Replace one dummy atom ([*]) in a template with a fragment.

    RDKit ReplaceSubstructs, replaceAll=False, SanitizeMol-checked.
    Returns the grafted SMILES or None (no dummy, parse failure, or
    sanitisation failure — the caller keeps the original).

    P2-B13: a template with TWO dummies used to return "success" with
    the second dummy still in place. The graft is valid only when
    exactly one template dummy was consumed: the grafted dummy count
    must equal the fragment's own dummy count, and the template must
    have carried at least one dummy.
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
        out = chem.MolToSmiles(grafted[0])
        template_dummies = _dummy_count(template, template_smiles)
        fragment_dummies = _dummy_count(fragment, fragment_smiles)
        grafted_dummies = _dummy_count(grafted[0], out)
        if template_dummies < 1 or grafted_dummies != fragment_dummies:
            return None
        return out
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
        # P2-B15: duck-typed means dict sides and plain objects too, not
        # just pydantic models — SimpleNamespace sides used to crash on
        # .model_copy. Dict sides get a dict copy; models keep
        # model_copy; anything else gets a shallow copy + setattr.
        if isinstance(side, dict):
            smiles = side.get("smiles") or ""
            label = side.get("label") or ""
        else:
            smiles = getattr(side, "smiles", "") or ""
            label = getattr(side, "label", "") or ""
        if not (smiles or "").strip():
            return side
        new_smiles, flags = substitute_rgroups(smiles, mapping, chem=chem)
        if new_smiles == smiles:
            return side
        report[label or smiles] = (smiles, new_smiles)
        if "rgroup_string_fallback" in flags:
            string_fallback.append(label or smiles)
        if isinstance(side, dict):
            out = dict(side)
            out["smiles"] = new_smiles
            out["verified"] = False
            return out
        if hasattr(side, "model_copy"):
            return side.model_copy(update={"smiles": new_smiles, "verified": False})
        out = copy.copy(side)
        out.smiles = new_smiles
        out.verified = False
        return out

    if isinstance(reaction, dict):
        return (
            {
                **reaction,
                "reactants": [_sub_side(s) for s in reaction.get("reactants", [])],
                "products": [_sub_side(s) for s in reaction.get("products", [])],
            },
            report,
            string_fallback,
        )
    new_reactants = [_sub_side(s) for s in reaction.reactants]
    new_products = [_sub_side(s) for s in reaction.products]
    if hasattr(reaction, "model_copy"):
        new_reaction = reaction.model_copy(update={
            "reactants": new_reactants,
            "products": new_products,
        })
    else:
        new_reaction = copy.copy(reaction)
        new_reaction.reactants = new_reactants
        new_reaction.products = new_products
    return new_reaction, report, string_fallback


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
