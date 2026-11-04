"""Three-library C2: graph / SMILES deterministic repairs (no decoder).

SmilesPE is NOT installed in this environment, so per the plan this item
takes only the constants and the repair rules — NOT the graph->SMILES
decoder (``convert_graph_to_smiles`` needs SmilesPE). Consequences, stated
plainly:
- ABBREVIATIONS (the 298-entry substitution table) is NOT ported: it
  belongs to the decoder's formula-expansion path and has no consumer
  here; porting it would be dead data. FORMULA_REGEX below therefore
  covers elements + R-groups + numbers + parens only, and its
  abbreviation alternation is deferred with the decoder.
- Edit-plan replay updates graph symbols only; SMILES recomputation
  awaits the decoder.
- Ring repair, N-charge repair and syntax repairs need RDKit (chem
  argument); with chem=None they return the input unchanged.

Graph shape (ChemEAGLE's): ``{symbols, coords, edges}`` where coords are
``[x, y]`` pairs and edges is an adjacency matrix of bond orders.
Repair functions never mutate their input (ChemEAGLE's ketene patch
mutates; ours copies).
"""
from __future__ import annotations

import copy
import re

# ── Constants (ChemEAGLE molnextr/constants.py, verbatim where ported) ──
# RGROUP_SYMBOLS lives in B2's rgroup module and is not duplicated here.
VALENCES = {
    "H": [1], "Li": [1], "Be": [2], "B": [3], "C": [4], "N": [3, 5],
    "O": [2], "F": [1], "Na": [1], "Mg": [2], "Al": [3], "Si": [4],
    "P": [5, 3], "S": [6, 2, 4], "Cl": [1], "K": [1], "Ca": [2],
    "Br": [1], "I": [1],
}

ELEMENTS = [
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca",
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
    "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
    "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd",
    "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
    "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
    "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm",
    "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds",
    "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
]

# Same construction as ChemEAGLE's FORMULA_REGEX, minus the abbreviation
# alternation (deferred with the decoder, see module docstring).
FORMULA_REGEX = re.compile(
    "(?:" + "|".join(
        ["R[0-9]*", "[A-Z][a-z]+", "[A-Z]", "[0-9]+", "\\(", "\\)"]
    ) + ")"
)

# Ketene geometry gate (ChemEAGLE helper.py, measured on real figures):
# a drawn C-O distance at least this many median bond lengths means the
# central carbon was dropped; a real carbonyl measures 1.0-1.1x.
KETENE_STRETCH = 1.25

_REPAIR_HALOGENS = frozenset({9, 17, 35, 53})
_REPAIR_ANION_CENTRES = frozenset({5, 13, 15, 33, 51})  # B, Al, P, As, Sb
_REPAIR_HETEROATOMS = frozenset({7, 8, 15, 16, 34})
_REPAIR_RING_ATOMS = frozenset({6, 7, 8, 15, 16, 34})
_ORGANIC_FIRST_LETTERS = frozenset("BCNOPSFI")
_AROMATIC_SECOND_LETTERS = frozenset("bcnops")
_TWO_LETTER_ELEMENTS = frozenset((
    "He Li Be Ne Na Mg Al Si Ar Ca Sc Ti Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Kr "
    "Rb Sr Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te Xe Cs Ba La Ce Pr Nd Sm Eu "
    "Gd Tb Dy Ho Er Tm Yb Lu Hf Ta Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn"
).split())
# Reconstructed token pattern (the source's exact pattern was not needed:
# the membership + exclusion guards below do the real work).
_TWO_LETTER_TOKEN = re.compile(r"[A-Z][a-z]")

_BARE_N_RE = re.compile(r"(?<!\[)N(?!\])")


def _syntax_repairs() -> tuple:
    """The source's seven syntax rewrites, in order (trailing-H rule:
    a literal H after ")" not followed by a lowercase letter)."""
    return (
        (re.compile(r"\[@\]"), "[C@]"),
        (re.compile(r"\[@@\]"), "[C@@]"),
        (re.compile(r"\(R\d*\)"), "(*)"),
        (re.compile(r"\(H\)"), ""),
        (re.compile(r"(?<=\))H(?![a-z])"), ""),
        (re.compile(r"=O=O"), "=O"),
        (re.compile(r"\[O-\]="), "O="),
    )


