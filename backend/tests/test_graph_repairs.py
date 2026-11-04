"""Three-library C2: constants + graph/SMILES deterministic repairs."""
from __future__ import annotations

import pytest

from app.services import graph_repairs as gr


def test_constants():
    from app.services.rgroup import RGROUP_SYMBOLS

    assert gr.VALENCES["C"] == [4]
    assert gr.VALENCES["N"] == [3, 5]
    assert "R1" in RGROUP_SYMBOLS
    tokens = gr.FORMULA_REGEX.findall("C6H5OH")
    assert tokens == ["C", "6", "H", "5", "O", "H"]
    assert gr.FORMULA_REGEX.findall("R1") == ["R1"]


def _ketene_graph(o_x, neighbour="R1"):
    # R1-C(=O) with the C-O distance set by o_x; median bond length is
    # computed from the two bonds (R1-C at 1.0, C-O at o_x - 1.0).
    return {
        "symbols": [neighbour, "C", "O"],
        "coords": [[0.0, 0.0], [1.0, 0.0], [o_x, 0.0]],
        "edges": [[0, 1, 0], [1, 0, 2], [0, 2, 0]],
    }


def test_ketene_site_geometry_gate():
    # C-O distance 2.5 vs median 1.75: 2.5 >= 1.25 * 1.75 -> ketene site.
    assert gr.find_ketene_sites(_ketene_graph(3.5)) == [(1, 2)]
    # Real carbonyl: C-O distance 1.0, median 1.0 -> no site.
    assert gr.find_ketene_sites(_ketene_graph(2.0)) == []
    # Non-R neighbour: a real ketone, never patched.
    assert gr.find_ketene_sites(_ketene_graph(3.5, neighbour="C")) == []
    # No coords: nothing patched (the SMILES-only rewrite is gone).
    graph = _ketene_graph(3.5)
    del graph["coords"]
    assert gr.find_ketene_sites(graph) == []


def test_ketene_repair_inserts_central_carbon():
    graph = _ketene_graph(3.5)
    new_graph, sites = gr.repair_ketene_in_graph(graph)
    assert sites == [(1, 2)]
    assert new_graph["symbols"] == ["R1", "C", "O", "C"]
    assert new_graph["edges"][1][2] == 0  # old C-O bond removed
    assert new_graph["edges"][1][3] == 2  # C=new C
    assert new_graph["edges"][3][2] == 2  # new C=O
    # Input untouched.
    assert graph["symbols"] == ["R1", "C", "O"]


def test_dust_and_runaway():
    assert gr.drop_dust_fragments("CCO.*.CCN") == ("CCO.CCN", 1)
    assert gr.drop_dust_fragments("*") == ("*", 1)
    graph = {
        "symbols": ["C", "*", "O"],
        "coords": [[0.0, 0.0], [5.0, 5.0], [1.0, 0.0]],
        "edges": [[0, 0, 1], [0, 0, 0], [1, 0, 0]],
    }
    new_graph, dropped = gr.drop_dust_nodes(graph)
    assert dropped == 1
    assert new_graph["symbols"] == ["C", "O"]
    assert new_graph["edges"] == [[0, 1], [1, 0]]
    # Runaway by atom count -> single "*" at bbox centre.
    big = {
        "symbols": ["C", "C", "C"],
        "coords": [[0.0, 0.0], [2.0, 0.0], [0.0, 4.0]],
        "edges": [[0, 1, 0], [1, 0, 0], [0, 0, 0]],
    }
    placeholder, flagged = gr.placeholder_runaway_graph(
        big, max_atoms=2, max_fragments=10
    )
    assert flagged is True
    assert placeholder["symbols"] == ["*"]
    assert placeholder["coords"] == [[1.0, 2.0]]
    _, flagged = gr.placeholder_runaway_graph(big, max_atoms=3, max_fragments=10)
    assert flagged is False


def test_edit_plan_replay():
    graph = {
        "symbols": ["C", "O"],
        "coords": [[5.0, 5.0], [20.0, 20.0]],
        "edges": [[0, 1], [1, 0]],
    }
    new_graph, applied, failed = gr.apply_edit_plan(
        graph, [((0, 0, 10, 10), "N"), ((100, 100, 110, 110), "S")]
    )
    assert applied == [0]
    assert failed == [((100, 100, 110, 110), "S")]
    assert new_graph["symbols"] == ["N", "O"]
    assert graph["symbols"] == ["C", "O"]  # pristine untouched


def test_cross_check_paths():
    a = {"bbox": (0, 0, 10, 10), "smiles": "CCO", "valid": True}
    b = {"bbox": (1, 1, 11, 11), "smiles": "INVALID", "valid": False}
    assert gr.cross_check_paths(a, b) == a
    assert gr.cross_check_paths(b, a) == a
    apart = {"bbox": (100, 100, 110, 110), "smiles": "CCO", "valid": True}
    assert gr.cross_check_paths(a, apart) is None  # boxes apart
    assert gr.cross_check_paths(a, {**a, "valid": True}) is None  # both valid
    assert gr.cross_check_paths(
        {"bbox": (0, 0, 10, 10), "smiles": "CCO"},
        {"bbox": (1, 1, 11, 11), "smiles": "CCN"},
    ) is None  # no chem, no flags -> fail closed


def test_bracket_two_letter_elements():
    assert gr.bracket_two_letter_elements("CSeC") == "C[Se]C"
    assert gr.bracket_two_letter_elements("CScC") == "CScC"  # Sc reads as S+c
    assert gr.bracket_two_letter_elements("C[Se]C") == "C[Se]C"


def test_smiles_repairs_with_real_rdkit():
    pytest.importorskip("rdkit")
    from rdkit import Chem

    assert gr.repair_smiles_syntax("C[C@H](R1)C(=O)O", chem=Chem) == (
        "C[C@H](*)C(=O)O"
    )
    assert gr.repair_smiles_syntax("CCC(=O=O)c1ccccc1", chem=Chem) == (
        "CCC(=O)c1ccccc1"
    )
    assert gr.repair_smiles_syntax("O=C(O)H", chem=Chem) == "O=C(O)"
    # Bare (unbracketed) tetravalent N gains its charge; the bracketed
    # form is a different spelling and is not this repair's target.
    assert gr.repair_bare_n_charge("CN(C)(C)C", chem=Chem) == "C[N+](C)(C)C"
    # Valid input passes through; garbage comes back unchanged, never raises.
    assert gr.repair_unparsable_smiles("CCO", chem=Chem) == "CCO"
    assert gr.repair_unparsable_smiles("CN(C)(C)C", chem=Chem) == "C[N+](C)(C)C"
    assert gr.repair_unparsable_smiles("not-a-smiles!!!", chem=Chem) == (
        "not-a-smiles!!!"
    )
    assert gr.repair_unparsable_smiles("CCO", chem=None) == "CCO"


def test_aromatize_ring_helper_with_real_rdkit():
    pytest.importorskip("rdkit")
    from rdkit import Chem

    mol = Chem.MolFromSmiles("C1=CC=CC=C1")
    rw = Chem.RWMol(mol)
    assert gr._aromatize_ring_around(rw, 0, Chem) is True
    bond = rw.GetBondBetweenAtoms(0, 1)
    assert bond.GetBondType() == Chem.BondType.AROMATIC
