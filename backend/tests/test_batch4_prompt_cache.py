"""Batch 4 cluster 1: prompt_assembly (B11/B12/B13) + figure_cache (B14/B36/B37)."""
from __future__ import annotations

import pytest

from app.services import figure_cache as fc
from app.services import prompt_assembly as pa


def test_load_prompt_rejects_traversal():
    with pytest.raises(ValueError, match="escapes prompts dir"):
        pa.load_prompt("../../../../../../../../tmp/probe")
    with pytest.raises(ValueError, match="escapes prompts dir"):
        pa.load_prompt("subdir/../../etc/passwd")
    # Legitimate agents still resolve (file or inline fallback).
    assert pa.PLACEHOLDER in pa.load_prompt("planner")


def test_verify_prefilled_nonstring_candidate_is_missing_not_crash():
    original = {"s": "CCO"}
    for bad in (None, 42, ["CCO"], {"smiles": "CCO"}):
        updated, flags = pa.verify_prefilled_smiles(original, {"s": bad})
        assert updated["s"] == "None"
        assert flags == ["prefilled_smiles_changed:s"]


def test_figure_cache_kind_whitelist_blocks_traversal(tmp_path):
    cache = fc.FigureCache(tmp_path)
    assert cache.put("../../evil_kind", "k", {"x": 1}) is False
    assert cache.get("../../evil_kind", "k") is None
    assert not (tmp_path.parent / "evil_kind").exists()
    # Known kinds still work.
    assert cache.put(fc.FIGURE_KIND, "k", {"x": 1}) is True
    assert cache.get(fc.FIGURE_KIND, "k") == {"x": 1}


def test_figure_cache_put_rejects_nondict(tmp_path):
    cache = fc.FigureCache(tmp_path)
    assert cache.put(fc.FIGURE_KIND, "k1", [1, 2]) is False
    assert cache.get(fc.FIGURE_KIND, "k1") is None


def test_figure_cache_constructor_fails_open(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    cache = fc.FigureCache(blocker)  # must not raise
    assert cache.get(fc.FIGURE_KIND, "k") is None
    assert cache.put(fc.FIGURE_KIND, "k", {"x": 1}) is False
