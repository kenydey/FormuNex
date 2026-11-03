"""Three-library A5: reaction-eval method layer (canon, signatures, assignment).

The eval script is an offline tool, not production code: tests import it
by file path and stub the RDKit surface (the sandbox has no RDKit).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from collections import Counter
from pathlib import Path

import pytest

_EVAL_PATH = Path(__file__).resolve().parent.parent / "scripts" / "vision_reaction_eval.py"


def _load_eval():
    spec = importlib.util.spec_from_file_location("vision_reaction_eval", _EVAL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _load_eval()


# ── fake RDKit surface ────────────────────────────────────────────────────────

class _FakeAtom:
    _NUM = {"C": 6, "O": 8, "N": 7, "Fe": 26, "Na": 11, "H": 1}

    def __init__(self, symbol, charge=0, isotope=0, mapnum=0):
        self.symbol = symbol
        self.charge = charge
        self.isotope = isotope
        self.mapnum = mapnum

    def GetAtomicNum(self):
        return self._NUM.get(self.symbol, 0)

    def GetSymbol(self):
        return self.symbol

    def GetFormalCharge(self):
        return self.charge

    def SetIsotope(self, value):
        self.isotope = value

    def SetAtomMapNum(self, value):
        self.mapnum = value


class _FakeMol:
    def __init__(self, smi, atoms):
        self.smi = smi
        self.atoms = atoms
        self.stereo_removed = False

    def GetAtoms(self):
        return self.atoms

    def GetNumHeavyAtoms(self):
        return sum(1 for a in self.atoms if a.GetAtomicNum() > 1)


class _FakeChem:
    """Table-driven: smi -> list of (symbol, charge, isotope, mapnum)."""

    def __init__(self, table, smiles_out=None):
        self.table = table
        self.smiles_out = smiles_out or {}
        self.inchi_calls = []
        self.mols = []

    def MolFromSmiles(self, smi):
        spec = self.table.get(smi)
        if spec is None:
            return None
        mol = _FakeMol(smi, [_FakeAtom(*a) for a in spec])
        self.mols.append(mol)
        return mol

    def MolToSmiles(self, mol):
        return self.smiles_out.get(mol.smi, mol.smi)

    def RemoveStereochemistry(self, mol):
        mol.stereo_removed = True

    def MolToInchi(self, mol):
        self.inchi_calls.append(mol.smi)
        return "InChI=" + mol.smi

    def MolFromInchi(self, inchi):
        return self.MolFromSmiles(inchi[len("InChI="):])


def test_canon_illegal_tokens():
    for token in ("", "none", "null", "n/a", "na", "?", "*"):
        assert ev.canon_smiles(token) is None
        assert ev.canon_smiles(token, chem=_FakeChem({})) is None


def test_canon_largest_fragment_and_invalid_skipped():
    chem = _FakeChem({
        "C": [("C", 0, 0, 0)],
        "CCO": [("C", 0, 0, 0), ("C", 0, 0, 0), ("O", 0, 0, 0)],
    })
    assert ev.canon_smiles("C.CCO", chem=chem) == "CCO"
    assert ev.canon_smiles("INVALID.CCO", chem=chem) == "CCO"
    assert ev.canon_smiles("INVALID", chem=chem) is None
    # No-RDKit fallback: longest fragment, no canonicalisation.
    assert ev.canon_smiles("C.CCO", chem=None) == "CCO"


def test_canon_clears_dummy_atom_and_stereo_flag():
    chem = _FakeChem({"CD": [("C", 0, 0, 0), ("D", 0, 5, 3)]})
    assert ev.canon_smiles("CD", chem=chem) == "CD"
    # mols[0] is the transformed mol (mols[1] is the double-parse re-read).
    assert chem.mols[0].atoms[1].isotope == 0
    assert chem.mols[0].atoms[1].mapnum == 0
    chem_on = _FakeChem({"C/C=C/C": [("C", 0, 0, 0), ("C", 0, 0, 0)]})
    assert ev.canon_smiles("C/C=C/C", chem=chem_on, remove_stereo=True) == "C/C=C/C"
    assert chem_on.mols[0].stereo_removed is True
    chem_off = _FakeChem({"C/C=C/C": [("C", 0, 0, 0), ("C", 0, 0, 0)]})
    assert ev.canon_smiles("C/C=C/C", chem=chem_off, remove_stereo=False) == "C/C=C/C"
    assert chem_off.mols[0].stereo_removed is False


def test_canon_inchi_roundtrip_only_for_charged_or_metal():
    chem = _FakeChem({
        "CCO": [("C", 0, 0, 0), ("C", 0, 0, 0), ("O", 0, 0, 0)],
        "[NH4+]": [("N", 1, 0, 0)],
        "Fe": [("Fe", 0, 0, 0)],
    })
    assert ev.canon_smiles("CCO", chem=chem) == "CCO"
    assert chem.inchi_calls == []
    assert ev.canon_smiles("[NH4+]", chem=chem) == "[NH4+]"
    assert ev.canon_smiles("Fe", chem=chem) == "Fe"
    assert chem.inchi_calls == ["[NH4+]", "Fe"]


def test_canon_double_parse_failure():
    chem = _FakeChem(
        {"X1": [("C", 0, 0, 0)]},
        smiles_out={"X1": "BAD"},
    )
    assert ev.canon_smiles("X1", chem=chem) is None


def test_split_side_smiles_bracket_aware():
    assert ev._split_side_smiles("[NH4+]+Cl-") == ["[NH4+]", "Cl-"]
    assert ev._split_side_smiles("CCO+CCN") == ["CCO", "CCN"]


def test_reconcile_roles_both_directions():
    pr, pc = ev._reconcile_roles(
        Counter({"CCO": 1}), Counter(), Counter(), Counter({"CCO": 1})
    )
    assert pr == Counter() and pc == Counter({"CCO": 1})
    pr, pc = ev._reconcile_roles(
        Counter(), Counter({"CCO": 1}), Counter({"CCO": 1}), Counter()
    )
    assert pr == Counter({"CCO": 1}) and pc == Counter()


def test_score_figure_soft_hard_and_extra_conditions():
    chem = _FakeChem({
        "CCO": [("C", 0, 0, 0), ("C", 0, 0, 0), ("O", 0, 0, 0)],
        "CCN": [("C", 0, 0, 0), ("C", 0, 0, 0), ("N", 0, 0, 0)],
    })
    pred = [(["CCO"], ["CCN"], ["THF", "extra"])]
    gt = [(["CCO"], ["CCN"], ["THF"])]
    result = ev.score_figure(pred, gt, chem=chem)
    assert result["soft"]["f1"] == 1.0
    # Extra predicted condition is not penalised (subset semantics).
    assert result["hard"]["f1"] == 1.0
    assert result["predictions_without_gt"] == []
    # Missing a GT condition: soft still perfect, hard fails — but the
    # prediction still matched a GT reaction softly, so it is NOT
    # "without GT" (that column is for reaction-level misses).
    pred2 = [(["CCO"], ["CCN"], ["other"])]
    result2 = ev.score_figure(pred2, gt, chem=chem)
    assert result2["soft"]["f1"] == 1.0
    assert result2["hard"]["f1"] == 0.0
    assert result2["predictions_without_gt"] == []
    # A structurally different prediction is listed as without GT.
    pred3 = [(["CCC"], ["CCN"], ["THF"])]
    chem.table["CCC"] = [("C", 0, 0, 0)] * 3
    result3 = ev.score_figure(pred3, gt, chem=chem)
    assert result3["soft"]["f1"] == 0.0
    assert result3["predictions_without_gt"] == ["CCC>>CCN"]


def test_score_figure_catalyst_read_as_reactant():
    chem = _FakeChem({
        "CCO": [("C", 0, 0, 0), ("C", 0, 0, 0), ("O", 0, 0, 0)],
        "CCN": [("C", 0, 0, 0), ("C", 0, 0, 0), ("N", 0, 0, 0)],
        "c1ccccc1": [("C", 0, 0, 0)] * 6,
    })
    pred = [(["CCO", "c1ccccc1"], ["CCN"], [])]
    gt = [(["CCO"], ["CCN"], ["c1ccccc1"])]
    result = ev.score_figure(pred, gt, chem=chem)
    assert result["soft"]["tp"] == 1


def _adversarial_figure():
    # A matches gt1 and gt2 (hard); B matches gt1 only. Optimal tp = 2,
    # greedy (A first) tp = 1.
    pred = [
        (["R"], ["P"], ["c1", "c2"]),
        (["R"], ["P"], ["c1"]),
    ]
    gt = [
        (["R"], ["P"], ["c1"]),
        (["R"], ["P"], ["c2"]),
    ]
    return pred, gt


def test_assignment_hungarian_optimal(monkeypatch):
    import itertools

    def _brute_lsa(costs):
        n, m = len(costs), len(costs[0])
        best, best_perm = None, None
        for perm in itertools.permutations(range(m)):
            total = sum(costs[i][perm[i]] for i in range(n))
            if best is None or total < best:
                best, best_perm = total, perm
        return list(range(n)), list(best_perm)

    scipy = types.ModuleType("scipy")
    scipy_opt = types.ModuleType("scipy.optimize")
    scipy_opt.linear_sum_assignment = _brute_lsa
    monkeypatch.setitem(sys.modules, "scipy", scipy)
    monkeypatch.setitem(sys.modules, "scipy.optimize", scipy_opt)
    pred, gt = _adversarial_figure()
    result = ev.score_figure(pred, gt, chem=None)
    assert result["hard"]["tp"] == 2
    assert result["assignment"]["hard"] == "hungarian"


def test_assignment_greedy_fallback(monkeypatch):
    monkeypatch.setitem(sys.modules, "scipy", None)
    pred, gt = _adversarial_figure()
    result = ev.score_figure(pred, gt, chem=None)
    assert result["hard"]["tp"] == 1
    assert result["assignment"]["hard"] == "greedy"


def test_ground_truth_list_duplicates_and_missing_file(tmp_path):
    dup = tmp_path / "dup.json"
    dup.write_text(json.dumps([
        {"file": "a.png", "reactions": []},
        {"file": "a.png", "reactions": []},
    ]), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        ev._load_ground_truth(dup)
    nofile = tmp_path / "nofile.json"
    nofile.write_text(json.dumps([{"reactions": []}]), encoding="utf-8")
    with pytest.raises(ValueError, match="'file'"):
        ev._load_ground_truth(nofile)


def test_main_end_to_end(monkeypatch, tmp_path):
    from app.services import vision_extract as ve_mod
    from app.services.vision_extract import (
        VisionReaction, VisionReactionCondition, VisionReactionSide,
    )

    image_dir = tmp_path / "images"
    image_dir.mkdir()
    (image_dir / "rxn1.png").write_bytes(b"\x89PNG")
    (image_dir / "rxn2.png").write_bytes(b"\x89PNG")
    gt = tmp_path / "gt.json"
    gt.write_text(json.dumps({
        "rxn1.png": {"reactions": [
            {"reactants": ["CCO"], "products": ["CCN"], "conditions": ["THF"]},
        ]},
    }), encoding="utf-8")
    out = tmp_path / "report.json"

    def _fake_extract(content, filename):
        if filename == "rxn1.png":
            rxn = VisionReaction(
                reactants=[VisionReactionSide(smiles="CCO", label="")],
                products=[VisionReactionSide(smiles="CCN", label="")],
                conditions=[VisionReactionCondition(role="solvent", text="THF")],
                confidence=0.9,
            )
            rxn.reaction_smiles = "CCO>>CCN"
            return _Extraction([rxn]), None
        rxn = VisionReaction(
            reactants=[VisionReactionSide(smiles="CCC", label="")],
            products=[VisionReactionSide(smiles="CCN", label="")],
            conditions=[],
            confidence=0.9,
        )
        rxn.reaction_smiles = "CCC>>CCN"
        return _Extraction([rxn]), None

    class _Extraction:
        def __init__(self, reactions):
            self.reactions = reactions

    monkeypatch.setattr(ve_mod, "vision_available", lambda: (True, ""))
    monkeypatch.setattr(ve_mod, "extract_reaction_image", _fake_extract)
    monkeypatch.setattr(sys, "argv", [
        "vision_reaction_eval.py",
        "--image-dir", str(image_dir),
        "--ground-truth", str(gt),
        "--out", str(out),
    ])
    assert ev.main() == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["n_images"] == 2
    assert report["n_scored"] == 1
    assert report["n_without_ground_truth"] == 1
    assert report["soft"]["f1"] == 1.0
    assert report["hard"]["f1"] == 1.0
    assert report["n_predictions_without_gt"] == 0
