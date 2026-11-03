"""Three-library B2: R-group vocabulary + substitution."""
from __future__ import annotations

import json

import pytest

from app.services import rgroup as rg
from app.services import vision_extract as ve


def test_parse_definitions_explicit_only():
    mapping = rg.parse_rgroup_definitions("Ar = 4-OMeC6H4\nR = Me")
    assert mapping == {"Ar": "4-OMeC6H4", "R": "Me"}
    # No equation, no inference: a bare mention never becomes a mapping.
    assert rg.parse_rgroup_definitions("Ar appears in the products") == {}
    assert rg.parse_rgroup_definitions("") == {}


def test_substitute_bracketed_and_longest_first():
    new, flags = rg.substitute_rgroups("c1ccc([R1])cc1", {"R": "X", "R1": "CCO"})
    assert new == "c1ccc(CCO)cc1"
    assert flags == ["rgroup_string_fallback"]  # no chem -> string fallback


def test_substitute_bare_bounded_only():
    # Bare R bounded by non-alphanumerics substitutes; R inside OR / CR
    # is ambiguous with real atoms and is left alone.
    new, _ = rg.substitute_rgroups("C(R)O", {"R": "CC"})
    assert new == "C(CC)O"
    assert rg.substitute_rgroups("COR", {"R": "CC"})[0] == "COR"


def test_substitute_failure_keeps_original():
    class _FailChem:
        @staticmethod
        def MolFromSmiles(smi):
            return None if smi == "BAD" else object()

    new, flags = rg.substitute_rgroups("[R]", {"R": "BAD"}, chem=_FailChem())
    assert new == "[R]"
    assert flags == ["rgroup_substitution_failed"]


def test_graft_with_fake_chem():
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

    assert rg.graft_rgroup_fragment("c1ccc([*])cc1", "CCO", chem=_Chem()) == (
        "c1ccc(CCO)cc1"
    )
    assert rg.graft_rgroup_fragment("c1ccccc1", "CCO", chem=_Chem()) is None


def test_decompose_with_real_rdkit():
    pytest.importorskip("rdkit")
    rows = rg.decompose_rgroups(
        "c1ccc([*])cc1", ["c1ccc(C)cc1", "c1ccc(O)cc1"]
    )
    assert rows is not None
    assert len(rows) == 2
    assert sorted(row["R1"] for row in rows) == ["C[*:1]", "O[*:1]"]


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


def test_extract_wiring_substitutes_before_gate(monkeypatch, tmp_path):
    _vision_configured(monkeypatch, tmp_path)
    _fake_rdkit(monkeypatch)
    payload = {
        "reactions": [{
            "reactants": [{"smiles": "[Ar]", "label": ""}],
            "conditions": [],
            "products": [{"smiles": "CCN", "label": ""}],
            "confidence": 0.9,
        }],
        "notes": "",
    }
    monkeypatch.setattr(ve, "_call_vision", lambda *a: json.dumps(payload))
    extraction, hint = ve.extract_reaction_image(
        b"\x89PNG", "rxn.png", rgroup_mapping={"Ar": "CCO"}
    )
    assert hint is None and extraction is not None
    rxn = extraction.reactions[0]
    # Substituted, then verified + canonicalised by the gate (not trusted).
    assert rxn.reactants[0].smiles == "CCO"
    assert rxn.reactants[0].verified is True
    # No mapping -> unchanged behaviour.
    extraction2, _ = ve.extract_reaction_image(b"\x89PNG", "rxn.png")
    assert extraction2.reactions[0].reactants[0].smiles == "[Ar]"
