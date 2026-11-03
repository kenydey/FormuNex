"""Three-library A2: deterministic confidence features for VLM reactions.

Fake Chem supplies hand-built mols (no real RDKit in the sandbox): the
feature code only uses MolFromSmiles / FindMolChiralCenters / GetAtoms /
GetSymbol / GetFormalCharge / GetNeighbors / GetNumHeavyAtoms /
GetRingInfo().NumRings().
"""
from __future__ import annotations

import json

import pytest

from app.services import vision_extract as ve


class _Atom:
    def __init__(self, symbol, charge=0, neighbors=None):
        self._symbol = symbol
        self._charge = charge
        self._neighbors = neighbors or []

    def GetSymbol(self):
        return self._symbol

    def GetFormalCharge(self):
        return self._charge

    def GetNeighbors(self):
        return self._neighbors


class _RingInfo:
    def __init__(self, n):
        self._n = n

    def NumRings(self):
        return self._n


class _Mol:
    def __init__(self, atoms, rings=0, chiral=None):
        self._atoms = atoms
        self._rings = rings
        self.chiral = chiral or []

    def GetAtoms(self):
        return self._atoms

    def GetFormalCharge(self):
        return sum(a.GetFormalCharge() for a in self._atoms)

    def GetNumHeavyAtoms(self):
        return sum(1 for a in self._atoms if a.GetSymbol() != "H")

    def GetRingInfo(self):
        return _RingInfo(self._rings)


def _mol_of(spec):
    """spec: list of symbols, or (symbols, rings, chiral)."""
    if isinstance(spec, tuple):
        symbols, rings, chiral = spec
    else:
        symbols, rings, chiral = spec, 0, []
    return _Mol([_Atom(s) for s in symbols], rings=rings, chiral=chiral)


class _FakeChem:
    def __init__(self, mapping):
        self._mapping = mapping

    def MolFromSmiles(self, smi):
        value = self._mapping.get(smi, "missing")
        if value is None or value == "missing":
            return None
        return value

    def FindMolChiralCenters(self, mol, includeUnassigned=True):
        return mol.chiral


def _benzene():
    return _mol_of((["C"] * 6, 1, []))


def _chlorobenzene():
    return _mol_of((["C"] * 6 + ["Cl"], 1, []))


def _reaction(reactants, products, conditions=None, verified=True):
    def side(smi):
        return ve.VisionReactionSide(smiles=smi, label="", verified=verified and bool(smi))

    return ve.VisionReaction(
        reactants=[side(s) for s in reactants],
        products=[side(s) for s in products],
        conditions=[ve.VisionReactionCondition(role=r, text=t) for r, t in (conditions or [])],
        confidence=0.9,
    )


@pytest.fixture
def chem():
    return _FakeChem({"c1ccccc1": _benzene(), "Clc1ccccc1": _chlorobenzene()})


def test_clean_reaction_scores_full(chem):
    rxn = _reaction(["c1ccccc1"], ["Clc1ccccc1"])
    detail = ve.compute_reaction_confidence(rxn, chem=chem)
    assert detail["score"] == 1.0
    assert detail["level"] == "high"
    assert detail["flags"] == []
    assert detail["features"]["product_heavy_atoms"] == 7
    assert detail["features"]["heavy_atom_balance"] == 1  # recorded, not penalised


def test_gate_caps_score_at_0_3(chem):
    rxn = _reaction(["c1ccccc1"], ["INVALID"], verified=False)
    # INVALID unparsable + unverified side with SMILES -> gate fires.
    detail = ve.compute_reaction_confidence(rxn, chem=chem)
    assert detail["score"] == 0.3
    assert detail["level"] == "low"
    assert "gate_capped_0.3" in detail["flags"]
    assert "invalid_smiles" in detail["flags"]


def test_missing_smiles_and_star(chem):
    rxn = _reaction(["c1ccccc1", ""], ["Clc1ccccc1"])
    detail = ve.compute_reaction_confidence(rxn, chem=chem)
    assert detail["score"] == 0.9
    assert "missing_smiles" in detail["flags"]
    chem2 = _FakeChem({"c1ccccc1": _benzene(), "C*": _mol_of(["C"])})
    rxn2 = _reaction(["c1ccccc1"], ["C*"])
    detail2 = ve.compute_reaction_confidence(rxn2, chem=chem2)
    assert detail2["score"] == 0.9
    assert "star_sides" in detail2["flags"]


def test_duplicate_products_and_overlap(chem):
    rxn = _reaction(["c1ccccc1"], ["Clc1ccccc1", "Clc1ccccc1"])
    detail = ve.compute_reaction_confidence(rxn, chem=chem)
    assert detail["score"] == 0.85
    assert "duplicate_products" in detail["flags"]
    rxn2 = _reaction(["c1ccccc1"], ["c1ccccc1"])
    detail2 = ve.compute_reaction_confidence(rxn2, chem=chem)
    assert detail2["score"] == 0.9
    assert "product_reactant_overlap" in detail2["flags"]


