"""Vision-LLM reaction extraction: structured reaction JSON + RDKit gate.

Covers the OCSR follow-up slice: reaction scheme image → reactions with
roles (reactants / conditions / products) + locally assembled reaction SMILES.
The vision call itself is stubbed; what is tested is schema parsing, the RDKit
gate, reaction-SMILES assembly, and degradation — no live endpoint, no key.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from app.config import get_settings
from app.services import vision_extract
from app.services.runtime_secrets import get_runtime_secrets

_ENV_KEYS = (
    "FORMUNEX_LLM_PROVIDER",
    "FORMUNEX_LLM_BASE_URL",
    "FORMUNEX_VISION_PROVIDER",
    "FORMUNEX_VISION_MODEL",
    "FORMUNEX_VISION_BASE_URL",
    "FORMUNEX_VISION_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("FORMUNEX_ENV_FILE", str(env_file))
    rs = get_runtime_secrets()
    rs.clear()
    get_settings.cache_clear()
    yield
    rs.clear()
    get_settings.cache_clear()


def _vision_configured(monkeypatch):
    """Custom vision endpoint + fake openai SDK so vision_available() passes."""
    rs = get_runtime_secrets()
    rs.set("vision_provider", "custom")
    rs.set("vision_model", "test-vlm")
    rs.set("vision_api_key", "test-key")
    rs.set("vision_base_url", "https://vlm.example.test/v1")
    mod = types.ModuleType("openai")
    mod.OpenAI = object
    monkeypatch.setitem(sys.modules, "openai", mod)


def _fake_rdkit(monkeypatch):
    """Minimal RDKit stub: valid SMILES pass through, 'INVALID' fails."""

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


_REACTION_JSON = {
    "reactions": [
        {
            "reactants": [{"smiles": "c1ccccc1", "label": "benzene"}],
            "conditions": [
                {"role": "reagent", "text": "Cl2"},
                {"role": "catalyst", "text": "FeCl3"},
                {"role": "temperature", "text": "25 C"},
            ],
            "products": [{"smiles": "Clc1ccccc1", "label": "chlorobenzene"}],
            "confidence": 0.9,
        }
    ],
    "notes": "",
}


def test_valid_reaction_assembles_reaction_smiles(monkeypatch):
    _vision_configured(monkeypatch)
    _fake_rdkit(monkeypatch)
    monkeypatch.setattr(
        vision_extract, "_call_vision", lambda *a: json.dumps(_REACTION_JSON)
    )
    extraction, hint = vision_extract.extract_reaction_image(b"\x89PNG", "rxn.png")
    assert hint is None and extraction is not None
    assert len(extraction.reactions) == 1
    rxn = extraction.reactions[0]
    assert rxn.reaction_smiles == "c1ccccc1>>Clc1ccccc1"
    assert rxn.reactants[0].verified is True
    assert rxn.products[0].verified is True
    assert rxn.confidence == 0.9
    assert [c.role for c in rxn.conditions] == ["reagent", "catalyst", "temperature"]


def test_invalid_side_smiles_excluded_and_confidence_capped(monkeypatch):
    _vision_configured(monkeypatch)
    _fake_rdkit(monkeypatch)
    payload = json.loads(json.dumps(_REACTION_JSON))
    payload["reactions"][0]["products"] = [{"smiles": "INVALID", "label": "???"}]
    monkeypatch.setattr(vision_extract, "_call_vision", lambda *a: json.dumps(payload))
    extraction, hint = vision_extract.extract_reaction_image(b"\x89PNG", "rxn.png")
    assert hint is None and extraction is not None
    rxn = extraction.reactions[0]
    assert rxn.products[0].verified is False
    assert rxn.reaction_smiles == "c1ccccc1>>"
    assert rxn.confidence == 0.3


def test_malformed_model_json_degrades_without_raising(monkeypatch):
    _vision_configured(monkeypatch)
    monkeypatch.setattr(vision_extract, "_call_vision", lambda *a: "not json at all")
    extraction, hint = vision_extract.extract_reaction_image(b"\x89PNG", "rxn.png")
    assert extraction is None
    assert hint


def test_unconfigured_vision_returns_hint(monkeypatch):
    mod = types.ModuleType("openai")
    mod.OpenAI = object
    monkeypatch.setitem(sys.modules, "openai", mod)
    extraction, hint = vision_extract.extract_reaction_image(b"\x89PNG", "rxn.png")
    assert extraction is None
    assert hint


def test_reaction_prompt_loads_from_file():
    prompt = vision_extract._reaction_prompt()
    assert "reactions" in prompt
    assert "reagent" in prompt
