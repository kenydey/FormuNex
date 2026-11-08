"""Three-library B5: prompt assembly + SMILES immutability check."""
from __future__ import annotations

import pytest

from app.services import prompt_assembly as pa


def test_load_prompt_files_have_placeholder_and_rule():
    for agent in ("planner", "observer", "checker"):
        text = pa.load_prompt(agent)
        assert pa.PLACEHOLDER in text
    # SMILES immutability rule lives in the prompts themselves.
    assert "verbatim" in pa.load_prompt("planner")
    assert "verbatim" in pa.load_prompt("checker")
    with pytest.raises(ValueError, match="unknown prompt agent"):
        pa.load_prompt("nope")


def test_assemble_filters_by_allowed_keys():
    out = pa.assemble_prompt(
        "planner",
        {"solvent": "EtOH", "temperature": "80C", "yield": "78%"},
        allowed_keys=["solvent", "yield"],
    )
    assert "solvent: EtOH" in out
    assert "yield: 78%" in out
    assert "temperature" not in out
    assert pa.PLACEHOLDER not in out


def test_assemble_raises_without_placeholder(monkeypatch):
    monkeypatch.setattr(pa, "load_prompt", lambda agent: "no placeholder here")
    with pytest.raises(ValueError, match="has no"):
        pa.assemble_prompt("planner", {"solvent": "EtOH"})


def test_verify_prefilled_smiles():
    original = {"solvent": "CCO", "catalyst": "[Na+]Cl", "ligand": ""}
    # Verbatim copy passes; charge-mark-only change passes; anything
    # else -> None. (2026-11-04 scheme behavior change, P1-B11: the old
    # substring check passed "in CCO at 80C" and "CCOCC" unflagged —
    # the updated value must BE the SMILES, not prose containing it.)
    updated, flags = pa.verify_prefilled_smiles(
        original,
        {"solvent": "CCO", "catalyst": "[Na]Cl", "ligand": "PPh3"},
    )
    assert updated == {"solvent": "CCO", "catalyst": "[Na]Cl", "ligand": "PPh3"}
    assert flags == []
    # Prose wrapping, chain extension, whole-ion addition all flag.
    updated, flags = pa.verify_prefilled_smiles(
        original,
        {"solvent": "in CCO at 80C", "catalyst": "[Na]Cl"},
    )
    assert updated["solvent"] == "None"
    assert flags == ["prefilled_smiles_changed:solvent"]
    updated, flags = pa.verify_prefilled_smiles(
        original, {"solvent": "regenerated CCN", "catalyst": "[Na]Cl"}
    )
    assert updated["solvent"] == "None"
    assert flags == ["prefilled_smiles_changed:solvent"]
    updated, flags = pa.verify_prefilled_smiles(
        original, {"solvent": "CCOCC", "catalyst": "[Na]Cl"}
    )
    assert updated["solvent"] == "None"
    assert flags == ["prefilled_smiles_changed:solvent"]
    updated, flags = pa.verify_prefilled_smiles(
        original, {"solvent": "[Na+]CCO", "catalyst": "[Na]Cl"}
    )
    assert updated["solvent"] == "None"
    assert flags == ["prefilled_smiles_changed:solvent"]
    # Missing key counts as changed.
    updated, flags = pa.verify_prefilled_smiles(original, {})
    assert updated["solvent"] == "None"
    assert "prefilled_smiles_changed:catalyst" in flags