def test_metal_product_unassigned_chiral_yield(chem):
    fe_mol = _mol_of(["Fe", "C", "C"])
    chem2 = _FakeChem({"c1ccccc1": _benzene(), "FeCC": fe_mol})
    rxn = _reaction(["c1ccccc1"], ["FeCC"])
    detail = ve.compute_reaction_confidence(rxn, chem=chem2)
    assert detail["score"] == 0.95
    assert "metal_product_sides" in detail["flags"]

    chiral_mol = _mol_of((["C"] * 4, 0, [(0, "?")]))
    chem3 = _FakeChem({"c1ccccc1": _benzene(), "CC(C)C": chiral_mol})
    rxn3 = _reaction(["c1ccccc1"], ["CC(C)C"])
    detail3 = ve.compute_reaction_confidence(rxn3, chem=chem3)
    assert detail3["score"] == 0.95
    assert "unassigned_chiral" in detail3["flags"]

    rxn4 = _reaction(
        ["c1ccccc1"], ["Clc1ccccc1"],
        conditions=[("yield", "85%"), ("temperature", "25 C")],
    )
    detail4 = ve.compute_reaction_confidence(rxn4, chem=chem)
    assert detail4["score"] == 1.0
    assert "yield_mentioned" in detail4["flags"]


def test_no_rdkit_scores_from_counts_only():
    rxn = _reaction(["c1ccccc1"], ["Clc1ccccc1"])
    detail = ve.compute_reaction_confidence(rxn, chem=None)
    assert detail["score"] == 1.0
    assert "rdkit_unavailable" in detail["flags"]


def test_empty_and_no_structures(chem):
    rxn = ve.VisionReaction(reactants=[], products=[], confidence=0.9)
    detail = ve.compute_reaction_confidence(rxn, chem=chem)
    assert detail["score"] == 0.0
    assert detail["level"] == "low"
    rxn2 = _reaction(["", ""], [""], verified=False)
    detail2 = ve.compute_reaction_confidence(rxn2, chem=chem)
    assert detail2["score"] == 0.3
    assert "no_structures" in detail2["flags"]


def test_reference_agreement_bonus_and_penalty(chem):
    rxn = _reaction(["c1ccccc1"], ["Clc1ccccc1"])
    full = ve.compute_reaction_confidence(
        rxn, chem=chem, reference_smiles={"c1ccccc1", "Clc1ccccc1"}
    )
    assert full["score"] == 1.0  # bonus capped at 1.0
    assert "reference_agreement_full" in full["flags"]
    assert full["features"]["reference_agreement"] == 1.0
    low = ve.compute_reaction_confidence(
        rxn, chem=chem, reference_smiles={"CCO"}
    )
    assert low["score"] == 0.95
    assert "reference_agreement_low" in low["flags"]
    assert low["features"]["reference_agreement"] == 0.0


def test_checker_answers_adjust_and_note():
    score, flags, note = ve.apply_checker_answers(1.0, [], {"drawn": "no", "duplicate": "yes", "complete": "unsure"})
    assert score == 0.8
    assert "checker_drawn_no" in flags and "checker_duplicate_yes" in flags
    assert note is not None and "unsure" not in note
    score2, flags2, note2 = ve.apply_checker_answers(0.9, ["x"], None)
    assert (score2, flags2, note2) == (0.9, ["x"], None)


def test_summary_counts_and_warnings():
    high = _reaction(["c1ccccc1"], ["Clc1ccccc1"])
    high.confidence_level = "high"
    high.confidence_flags = []
    low = _reaction(["c1ccccc1"], ["INVALID"], verified=False)
    low.confidence_level = "low"
    low.confidence_flags = ["gate_capped_0.3"]
    summary = ve.build_confidence_summary([high, low])
    assert summary["n_high"] == 1 and summary["n_low"] == 1
    assert summary["low_indices"] == [1]
    assert any("gate_capped_0.3" in w for w in summary["warnings"])


def test_extract_wires_confidence_fields(monkeypatch):
    from app.services import vision_extract
    from tests.test_vision_reaction import _fake_rdkit, _vision_configured, _REACTION_JSON

    _vision_configured(monkeypatch)
    _fake_rdkit(monkeypatch)
    monkeypatch.setattr(
        vision_extract, "_call_vision", lambda *a: json.dumps(_REACTION_JSON)
    )
    extraction, hint = vision_extract.extract_reaction_image(b"\x89PNG", "rxn.png")
    assert hint is None and extraction is not None
    rxn = extraction.reactions[0]
    assert rxn.reaction_smiles == "c1ccccc1>>Clc1ccccc1"
    assert rxn.confidence == 0.9
    assert rxn.confidence_score == 1.0
    assert rxn.confidence_level == "high"
    assert extraction.confidence_summary["n_high"] == 1