# ── Graph helpers ─────────────────────────────────────────────────────────

def _is_wildcard_symbol(sym) -> bool:
    if not isinstance(sym, str):
        return False
    s = sym.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    if not s:
        return False
    if s == "*":
        return True
    if s[0] in ("R", "r") and (len(s) == 1 or s[1:].isdigit()):
        return True
    if s.startswith("Ar") and (len(s) == 2 or s[2:].isdigit()):
        return True
    return False


def _median_bond_length(coords, edges) -> float:
    lengths = []
    n = min(len(coords), len(edges))
    for i in range(n):
        for j in range(i + 1, n):
            if j < len(edges[i]) and edges[i][j]:
                dx = float(coords[i][0]) - float(coords[j][0])
                dy = float(coords[i][1]) - float(coords[j][1])
                lengths.append((dx * dx + dy * dy) ** 0.5)
    if not lengths:
        return 0.0
    lengths.sort()
    m = len(lengths)
    return lengths[m // 2] if m % 2 else 0.5 * (lengths[m // 2 - 1] + lengths[m // 2])


def _bare(symbol: str) -> str:
    if isinstance(symbol, str) and symbol.startswith("[") and symbol.endswith("]"):
        return symbol[1:-1]
    return symbol


def find_ketene_sites(graph: dict, *, stretch: float = KETENE_STRETCH) -> list[tuple[int, int]]:
    """(carbon, oxygen) pairs of a carbonyl whose other neighbours are all
    R groups and whose drawn C-O distance is stretched to two bond
    lengths: a ketene that lost its central carbon. Empty without coords
    or with a zero median bond length — never patched on SMILES alone
    (the old SMILES-only rewrite is gone for the same reason)."""
    symbols = graph.get("symbols", []) or []
    edges = graph.get("edges", []) or []
    coords = graph.get("coords")
    if coords is None:
        return []
    median = _median_bond_length(coords, edges)
    if median <= 0:
        return []
    sites: list[tuple[int, int]] = []
    n = len(symbols)
    for i, sym in enumerate(symbols):
        if _bare(sym) != "C":
            continue
        o_idx = None
        non_o_neighbours: list[int] = []
        for j in range(n):
            if j == i:
                continue
            order = edges[i][j] if i < len(edges) and j < len(edges[i]) else 0
            if order == 0:
                continue
            jbare = _bare(symbols[j])
            if order == 2 and jbare == "O":
                o_other = 0
                for k in range(n):
                    if k == j or k == i:
                        continue
                    if (j < len(edges) and k < len(edges[j]) and edges[j][k]) or (
                        k < len(edges) and j < len(edges[k]) and edges[k][j]
                    ):
                        o_other += 1
                if o_other == 0:
                    o_idx = j
                    continue
            non_o_neighbours.append(j)
        if o_idx is None or not non_o_neighbours:
            continue
        if not all(_is_wildcard_symbol(symbols[k]) for k in non_o_neighbours):
            continue
        if i >= len(coords) or o_idx >= len(coords):
            continue
        dx = float(coords[i][0]) - float(coords[o_idx][0])
        dy = float(coords[i][1]) - float(coords[o_idx][1])
        if (dx * dx + dy * dy) ** 0.5 >= stretch * median:
            sites.append((i, o_idx))
    return sites


def repair_ketene_in_graph(graph: dict, *, stretch: float = KETENE_STRETCH) -> tuple[dict, list[tuple[int, int]]]:
    """Re-insert the dropped central carbon for each ketene site.
    Returns (new graph, sites). Input is copied, never mutated; site
    indices refer to the original graph (appends do not shift them)."""
    sites = find_ketene_sites(graph, stretch=stretch)
    if not sites:
        return copy.deepcopy(graph), []
    new_graph = copy.deepcopy(graph)
    symbols = new_graph["symbols"]
    coords = new_graph["coords"]
    edges = new_graph["edges"]
    for c_idx, o_idx in sites:
        n = len(symbols)
        symbols.append("C")
        cx, cy = coords[c_idx][0], coords[c_idx][1]
        ox, oy = coords[o_idx][0], coords[o_idx][1]
        coords.append([(cx + ox) / 2.0, (cy + oy) / 2.0])
        for row in edges:
            row.append(0)
        edges.append([0] * (n + 1))
        edges[c_idx][o_idx] = 0
        edges[o_idx][c_idx] = 0
        edges[c_idx][n] = 2
        edges[n][c_idx] = 2
        edges[n][o_idx] = 2
        edges[o_idx][n] = 2
    return new_graph, sites


def _components(symbols: list, edges: list) -> list[list[int]]:
    """Connected components over bonds (order > 0)."""
    n = len(symbols)
    seen = [False] * n
    comps: list[list[int]] = []
    for start in range(n):
        if seen[start]:
            continue
        stack, comp = [start], []
        seen[start] = True
        while stack:
            node = stack.pop()
            comp.append(node)
            for j in range(n):
                order = edges[node][j] if node < len(edges) and j < len(edges[node]) else 0
                if order and not seen[j]:
                    seen[j] = True
                    stack.append(j)
        comps.append(comp)
    return comps


def placeholder_runaway_graph(
    graph: dict, *, max_atoms: int, max_fragments: int
) -> tuple[dict, bool]:
    """A runaway graph (too many atoms, or too many fragments) becomes a
    single "*" placeholder at the bbox centre, keeping the position so
    downstream code cannot copy a runaway reading. Thresholds are
    caller parameters (no source numbers exist for them)."""
    symbols = graph.get("symbols", []) or []
    edges = graph.get("edges", []) or []
    coords = graph.get("coords", []) or []
    n_fragments = len(_components(symbols, edges)) if symbols else 0
    if len(symbols) <= max_atoms and n_fragments <= max_fragments:
        return copy.deepcopy(graph), False
    if coords:
        xs = [float(c[0]) for c in coords]
        ys = [float(c[1]) for c in coords]
        centre = [(min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0]
    else:
        centre = [0.0, 0.0]
    return {"symbols": ["*"], "coords": [centre], "edges": [[0]]}, True


def drop_dust_nodes(graph: dict) -> tuple[dict, int]:
    """Drop isolated "*" nodes (dust specks misread as atoms). Returns
    (new graph, dropped count) with edges reindexed."""
    symbols = graph.get("symbols", []) or []
    edges = graph.get("edges", []) or []
    coords = graph.get("coords", []) or []

    def _degree(i: int) -> int:
        total = 0
        for j in range(len(symbols)):
            order = edges[i][j] if i < len(edges) and j < len(edges[i]) else 0
            total += 1 if order else 0
        return total

    keep = [
        i for i, sym in enumerate(symbols)
        if not (sym == "*" and _degree(i) == 0)
    ]
    if len(keep) == len(symbols):
        return copy.deepcopy(graph), 0
    remap = {old: new for new, old in enumerate(keep)}
    new_edges = [
        [edges[i][j] if i < len(edges) and j < len(edges[i]) else 0 for j in keep]
        for i in keep
    ]
    _ = remap
    return {
        "symbols": [symbols[i] for i in keep],
        "coords": [coords[i] for i in keep] if coords else [],
        "edges": new_edges,
    }, len(symbols) - len(keep)


def drop_dust_fragments(smiles: str) -> tuple[str, int]:
    """Drop disconnected "*" fragments from a dot-disconnected SMILES.
    All-dust input becomes a single "*" placeholder."""
    if not smiles:
        return smiles, 0
    parts = smiles.split(".")
    kept = [p for p in parts if p != "*"]
    dropped = len(parts) - len(kept)
    if not kept:
        return "*", dropped
    return ".".join(kept), dropped


def apply_edit_plan(
    graph: dict, edits: list[tuple[tuple, str]], *, tolerance: float = 0.0
) -> tuple[dict, list[int], list[tuple[tuple, str]]]:
    """Replay (bbox, symbol) corrections onto a graph: the node inside
    the bbox (nearest its centre on ties) takes the new symbol. Returns
    (new graph, applied node indices, failed edits). The pristine graph
    is never mutated; SMILES recomputation awaits the decoder."""
    new_graph = copy.deepcopy(graph)
    symbols = new_graph.get("symbols", []) or []
    coords = new_graph.get("coords", []) or []
    applied: list[int] = []
    failed: list[tuple[tuple, str]] = []
    for bbox, new_symbol in edits or []:
        x0, y0, x1, y1 = (float(v) for v in bbox)
        best, best_dist = None, None
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        for idx, coord in enumerate(coords):
            x, y = float(coord[0]), float(coord[1])
            if x0 - tolerance <= x <= x1 + tolerance and y0 - tolerance <= y <= y1 + tolerance:
                dist = (x - cx) ** 2 + (y - cy) ** 2
                if best_dist is None or dist < best_dist:
                    best, best_dist = idx, dist
        if best is None:
            failed.append((bbox, new_symbol))
        else:
            symbols[best] = new_symbol
            applied.append(best)
    return new_graph, applied, failed


def _iou(a: tuple, b: tuple) -> float:
    ax0, ay0, ax1, ay1 = (float(v) for v in a)
    bx0, by0, bx1, by1 = (float(v) for v in b)
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, ay1 - ay0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _path_valid(path: dict, chem) -> bool:
    if chem is not None:
        try:
            return chem.MolFromSmiles(path.get("smiles", "") or "") is not None
        except Exception:
            return False
    # No RDKit: fail closed — only an explicit valid flag counts.
    return bool(path.get("valid", False))


def cross_check_paths(
    path_a: dict, path_b: dict, *, iou_threshold: float = 0.5, chem=None
) -> dict | None:
    """Dual-path visual cross-check: same box (IoU >= threshold) with
    exactly one valid path -> the valid path wins. Anything else (both
    valid, both invalid, boxes apart) -> None (no decision)."""
    if _iou(path_a.get("bbox", (0, 0, 0, 0)), path_b.get("bbox", (0, 0, 0, 0))) < iou_threshold:
        return None
    valid_a = _path_valid(path_a, chem)
    valid_b = _path_valid(path_b, chem)
    if valid_a and not valid_b:
        return path_a
    if valid_b and not valid_a:
        return path_b
    return None


# ── SMILES repairs (RDKit-gated unless noted) ─────────────────────────────

def bracket_two_letter_elements(smiles: str) -> str:
    """Se -> [Se] outside brackets, skipping pairs that also read as two
    organic atoms (Sc, Co, Sn ...). Pure string transform, no RDKit."""

    def _sub(m: re.Match) -> str:
        token = m.group(0)
        if token not in _TWO_LETTER_ELEMENTS or (
            token[0] in _ORGANIC_FIRST_LETTERS
            and token[1] in _AROMATIC_SECOND_LETTERS
        ):
            return token
        return "[" + token + "]"

    parts = re.split(r"(\[[^\]]*\])", smiles or "")
    return "".join(
        p if p.startswith("[") else _TWO_LETTER_TOKEN.sub(_sub, p) for p in parts
    )


def _parses(smiles: str, chem) -> bool:
    try:
        return chem.MolFromSmiles(smiles) is not None
    except Exception:
        return False


def repair_bare_n_charge(smiles: str, *, chem=None) -> str:
    """A tetravalent N written without its charge gains [N+]: each bare-N
    position is tried, the first parseable rewrite wins. Without RDKit
    the input is returned unchanged (no guess)."""
    if chem is None or not smiles:
        return smiles
    if _parses(smiles, chem):
        return smiles
    for match in _BARE_N_RE.finditer(smiles):
        pos = match.start()
        candidate = smiles[:pos] + "[N+]" + smiles[pos + 1:]
        if _parses(candidate, chem):
            return candidate
    return smiles


def repair_smiles_syntax(smiles: str, *, chem=None) -> str | None:
    """Spelling accidents (lost-atom stereo markers, inline R labels,
    substituent hydrogens, doubled carbonyls, charged double-bond O):
    each rewrite only when the result parses; all matching rewrites
    together as a second attempt. None when nothing parses (or no RDKit).
    """
    if chem is None or not smiles:
        return None
    for pattern, replacement in _syntax_repairs():
        if not pattern.search(smiles):
            continue
        candidate = pattern.sub(replacement, smiles)
        if candidate != smiles and _parses(candidate, chem):
            return candidate
    candidate = smiles
    for pattern, replacement in _syntax_repairs():
        candidate = pattern.sub(replacement, candidate)
    if candidate != smiles and _parses(candidate, chem):
        return candidate
    return None


def _aromatize_ring_around(rw, idx: int, chem) -> bool:
    """Let RDKit re-place the double bonds of the smallest 5/6 ring
    holding atom idx (port of ChemEAGLE's helper)."""
    rings = sorted(
        (tuple(r) for r in chem.GetSymmSSSR(rw) if idx in r and len(r) in (5, 6)),
        key=len,
    )
    for ring in rings:
        members = set(ring)
        usable = True
        for i in ring:
            atom = rw.GetAtomWithIdx(i)
            if atom.GetAtomicNum() not in _REPAIR_RING_ATOMS or atom.GetDegree() > 3:
                usable = False
                break
            if any(
                b.GetOtherAtomIdx(i) not in members
                and b.GetBondType() != chem.BondType.SINGLE
                for b in atom.GetBonds()
            ):
                usable = False
                break
        if not usable:
            continue
        for k, i in enumerate(ring):
            rw.GetAtomWithIdx(i).SetIsAromatic(True)
            bond = rw.GetBondBetweenAtoms(i, ring[(k + 1) % len(ring)])
            bond.SetBondType(chem.BondType.AROMATIC)
            bond.SetIsAromatic(True)
        return True
    return False


def repair_unparsable_smiles(smiles: str, *, chem=None) -> str:
    """Full deterministic repair chain (port of ChemEAGLE's
    _validate_and_fix_smiles / _repair_unparsable_smiles): bare-N
    charge fix first, then syntax rewrites, two-letter bracketing, then
    up to 8 rounds of valence fixes (halogen counter-ion unbonds,
    surplus-H removal, ring aromatisation), sanitised and re-validated.
    Without RDKit, or when nothing fixes it, the input is returned
    unchanged."""
    if chem is None or not smiles:
        return smiles
    if _parses(smiles, chem):
        return smiles
    bare_n = repair_bare_n_charge(smiles, chem=chem)
    if bare_n != smiles:
        return bare_n
    syntactic = repair_smiles_syntax(smiles, chem=chem)
    if syntactic is not None:
        return syntactic
    try:
        bracketed = bracket_two_letter_elements(smiles)
        if bracketed != smiles and _parses(bracketed, chem):
            return bracketed
        mol = chem.MolFromSmiles(bracketed, sanitize=False)
        if mol is None:
            return smiles
        rw = chem.RWMol(mol)
        for _ in range(8):
            rw.UpdatePropertyCache(strict=False)
            problems = chem.DetectChemistryProblems(rw)
            if not problems:
                break
            changed = False
            for problem in problems:
                if problem.GetType() != "AtomValenceException":
                    continue
                i = problem.GetAtomIdx()
                atom = rw.GetAtomWithIdx(i)
                if atom.GetAtomicNum() in _REPAIR_HALOGENS and atom.GetDegree() >= 2:
                    centres = [
                        n.GetIdx() for n in atom.GetNeighbors()
                        if n.GetAtomicNum() in _REPAIR_ANION_CENTRES
                        and n.GetFormalCharge() < 0
                    ]
                    if centres:
                        for j in [
                            n.GetIdx() for n in atom.GetNeighbors()
                            if n.GetIdx() != centres[0]
                        ]:
                            rw.RemoveBond(i, j)
                        changed = True
                        break
                if atom.GetNumExplicitHs() > 0:
                    atom.SetNumExplicitHs(atom.GetNumExplicitHs() - 1)
                    changed = True
                    break
                if (
                    atom.GetAtomicNum() in _REPAIR_HETEROATOMS
                    and _aromatize_ring_around(rw, i, chem)
                ):
                    changed = True
                    break
            if not changed:
                return smiles
        fixed = rw.GetMol()
        chem.SanitizeMol(fixed)
        out = chem.MolToSmiles(fixed)
        return out if _parses(out, chem) else smiles
    except Exception:
        return smiles
