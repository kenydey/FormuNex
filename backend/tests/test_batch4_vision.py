"""Batch 4 cluster 3: vision_extract, chem_extract, hybrid breaker."""
from __future__ import annotations

import json

import pytest

from app.config import get_settings
from app.services import chem_extract as cx
from app.services import hybrid_parse as hp
from app.services import vision_extract as ve
from app.services.mineru_cloud import MinerUBlock


def test_b2_deepseek_flash_is_vision():
    assert ve._is_deepseek_vision("deepseek-flash") is True
    assert ve._is_deepseek_vision("deepseek-flash-2026") is True
    assert ve._is_deepseek_vision("deepseek-v4-flash-vision-exp") is True
    assert ve._is_deepseek_vision("deepseek-chat") is False
    assert ve._is_deepseek_vision("deepseek-reasoner") is False


def test_b2_two_json_blocks_first_parseable_wins():
    raw = '{"a": 1} and also {"reactions": []}'
    out = ve._strip_json_fences(raw)
    assert json.loads(out) == {"a": 1}
    # Braces inside strings do not confuse the scan.
    raw2 = 'pre {"note": "a } b", "x": 1} tail {"y": 2}'
    assert json.loads(ve._strip_json_fences(raw2)) == {"note": "a } b", "x": 1}
    # Plain JSON passes through untouched.
    assert ve._strip_json_fences('{"k": true}') == '{"k": true}'


def test_b3_account_mention_alone_is_not_permanent():
    assert ve._is_permanent_vision_failure("check your account settings hint") is False
    assert ve._is_permanent_vision_failure("Your account org-x is suspended") is True
    assert ve._is_permanent_vision_failure("401 unauthorized") is True
    assert ve._is_permanent_vision_failure("timeout after 30s") is False


def test_b3_exception_path_counts_and_quota_honours_budget(monkeypatch, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="app.services.hybrid_parse")
    hp._vision_consecutive_failures.set(0)
    try:
        block = MinerUBlock(type="image", image=b"fake")
        calls = {"n": 0}

        def _raise(image, name):
            calls["n"] += 1
            raise RuntimeError("endpoint is paused")

        out = hp._visual_markdown(
            block, "p.1", True, "", _raise, lambda ext, label: "md"
        )
        assert "视觉解析异常" in out
        assert hp._vision_consecutive_failures.get() == 1
        assert calls["n"] == 1

        # Quota exhaustion jumps straight to the budget (breaker opens).
        settings = get_settings()
        monkeypatch.setattr(settings, "vision_max_consecutive_failures", 3)
        hp._count_vision_failure("insufficient_quota", "p.2")
        assert hp._vision_consecutive_failures.get() >= 3
        assert "breaker opened immediately" in caplog.text

        # Budget 0 = breaker disabled: the counter moves by one only,
        # and the log must not claim the breaker opened.
        hp._vision_consecutive_failures.set(0)
        monkeypatch.setattr(settings, "vision_max_consecutive_failures", 0)
        caplog.clear()
        hp._count_vision_failure("insufficient_quota", "p.3")
        assert hp._vision_consecutive_failures.get() == 1
        assert "breaker disabled" in caplog.text
        assert "breaker opened immediately" not in caplog.text
    finally:
        hp._vision_consecutive_failures.set(0)


def test_b5_inheritance_copies_and_resets_at_table_boundary():
    def _rxn(entry, conds):
        return ve.VisionReaction(
            entry=entry,
            conditions=[ve.VisionReactionCondition(role=r, text=t) for r, t in conds],
        )

    row1 = _rxn("1", [("solvent", "THF")])
    row2 = _rxn("2", [("solvent", "同上")])
    out = ve.apply_condition_inheritance([row1, row2])
    assert out[1].conditions[0].text == "THF"
    # Inherited conditions are copies: mutating one row must not rewrite another.
    out[1].conditions[0].text = "MUTATED"
    assert out[0].conditions[0].text == "THF"

    # A second table reusing entry "1" does not inherit table 1's solvent.
    table1 = [_rxn("1", [("solvent", "THF")]), _rxn("2", [("solvent", "同上")])]
    table2 = [_rxn("1", [("solvent", "同上")])]
    out = ve.apply_condition_inheritance(table1 + table2)
    assert out[2].conditions[0].text == "同上"


def test_b4_round_robin_keeps_smiles_and_reactions(monkeypatch):
    # The old fixed-order cap (CAS -> formula -> SMILES -> reaction)
    # dropped every SMILES once 20 CAS + 5 formulas filled the 25 cap.
    monkeypatch.setattr(cx, "extract_cas", lambda text: [f"CAS-{i}" for i in range(20)])
    monkeypatch.setattr(cx, "extract_formulas", lambda text: [f"F{i}" for i in range(20)])
    monkeypatch.setattr(
        cx, "extract_smiles",
        lambda text: [{"canonical": "c1ccccc1", "raw": "c1ccccc1"}],
    )
    monkeypatch.setattr(
        cx, "extract_reactions",
        lambda text: [
            {"raw": "A>>B", "reactants": ["A"], "products": ["B"]},
            {"raw": "C>>D", "reactants": ["C"], "products": ["D"]},
        ],
    )
    monkeypatch.setattr(cx, "extract_products", lambda text: [])
    meta = cx.extract_entities("x")
    assert meta is not None
    types = [c["type"] for c in meta["chem"]]
    assert len(meta["chem"]) <= 25
    assert "smiles" in types
    assert types.count("reaction") == 2
