"""Three-library A4: footnote merge, condition inheritance, no-drop rules.

Pure rules in vision_extract; prompt rules in vision_reaction.txt +
inline fallback (kept in sync).
"""
from __future__ import annotations

import json

from app.services import vision_extract as ve


def _rxn(entry, conditions, reactants=None, products=None):
    return ve.VisionReaction(
        entry=entry,
        reactants=[ve.VisionReactionSide(smiles=s, label="") for s in (reactants or [])],
        products=[ve.VisionReactionSide(smiles=s, label="") for s in (products or [])],
        conditions=[ve.VisionReactionCondition(role=r, text=t) for r, t in conditions],
        confidence=0.9,
    )


def test_footnote_abbrev_expansion_is_entry_scoped():
    footnotes = [ve.VisionFootnote(key="a", text="SiT2 = Si(CH3)2", entry="2")]
    r1 = _rxn("1", [("solvent", "SiT2, THF")], reactants=["C"], products=["CC"])
    r2 = _rxn("2", [("solvent", "SiT2, THF")], reactants=["C"], products=["CC"])
    merged = ve.merge_footnotes([r1, r2], footnotes)
    assert merged[0].conditions[0].text == "SiT2, THF"  # entry mismatch: untouched
    assert merged[0].applied_footnotes == []
    assert merged[1].conditions[0].text == "Si(CH3)2, THF"
    assert merged[1].applied_footnotes == ["a"]


def test_footnote_document_level_and_marker_note():
    footnotes = [ve.VisionFootnote(key="a", text="dry solvent", entry="")]
    rxn = _rxn("1", [("solvent", "THF [a]")], reactants=["C"], products=["CC"])
    merged = ve.merge_footnotes([rxn], footnotes)
    assert merged[0].conditions[0].text == "THF [a]"  # original text preserved
    assert merged[0].applied_footnotes == ["a"]
    assert "Footnote a: dry solvent" in merged[0].notes


def test_condition_inheritance_explicit_markers_only():
    row1 = _rxn("1", [("solvent", "THF"), ("temperature", "25 C")],
                reactants=["C"], products=["CC"])
    row2 = _rxn("2", [("solvent", "同上"), ("temperature", "80 C")],
                reactants=["C"], products=["CC"])
    row3 = _rxn("3", [("temperature", "same as above")],
                reactants=["C"], products=["CC"])
    out = ve.apply_condition_inheritance([row1, row2, row3])
    assert out[1].conditions[0].text == "THF"
    assert out[1].conditions[1].text == "80 C"
    # Absent role stays absent: row3 has no solvent, it does NOT inherit THF.
    assert [c.role for c in out[2].conditions] == ["temperature"]
    assert out[2].conditions[0].text == "80 C"  # inherited from row2's value
    first_marker = _rxn("1", [("solvent", "同前")], reactants=["C"], products=["CC"])
    assert ve.apply_condition_inheritance([first_marker])[0].conditions[0].text == "同前"


def test_no_reaction_sentinel_unified():
    assert ve.NO_REACTION_SENTINEL == "N.R."
    rxn = ve.VisionReaction(reactants=[], products=[], notes="N.R.", confidence=0.5)
    assert ve.normalize_outcome(rxn).no_reaction is True
    rxn2 = ve.VisionReaction(reactants=[], products=[], notes="NR observed", confidence=0.5)
    assert ve.normalize_outcome(rxn2).no_reaction is True
    # A reaction with sides is never rewritten by the normaliser.
    rxn3 = _rxn("1", [], reactants=["C"], products=["CC"])
    rxn3.notes = "N.R."
    assert ve.normalize_outcome(rxn3).no_reaction is False


