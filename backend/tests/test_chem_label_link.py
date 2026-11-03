"""Three-library A3: compound-label linking + OCR confusable repair.

Pure rules in chem_extract, opt-in wiring in extract_entities and
extract_reaction_image (default behaviour unchanged).
"""
from __future__ import annotations

import json

import pytest

from app.services import chem_extract as ce
from app.services import vision_extract


@pytest.mark.parametrize("token,prev,expected", [
    ("3a", "", True),
    ("12b", "from", True),
    ("3a", "compound", True),
    ("12", "compound", True),
    ("12", "intermediate", True),
    ("4", "", False),          # bare digit, no compound context
    ("4", "step", False),      # prose reference
    ("3a", "table", False),    # Table 3a is a table, not a compound
    ("2024", "", False),       # year
    ("Product", "", False),    # no digits at all
    ("A", "", False),
])
def test_is_label_like(token, prev, expected):
    assert ce.is_label_like(token, preceding_word=prev) is expected


def test_repair_candidates_preserve_real_digits():
    # OCR of "12g" is "129": single-position repair recovers "12g".
    # Full repair ("l2g") would corrupt the real leading "1" — it is only
    # a last-resort candidate and never wins when a single repair hits.
    cands = ce._repair_candidates("129")
    assert cands[0] == "129"
    assert "12g" in cands
    assert cands.index("12g") < cands.index("l2g")
    assert ce.repair_ocr_confusables("129") == "l2g"


def test_link_labels_guards_and_ocr():
    text = (
        "Compound 3a was obtained from 3b in 85% yield. "
        "See step 4 and Table 3a. The product 129 was isolated in 2024."
    )
    links = ce.link_labels(text, {"3a": "CCO", "3b": "CCN", "12g": "CCC"})
    by_label = {(lnk["label"], lnk["canonical_label"]): lnk for lnk in links}
    assert ("3a", "3a") in by_label
    assert by_label[("3a", "3a")]["smiles"] == "CCO"
    assert ("3b", "3b") in by_label
    # OCR'd "129" links to known label "12g"; step 4 / Table 3a / 2024 do not.
    assert ("129", "12g") in by_label
    assert len(links) == 3
    assert all(lnk["label"] not in ("4", "2024") for lnk in links)


def test_link_labels_empty_without_map():
    assert ce.link_labels("Compound 3a", {}) == []
    assert ce.link_labels("", {"3a": "CCO"}) == []


def test_extract_entities_label_links_opt_in():
    text = "Compound 3a was prepared."
    assert ce.extract_entities(text) is None or "label_links" not in (
        ce.extract_entities(text) or {}
    )
    meta = ce.extract_entities(text, label_to_smiles={"3a": "CCO"})
    assert meta is not None
    assert meta["label_links"][0]["canonical_label"] == "3a"
    assert meta["label_links"][0]["smiles"] == "CCO"


def test_resolve_side_label():
    mapping = {"3a": "CCO", "12": "CCN", "12g": "CCC"}
    assert ce.resolve_side_label("3a", mapping) == "CCO"
    assert ce.resolve_side_label("compound 3a", mapping) == "CCO"
    assert ce.resolve_side_label("12", mapping) == "CCN"
    assert ce.resolve_side_label("Product A", mapping) is None
    assert ce.resolve_side_label("step 4", mapping) is None
    assert ce.resolve_side_label("3b", mapping) is None
    assert ce.resolve_side_label("129", mapping) == "CCC"  # OCR repair


def _vision_configured(monkeypatch):
    import sys
    import types

    from app.config import get_settings
    from app.services.runtime_secrets import get_runtime_secrets

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


def test_vision_side_label_resolution(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.setenv("FORMUNEX_ENV_FILE", str(tmp_path / ".env"))
    _vision_configured(monkeypatch)
    _fake_rdkit(monkeypatch)
    payload = {
        "reactions": [{
            "reactants": [{"smiles": "", "label": "3a"}],
            "conditions": [],
            "products": [{"smiles": "", "label": "Product A"}],
            "confidence": 0.9,
        }],
        "notes": "",
    }
    monkeypatch.setattr(
        vision_extract, "_call_vision", lambda *a: json.dumps(payload)
    )
    extraction, hint = vision_extract.extract_reaction_image(
        b"\x89PNG", "rxn.png", label_to_smiles={"3a": "CCO"}
    )
    assert hint is None and extraction is not None
    rxn = extraction.reactions[0]
    # Resolved + RDKit-verified through the gate (not blindly trusted).
    assert rxn.reactants[0].smiles == "CCO"
    assert rxn.reactants[0].verified is True
    assert rxn.reaction_smiles == "CCO>>"
    # Prose label is not resolved.
    assert rxn.products[0].smiles == ""
    assert rxn.products[0].verified is False


def test_vision_no_map_unchanged(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.setenv("FORMUNEX_ENV_FILE", str(tmp_path / ".env"))
    _vision_configured(monkeypatch)
    _fake_rdkit(monkeypatch)
    payload = {
        "reactions": [{
            "reactants": [{"smiles": "", "label": "3a"}],
            "conditions": [],
            "products": [{"smiles": "CCN", "label": ""}],
            "confidence": 0.9,
        }],
        "notes": "",
    }
    monkeypatch.setattr(
        vision_extract, "_call_vision", lambda *a: json.dumps(payload)
    )
    extraction, hint = vision_extract.extract_reaction_image(b"\x89PNG", "rxn.png")
    assert hint is None and extraction is not None
    assert extraction.reactions[0].reactants[0].smiles == ""
