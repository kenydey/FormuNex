"""Batch 4 cluster 2: B/C new modules (table_roles, chem_split, figure_roles,
vision_plan, text_reaction_dedup, doc_label_registry, graph_repairs, rgroup,
white_row_split)."""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.services import (
    chem_split as cs,
    doc_label_registry as dlr,
    figure_roles as fr,
    graph_repairs as gr,
    rgroup as rg,
    table_roles as tr,
    text_reaction_dedup as trd,
    vision_plan as vp,
    white_row_split as wrs,
)


# ── table_roles ──────────────────────────────────────────────────────

def test_b15_same_role_keeps_first_and_flags():
    runs = tr.table_to_runs({
        "headers": ["Yield", "Result"],
        "rows": [["90%", "good"]],
    })
    assert runs[0]["yield"] == "90%"
    assert runs[0]["role_conflicts"] == [
        {"role": "yield", "kept": "90%", "dropped": "good"}
    ]


def test_b16_reactant_split_is_paren_aware():
    runs = tr.table_to_runs({
        "headers": ["Substrate"],
        "rows": [["poly(acrylic acid, sodium salt), ethanol"]],
    })
    assert runs[0]["reactant_refs"] == [
        "poly(acrylic acid, sodium salt)", "ethanol"
    ]


def test_b21_empty_header_gets_placeholder_role():
    runs = tr.table_to_runs({"headers": [""], "rows": [["x"]]})
    assert "" not in runs[0]
    assert runs[0]["column_0"] == "x"


def test_b22_wide_rows_land_in_extra_cells():
    runs = tr.table_to_runs({
        "headers": ["Yield"], "rows": [["90%", "EXTRA"]]
    })
    assert runs[0]["yield"] == "90%"
    assert runs[0]["extra_cells"] == ["EXTRA"]


# ── chem_split ───────────────────────────────────────────────────────

def test_b17_bracket_comma_does_not_split():
    names = [e["name"] for e in cs.split_chemical_list("sodium [1,2-dichloro], ethanol")]
    assert names == ["sodium [1,2-dichloro]", "ethanol"]


def test_b18_mid_segment_quantity_extracted():
    entries = cs.split_chemical_list("A (2 equiv) in THF")
    assert entries[0]["quantity"] == "2 equiv"
    assert "(2 equiv)" not in entries[0]["name"]
    # Non-quantity parens stay in the name.
    entries = cs.split_chemical_list("CaCl2 (anhydrous)")
    assert entries[0]["name"] == "CaCl2 (anhydrous)"
    assert entries[0]["quantity"] is None


def test_b19_bare_quantity_never_overwrites_and_leading_attaches():
    entries = cs.split_chemical_list("ethanol (1 equiv), 2 equiv")
    assert entries[0]["quantity"] == "1 equiv"
    assert entries[0]["quantity_conflict"] == "2 equiv"
    entries = cs.split_chemical_list("(2 equiv), ethanol")
    assert len(entries) == 1
    assert entries[0]["name"] == "ethanol"
    assert entries[0]["quantity"] == "2 equiv"
    assert entries[0]["quantity_from_leading"] is True


def test_b20_amount_units_extended_concentration_excluded():
    entries = cs.split_chemical_list("NaCl (10 umol), KCl (5 nmol)")
    assert entries[0]["quantity"] == "10 umol"
    assert entries[1]["quantity"] == "5 nmol"
    # mM is a concentration, not an amount: stays in the name.
    entries = cs.split_chemical_list("NaCl (10 mM)")
    assert entries[0]["quantity"] is None
    assert entries[0]["name"] == "NaCl (10 mM)"
    # Double parens keep the §6 verified keep-as-name behaviour.
    entries = cs.split_chemical_list("A ((2 equiv))")
    assert entries[0]["name"] == "A ((2 equiv))"
    assert entries[0]["quantity"] is None


# ── figure_roles ─────────────────────────────────────────────────────