def test_optimization_run_to_reaction():
    run = {
        "entry": "3",
        "reactant_refs": ["1a", "9z"],
        "catalyst": "Pd/C",
        "solvent": "EtOH",
        "yield": "78%",
        "footnotes": [{"key": "a", "text": "dry", "entry": "3"}],
    }
    rxn = ve.optimization_run_to_reaction(run, label_to_smiles={"1a": "CCO"})
    assert rxn.entry == "3"
    assert rxn.reactants[0].smiles == "CCO"
    assert rxn.reactants[0].label == "1a"
    # Unresolved refs are kept as label-only sides, never dropped.
    assert rxn.reactants[1].smiles == ""
    assert rxn.reactants[1].label == "9z"
    roles = {c.role: c.text for c in rxn.conditions}
    assert roles == {"catalyst": "Pd/C", "solvent": "EtOH", "yield": "78%"}
    # Footnotes are not conditions; the caller passes them to merge_footnotes.
    assert "footnotes" not in roles


def _vision_configured(monkeypatch, tmp_path):
    import sys
    import types

    from app.config import get_settings
    from app.services.runtime_secrets import get_runtime_secrets

    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.setenv("FORMUNEX_ENV_FILE", str(tmp_path / ".env"))
    for key in (
        "FORMUNEX_LLM_PROVIDER", "FORMUNEX_LLM_BASE_URL",
        "FORMUNEX_VISION_PROVIDER", "FORMUNEX_VISION_MODEL",
        "FORMUNEX_VISION_BASE_URL", "FORMUNEX_VISION_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    rs = get_runtime_secrets()
    rs.clear()
    get_settings.cache_clear()
    rs.set("vision_provider", "custom")
    rs.set("vision_model", "test-vlm")
    rs.set("vision_api_key", "test-key")
    rs.set("vision_base_url", "https://vlm.example.test/v1")
    mod = types.ModuleType("openai")
    mod.OpenAI = object
    monkeypatch.setitem(sys.modules, "openai", mod)


def _fake_rdkit(monkeypatch):
    import sys
    import types

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

    class _RDLogger:
        @staticmethod
        def DisableLog(*args):
            pass

    rdkit = types.ModuleType("rdkit")
    rdkit.Chem = _Chem
    rdkit.RDLogger = _RDLogger
    monkeypatch.setitem(sys.modules, "rdkit", rdkit)


def test_extract_keeps_no_reaction_entry(monkeypatch, tmp_path):
    _vision_configured(monkeypatch, tmp_path)
    _fake_rdkit(monkeypatch)
    payload = {
        "reactions": [
            {
                "reactants": [], "products": [], "conditions": [],
                "confidence": 0.5, "no_reaction": True, "notes": "N.R.",
            },
            {
                "reactants": [{"smiles": "c1ccccc1", "label": "benzene"}],
                "conditions": [{"role": "reagent", "text": "Cl2"}],
                "products": [{"smiles": "Clc1ccccc1", "label": "chlorobenzene"}],
                "confidence": 0.9,
            },
        ],
        "footnotes": [],
        "notes": "",
    }
    monkeypatch.setattr(
        ve, "_call_vision", lambda *a: json.dumps(payload)
    )
    extraction, hint = ve.extract_reaction_image(b"\x89PNG", "rxn.png")
    assert hint is None and extraction is not None
    assert len(extraction.reactions) == 2  # N.R. entry is NOT dropped
    nr = extraction.reactions[0]
    assert nr.no_reaction is True
    assert nr.confidence_score is None  # not scored: a valid negative result
    assert nr.confidence_flags == ["no_reaction_entry"]
    assert extraction.confidence_summary["outcomes"] == {
        "reactions": 1, "no_reaction": 1, "other": 0,
    }


def test_prompt_carries_footnote_and_nodrop_rules():
    prompt = ve._reaction_prompt()
    assert "footnotes" in prompt
    assert "N.R." in prompt
    assert "no_reaction" in prompt
    assert "N.R." in ve._REACTION_PROMPT_FALLBACK
    assert "footnotes" in ve._REACTION_PROMPT_FALLBACK
