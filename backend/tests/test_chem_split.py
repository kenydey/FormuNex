"""Three-library B4: bracket-aware splitting, quantities, aliases."""
from __future__ import annotations

from app.services import chem_split as cs


def test_split_keeps_parenthesised_commas():
    entries = cs.split_chemical_list(
        "poly(acrylic acid, sodium salt), NaOH (2 equiv), water"
    )
    assert [e["name"] for e in entries] == [
        "poly(acrylic acid, sodium salt)", "NaOH", "water",
    ]
    assert entries[1]["quantity"] == "2 equiv"
    assert entries[2]["quantity"] is None


def test_trailing_paren_quantity_vs_descriptor():
    entries = cs.split_chemical_list("K2CO3 (10 mmol), CaCl2 (anhydrous)")
    assert entries[0]["quantity"] == "10 mmol"
    # "(anhydrous)" is not a quantity: it stays part of the name.
    assert entries[1]["name"] == "CaCl2 (anhydrous)"
    assert entries[1]["quantity"] is None


def test_bare_quantity_attaches_to_previous():
    entries = cs.split_chemical_list("NaOH, 2 equiv, water")
    assert len(entries) == 2
    assert entries[0] == {"name": "NaOH", "quantity": "2 equiv", "label_like": False}
    assert entries[1]["name"] == "water"


def test_label_like_segments_flagged():
    entries = cs.split_chemical_list("1a, NaOH")
    assert entries[0]["label_like"] is True
    assert entries[1]["label_like"] is False


def test_alias_table_injected_not_invented():
    table = {"THF": "tetrahydrofuran", "DMF": "N,N-dimethylformamide"}
    out = cs.split_and_normalize("THF, water", table)
    assert out[0]["alias_expansion"] == "tetrahydrofuran"
    assert out[1]["alias_expansion"] is None
    # Default table is empty: no expansion is ever guessed.
    assert cs.normalize_abbreviation("THF") is None
    assert cs.SURFACE_TREATMENT_ALIASES == {}


def test_opsin_parse_degrades_to_none():
    # No Java / py2opsin in the sandbox: must return None, never raise.
    assert cs.opsin_parse("ethanol") is None