def test_b17_scheme_words_win_and_boundaries_hold():
    assert fr.classify_figure("Scheme 1: curved arrow mechanism for synthesis") == fr.ROLE_REACTION_SCHEME
    assert fr.classify_figure("Scheme 2: activity against bacteria") == fr.ROLE_REACTION_SCHEME
    assert fr.classify_figure("Scheme 1: synthesis route, 1H NMR data shown") == fr.ROLE_REACTION_SCHEME
    # Genuine spectra / plots still classify.
    assert fr.classify_figure("Figure 1. 1H NMR spectrum of 1a") == fr.ROLE_SPECTRA
    assert fr.classify_figure("Figure 2. Yield vs. temperature plot") == fr.ROLE_DATA_PLOT
    # "curved" alone (no scheme word) is not a plot word hit.
    assert fr.classify_figure("Figure 5. Curved surface of the catalyst") == fr.ROLE_OTHER


# ── vision_plan ──────────────────────────────────────────────────────

def test_b21_observer_message_renders_str_issues():
    out = vp.observer_user_message(["a note"])
    assert "- a note" in out


def test_b31_dict_reactions_count_wildcards():
    reactions = [{"reactants": ["C[*]", "CCO"], "products": []}]
    assert vp.needs_rgroup_fallback(reactions, {}, threshold=1) is True
    assert vp.needs_rgroup_fallback(reactions, {"agents": ["rgroup"]}, threshold=1) is False


def test_b32_malformed_assertion_drops_issue_not_crash():
    assert vp.contradicts_itself({"assertions": [{"a": 1}]}) is True
    assert vp.contradicts_itself({"assertions": [("k", "v"), ("k", "v")]}) is False
    assert vp.contradicts_itself({"assertions": [("k", "v"), ("k", "w")]}) is True


def test_b33_filter_passes_str_issues_through():
    assert vp.filter_observer_issues(["note"]) == ["note"]


def test_b34_fail_open_lock_is_flagged():
    notes: list = []
    assert vp.main_chain_consistent("CCO", "CC", chem=None, notes=notes) is True
    assert notes == ["main_chain_lock_inactive_no_rdkit"]


# ── text_reaction_dedup ──────────────────────────────────────────────

def test_b38_string_side_is_one_side():
    key = trd.reaction_key({"reactants": "CCO", "products": "CC"})
    assert key == ((("CCO",), ("CC",)))


def test_b39_empty_reactions_do_not_collide():
    out = trd.dedup_text_reactions([{}], [{}])
    assert out[0].get("text_mention") is not True
    assert out[0].get("source") == "text"


def test_b40_object_marking_symmetric_and_reported():
    class Frozen:
        __slots__ = ("reactants", "products")

        def __init__(self):
            self.reactants = ["CCO"]
            self.products = ["CC"]

    report: list = []
    out = trd.dedup_text_reactions(
        [Frozen()], [{"reactants": ["CCO"], "products": ["CC"]}], report=report
    )
    assert any(e["reason"] == "unmarkable_object" for e in report)
    assert any(e["reason"] == "raw_string_keys_no_canonicalization" for e in report)
    ns = SimpleNamespace(reactants=["CCN"], products=["CC"])
    out = trd.dedup_text_reactions([ns], [{"reactants": ["CCO"], "products": ["CC"]}])
    assert out[0].source == "text"


def test_b35_backfill_clears_verified():
    reg = dlr.DocumentLabelRegistry()
    reg.register_drawn("1a", "CCO")
    sides, filled = reg.backfill([{"smiles": "", "label": "1a", "verified": True}])
    assert sides[0]["smiles"] == "CCO"
    assert sides[0]["verified"] is False
    assert filled == ["1a"]


# ── graph_repairs ────────────────────────────────────────────────────

def test_b23_dust_degree_is_bidirectional():
    graph = {"symbols": ["*", "C"], "coords": [[0, 0], [1, 0]], "edges": [[0, 0], [1, 0]]}
    new_graph, dropped = gr.drop_dust_nodes(graph)
    assert dropped == 0
    assert new_graph["symbols"] == ["*", "C"]


def test_b24_short_coords_return_unchanged():
    graph = {"symbols": ["C", "C", "C"], "coords": [[0, 0]], "edges": [[0, 0, 0]] * 3}
    notes: list = []
    new_graph, dropped = gr.drop_dust_nodes(graph, notes=notes)
    assert dropped == 0
    assert new_graph == graph
    assert notes == ["coords_length_mismatch_graph_unchanged"]


def test_b25_second_edit_on_same_node_fails():
    graph = {"symbols": ["C", "C"], "coords": [[5, 5], [50, 50]], "edges": [[0, 0], [0, 0]]}
    new_graph, applied, failed = gr.apply_edit_plan(
        graph, [((0, 0, 10, 10), "N"), ((0, 0, 10, 10), "S")]
    )
    assert applied == [0]
    assert len(failed) == 1
    assert new_graph["symbols"][0] == "N"


