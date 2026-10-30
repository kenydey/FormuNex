"""P0-3: Chemist Agent NCO:OH molar-balance advisory.

Counting is monkeypatched to token counts (no RDKit needed in the sandbox);
one integration test uses real SMILES and skips when RDKit is absent.
"""

from __future__ import annotations

import pytest

import app.agents.chemist as chemist_mod
from app.agents.chemist import ChemistAgent
from app.agents.rules import FREE_ISOCYANATE_SMARTS
from app.domain.schemas import Formulation, Ingredient, ProductDomain


def _ing(name, role, smiles, molar_mass, pct):
    return Ingredient(
        name=name, role=role, smiles=smiles,
        molar_mass=molar_mass, weight_pct=pct,
    )


def _form(*ings):
    return Formulation(
        name="2K-PU test", domain=ProductDomain.anticorrosion_coating,
        ingredients=list(ings),
    )


def _fake_counter(monkeypatch):
    """Token counting: 'NCO' substrings are isocyanate groups, 'OH' are alcohols."""

    # The balance check early-returns without RDKit; stub a dummy Chem so the
    # fake counter (not real SMARTS) drives the decision logic under test.
    monkeypatch.setattr(chemist_mod, "_rdkit_chem", lambda: object())

    def fake(Chem, smiles, smarts):
        if not smiles:
            return 0
        if smarts == FREE_ISOCYANATE_SMARTS:
            return smiles.count("NCO")
        if smarts == "[CX3](=O)[OX2H]":
            return 0
        return smiles.count("OH")

    monkeypatch.setattr(chemist_mod, "_count_matches", fake)


def _nco_oh_codes(finding):
    return [i.code for i in finding.issues if i.code.startswith("nco_oh")]


def test_imbalance_is_medium_issue(monkeypatch):
    _fake_counter(monkeypatch)
    form = _form(
        _ing("HDI", "hardener", "NCONCO", 100.0, 50.0),
        _ing("Polyol", "resin", "OH", 100.0, 50.0),
    )
    finding = ChemistAgent().inspect(form, explain=False)
    issues = [i for i in finding.issues if i.code == "nco_oh_imbalance"]
    assert len(issues) == 1
    assert issues[0].severity == "medium"
    assert "2.00" in issues[0].message
    assert finding.status == "warn"  # advisory only, never intercept


def test_balanced_ratio_passes(monkeypatch):
    _fake_counter(monkeypatch)
    form = _form(
        _ing("HDI", "hardener", "NCO", 100.0, 50.0),
        _ing("Polyol", "resin", "OH", 100.0, 50.0),
    )
    finding = ChemistAgent().inspect(form, explain=False)
    assert _nco_oh_codes(finding) == []


def test_ratio_inside_approved_interval_passes(monkeypatch):
    _fake_counter(monkeypatch)
    form = _form(
        _ing("HDI", "hardener", "NCO", 100.0, 55.0),
        _ing("Polyol", "resin", "OH", 100.0, 50.0),
    )
    finding = ChemistAgent().inspect(form, explain=False)
    assert _nco_oh_codes(finding) == []


def test_missing_molar_mass_is_data_insufficient_not_imbalance(monkeypatch):
    _fake_counter(monkeypatch)
    form = _form(
        _ing("HDI", "hardener", "NCO", None, 50.0),
        _ing("Polyol", "resin", "OH", 100.0, 50.0),
    )
    finding = ChemistAgent().inspect(form, explain=False)
    issues = [i for i in finding.issues if i.code == "nco_oh_data_insufficient"]
    assert len(issues) == 1
    assert issues[0].severity == "low"
    assert not [i for i in finding.issues if i.code == "nco_oh_imbalance"]


def test_no_nco_system_is_silent(monkeypatch):
    _fake_counter(monkeypatch)
    form = _form(_ing("Polyol", "resin", "OH", 100.0, 50.0))
    finding = ChemistAgent().inspect(form, explain=False)
    assert _nco_oh_codes(finding) == []


def test_solvent_oh_does_not_count(monkeypatch):
    _fake_counter(monkeypatch)
    form = _form(
        _ing("HDI", "hardener", "NCO", 100.0, 50.0),
        _ing("Polyol", "resin", "OH", 100.0, 50.0),
        _ing("Water", "solvent", "OHOH", 18.0, 40.0),
    )
    finding = ChemistAgent().inspect(form, explain=False)
    assert _nco_oh_codes(finding) == []


def test_no_rdkit_fails_open(monkeypatch):
    monkeypatch.setattr(chemist_mod, "_rdkit_chem", lambda: None)
    form = _form(
        _ing("HDI", "hardener", "O=C=NCCCCCCN=C=O", 168.2, 50.0),
        _ing("Polyol", "resin", "OCCO", 62.07, 50.0),
    )
    finding = ChemistAgent().inspect(form, explain=False)
    assert _nco_oh_codes(finding) == []


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("rdkit") is None,
    reason="RDKit not installed",
)
def test_real_smiles_balanced_and_imbalanced():
    balanced = _form(
        _ing("HDI", "hardener", "O=C=NCCCCCCN=C=O", 168.2, 16.82),
        _ing("Ethylene glycol", "resin", "OCCO", 62.07, 6.207),
    )
    assert _nco_oh_codes(ChemistAgent().inspect(balanced, explain=False)) == []
    imbalanced = _form(
        _ing("HDI", "hardener", "O=C=NCCCCCCN=C=O", 168.2, 33.64),
        _ing("Ethylene glycol", "resin", "OCCO", 62.07, 6.207),
    )
    finding = ChemistAgent().inspect(imbalanced, explain=False)
    assert [i.code for i in finding.issues].count("nco_oh_imbalance") == 1
