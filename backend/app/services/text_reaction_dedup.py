"""Three-library C1: text reaction dedup (pure key comparison).

A text reaction already present among the figure reactions is kept but
marked ``text_mention: True``; a new one joins with ``"source": "text"``.
Identity is the sorted reactant / product SMILES multiset, canonicalised
through RDKit when a chem module is given, raw strings otherwise.
Reactions are plain dicts (``{reactants, products}``) or duck-typed
objects with those attributes. Dict reactions gain ``text_mention`` /
``source`` keys. Duck-typed objects are COPIED before marking (P3-B25):
the caller's object is never mutated — the marked copy is what the
result list carries; an uncopyable object falls back to the original and
is recorded in ``report`` as ``uncopyable_object``.
"""
from __future__ import annotations


def _sides(reaction, key: str) -> list[str]:
    if isinstance(reaction, dict):
        values = reaction.get(key, []) or []
    else:
        values = getattr(reaction, key, []) or []
    # P2-B38: a bare string side is ONE side, not one side per char.
    if isinstance(values, str):
        values = [values]
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
    text_reactions: list, figure_reactions: list, *, chem=None, report: list | None = None
) -> list:
    """Mark text reactions already drawn; add the new ones as source=text.

    Marking is symmetric for dicts and objects: duplicates get
    ``text_mention`` and new reactions get ``source: "text"`` either way.
    P2-B40: when an object cannot take the attribute (frozen model),
    it passes through unchanged and the failure is recorded in
    ``report`` instead of failing silently. P2-B39: an empty key
    (``((), ())``) never counts as a duplicate — two empty reactions
    are not the same reaction. P2-B41: with ``chem=None`` keys are raw
    strings, so isomer spellings (OCC vs CCO) do not dedup; that is
    recorded in ``report`` so callers do not mistake it for a
    canonical comparison.
    """
    if chem is None and report is not None:
        report.append({"reason": "raw_string_keys_no_canonicalization"})
    figure_keys = {reaction_key(r, chem=chem) for r in (figure_reactions or [])}
    out: list = []
    for idx, reaction in enumerate(text_reactions or []):
        key = reaction_key(reaction, chem=chem)
        is_duplicate = key != ((), ()) and key in figure_keys
        # P3-B25: the dict path copies, the object path used to mutate
        # the caller's object in place. Shallow-copy objects too; if a
        # copy fails, fall back to the original and let the existing
        # unmarkable handling below record it.
        if isinstance(reaction, dict):
            base = dict(reaction)
        else:
            try:
                import copy as _copy

                base = _copy.copy(reaction)
            except Exception:
                if report is not None:
                    report.append({"reason": "uncopyable_object", "index": idx})
                base = reaction
        if is_duplicate:
            if isinstance(base, dict):
                base = {**base, "text_mention": True}
            else:
                try:
                    base.text_mention = True
                except Exception:
                    if report is not None:
                        report.append({"reason": "unmarkable_object", "index": idx, "mark": "text_mention"})
        elif isinstance(base, dict):
            base = {**base, "source": "text"}
        else:
            try:
                base.source = "text"
            except Exception:
                if report is not None:
                    report.append({"reason": "unmarkable_object", "index": idx, "mark": "source"})
        out.append(base)
    return out
