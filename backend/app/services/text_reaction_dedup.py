"""Three-library C1: text reaction dedup (pure key comparison).

A text reaction already present among the figure reactions is kept but
marked ``text_mention: True``; a new one joins with ``"source": "text"``.
Identity is the sorted reactant / product SMILES multiset, canonicalised
through RDKit when a chem module is given, raw strings otherwise.
Reactions are plain dicts (``{reactants, products}``) or duck-typed
objects with those attributes. Dict reactions gain ``text_mention`` /
``source`` keys; duck-typed objects gain a ``text_mention`` attribute
when duplicate and pass through unchanged otherwise.
"""
from __future__ import annotations


def _sides(reaction, key: str) -> list[str]:
    if isinstance(reaction, dict):
        values = reaction.get(key, []) or []
    else:
        values = getattr(reaction, key, []) or []
    out: list[str] = []
    for side in values:
        if isinstance(side, str):
            out.append(side.strip())
        elif isinstance(side, dict):
            out.append((side.get("smiles") or "").strip())
        else:
            out.append((getattr(side, "smiles", "") or "").strip())
    return [s for s in out if s]


def _canon(smiles: str, chem) -> str:
    if chem is None:
        return smiles
    try:
        mol = chem.MolFromSmiles(smiles)
        return chem.MolToSmiles(mol) if mol is not None else smiles
    except Exception:
        return smiles


def reaction_key(reaction, *, chem=None) -> tuple:
    """Sorted (reactants, products) canonical SMILES tuple."""
    reactants = tuple(sorted(_canon(s, chem) for s in _sides(reaction, "reactants")))
    products = tuple(sorted(_canon(s, chem) for s in _sides(reaction, "products")))
    return (reactants, products)


def dedup_text_reactions(
    text_reactions: list, figure_reactions: list, *, chem=None
) -> list:
    """Mark text reactions already drawn; add the new ones as source=text."""
    figure_keys = {reaction_key(r, chem=chem) for r in (figure_reactions or [])}
    out: list = []
    for reaction in text_reactions or []:
        base = dict(reaction) if isinstance(reaction, dict) else reaction
        if reaction_key(reaction, chem=chem) in figure_keys:
            if isinstance(base, dict):
                base = {**base, "text_mention": True}
            else:
                try:
                    base.text_mention = True
                except Exception:
                    pass
        else:
            if isinstance(base, dict):
                base = {**base, "source": "text"}
        out.append(base)
    return out
