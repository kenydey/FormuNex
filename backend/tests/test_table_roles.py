"""Three-library B3: table header -> role mapping + run conversion."""
from __future__ import annotations

from app.services import table_roles as tr
from app.services.vision_extract import optimization_run_to_reaction


def test_map_headers_english_units_and_abbreviations():
    roles = tr.map_table_headers([
        "Entry", "Substance", "Catalyst", "Temperature (°C)", "Time (h)",
        "Solvent", "Yield (%)", "Temp", "Pressure",
    ])
    assert roles == {
        0: "entry", 1: "reactant_refs", 2: "catalyst", 3: "temperature",
        4: "duration", 5: "solvent", 6: "yield", 7: "temperature",
        8: "pressure",  # unmapped: preserved, never dropped
    }


def test_map_headers_chinese_and_variable_columns():
    roles = tr.map_table_headers(["条目", "底物", "温度", "产率", "R", "Ar", "Ratio"])
    assert roles == {
        0: "entry", 1: "reactant_refs", 2: "temperature", 3: "yield",
        4: "R", 5: "Ar", 6: "ratio",
    }


def test_table_to_runs_entry_fallback_and_ref_split():
    table = {
        "headers": ["Substance", "Catalyst", "Yield", "Ratio"],
        "rows": [
            ["1a, 2b", "Pd/C", "78%", "1:2"],
            ["3a", "", "65%", ""],
        ],
        "label": "Example",
        "page_hint": None,
    }
    runs = tr.table_to_runs(table)
    assert runs[0] == {
        "reactant_refs": ["1a", "2b"], "catalyst": "Pd/C",
        "yield": "78%", "ratio": "1:2", "entry": "1",
    }
    assert runs[1] == {"reactant_refs": ["3a"], "yield": "65%", "entry": "2"}


def test_runs_feed_a4_converter():
    runs = tr.table_to_runs({
        "headers": ["Entry", "Substance", "Solvent", "Yield"],
        "rows": [["5", "1a", "EtOH", "78%"]],
        "label": "Example",
        "page_hint": None,
    })
    rxn = optimization_run_to_reaction(runs[0], label_to_smiles={"1a": "CCO"})
    assert rxn.entry == "5"
    assert rxn.reactants[0].smiles == "CCO"
    roles = {c.role: c.text for c in rxn.conditions}
    assert roles == {"solvent": "EtOH", "yield": "78%"}