def test_b27_formula_regex_keeps_second_letter():
    assert gr.FORMULA_REGEX.findall("Rf Rn Ra Ar") == ["Rf", "Rn", "Ra", "Ar"]
    assert gr.FORMULA_REGEX.findall("R1 R") == ["R1", "R"]


def test_b18_ar_placeholder_not_bracketed():
    assert gr.bracket_two_letter_elements("Ar") == "Ar"
    assert gr.bracket_two_letter_elements("Pr") == "Pr"
    # Genuine two-letter elements still bracket.
    assert gr.bracket_two_letter_elements("Se") == "[Se]"


def test_b26_chain_reports_rule_name():
    pytest.importorskip("rdkit")
    from rdkit import Chem

    # "[@]" is not a valid atom: the chain's syntax step rewrites it
    # to "[C@]", which parses — and the rule name must be reported.
    notes: list = []
    out = gr.repair_unparsable_smiles("C[@](C)(C)C", chem=Chem, notes=notes)
    assert out == "C[C@](C)(C)C"
    assert notes == ["syntax"]
    # An already-parseable input rewrites nothing and reports nothing.
    notes = []
    assert gr.repair_unparsable_smiles("CCO", chem=Chem, notes=notes) == "CCO"
    assert notes == []


# ── rgroup ───────────────────────────────────────────────────────────

def test_b14_no_chain_expansion():
    new, _ = rg.substitute_rgroups("[R1]", {"R1": "[R]", "R": "C"})
    assert new == "[R]"


def test_b13_second_dummy_returns_none():
    class _Mol:
        def __init__(self, smi):
            self.smi = smi

    class _Chem:
        @staticmethod
        def MolFromSmiles(smi):
            return None if smi == "INVALID" else _Mol(smi)

        @staticmethod
        def MolToSmiles(mol):
            return mol.smi

        @staticmethod
        def ReplaceSubstructs(template, query, repl, replaceAll=False):
            if "[*]" not in template.smi:
                return ()
            return (_Mol(template.smi.replace("[*]", repl.smi, 1)),)

        @staticmethod
        def SanitizeMol(mol):
            return None

    assert rg.graft_rgroup_fragment("c1ccc([*])cc1[*]", "CCO", chem=_Chem()) is None
    assert rg.graft_rgroup_fragment("c1ccc([*])cc1", "CCO", chem=_Chem()) == "c1ccc(CCO)cc1"


def test_b15_dict_and_namespace_sides_work():
    reaction = {"reactants": [{"smiles": "[R1]", "label": "a"}], "products": []}
    new_rxn, report, _ = rg.apply_rgroup_to_reaction(reaction, {"R1": "CCO"})
    assert new_rxn["reactants"][0]["smiles"] == "CCO"
    assert new_rxn["reactants"][0]["verified"] is False
    assert report == {"a": ("[R1]", "CCO")}
    ns_rxn = SimpleNamespace(
        reactants=[SimpleNamespace(smiles="[R1]", label="a")], products=[]
    )
    new_rxn, _, _ = rg.apply_rgroup_to_reaction(ns_rxn, {"R1": "CCO"})
    assert new_rxn.reactants[0].smiles == "CCO"


def test_b16_ignored_definitions_reported_and_ar_variants_accepted():
    ignored: list = []
    mapping = rg.parse_rgroup_definitions("Ar1 = Me\nr1 = Et", ignored=ignored)
    assert mapping == {"Ar1": "Me"}
    assert ignored == [("r1", "r1 = Et")]


# ── white_row_split ──────────────────────────────────────────────────

def test_b20_filter_keeps_segments_without_labels():
    kept = wrs.filter_segments([b"a", b"b", b"c"], ["ok"])
    assert kept == [b"a", b"b", b"c"]


def test_b28_tiny_all_white_image_returns_whole():
    img = Image.new("RGB", (10, 3), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    segments, cuts = wrs.split_white_rows(buf.getvalue())
    assert cuts == []
    assert len(segments) == 1


def test_b29_numpy_bool_is_a_real_bool():
    import numpy as np

    assert wrs.recall_gate(np.bool_(False)) is False
    assert wrs.recall_gate(np.bool_(True)) is True
