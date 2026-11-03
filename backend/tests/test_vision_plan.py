"""Three-library B1 (rule version): planner, observer, failure isolation."""
from __future__ import annotations

import pytest

from app.services import vision_plan as vp
from app.services.vision_extract import VisionReaction, VisionReactionSide


def test_build_plan_single_path_is_current_behaviour():
    # Multi switch off by default: always the single reaction agent.
    assert vp.build_plan() == {"agents": ["reaction"]}
    assert vp.build_plan(has_table=True, has_rgroup_table=True) == {
        "agents": ["reaction"]
    }
    assert vp.build_plan(has_table=True, multi_enabled=True) == {
        "agents": ["reaction", "table"]
    }
    assert vp.build_plan(
        has_table=True, has_rgroup_table=True, multi_enabled=True
    ) == {"agents": ["reaction", "table", "rgroup"]}


def _rxn_with_stars(n):
    sides = [VisionReactionSide(smiles="*" * n, label="")]
    return VisionReaction(reactants=sides, products=[], conditions=[])


def test_rgroup_fallback_threshold():
    plan = vp.build_plan()
    assert vp.needs_rgroup_fallback([_rxn_with_stars(4)], plan) is False
    assert vp.needs_rgroup_fallback([_rxn_with_stars(5)], plan) is True
    # Plan already has an rgroup agent (multi on): no fallback.
    assert vp.needs_rgroup_fallback(
        [_rxn_with_stars(9)],
        vp.build_plan(has_rgroup_table=True, multi_enabled=True),
    ) is False


def test_contradicts_itself():
    assert vp.contradicts_itself(
        {"assertions": [("product", "CCO"), ("product", "CCN")]}
    ) is True
    assert vp.contradicts_itself(
        {"assertions": [("product", "CCO"), ("solvent", "THF")]}
    ) is False
    assert vp.contradicts_itself({}) is False


def test_filter_observer_issues():
    issues = [
        {"text": "ok", "assertions": [("a", "1")]},
        {"text": "self-contradictory", "assertions": [("a", "1"), ("a", "2")]},
    ]
    assert vp.filter_observer_issues(issues) == [issues[0]]


def test_main_chain_lock_with_fake_chem():
    class _Mol:
        def __init__(self, smi):
            self.smi = smi

        def HasSubstructMatch(self, query):
            return query.smi in self.smi

    class _Chem:
        @staticmethod
        def MolFromSmiles(smi):
            return None if smi == "INVALID" else _Mol(smi)

    chem = _Chem()
    assert vp.main_chain_consistent("CCCO", "CC", chem=chem) is True
    assert vp.main_chain_consistent("CCN", "CCO", chem=chem) is False
    assert vp.main_chain_consistent("INVALID", "CC", chem=chem) is False
    # No RDKit: fail-open, do not filter.
    assert vp.main_chain_consistent("CCN", "CCO", chem=None) is True
    issues = [
        {"text": "kept", "smiles": "CCCO"},
        {"text": "dropped", "smiles": "CCN"},
    ]
    kept = vp.filter_observer_issues(
        issues, main_chain_smiles="CCO", chem=chem
    )
    assert [i["text"] for i in kept] == ["kept"]


def test_run_agents_failure_isolation():
    def _ok():
        return "done"

    def _boom():
        raise ValueError("boom")

    results, failed = vp.run_agents([("a", _ok), ("b", _boom), ("c", _ok)])
    assert results == {"a": "done", "c": "done"}
    assert failed == ["b"]
    with pytest.raises(RuntimeError, match="all agents failed"):
        vp.run_agents([("a", _boom), ("b", _boom)])


def test_observer_user_message():
    msg = vp.observer_user_message([{"text": "check the solvent"}])
    assert "Observer notes:" in msg
    assert "check the solvent" in msg


def test_llm_gating_defaults_off():
    # Every gate needs conflict/trigger + switch + endpoint; any missing
    # piece keeps the rule version.
    assert vp.should_use_llm_planner(conflicts=True) is False
    assert vp.should_use_llm_planner(
        conflicts=True, llm_planner_enabled=True
    ) is False
    assert vp.should_use_llm_planner(
        conflicts=True, llm_planner_enabled=True, endpoint_available=True
    ) is True
    assert vp.should_use_llm_planner(
        conflicts=False, llm_planner_enabled=True, endpoint_available=True
    ) is False
    assert vp.should_use_llm_observer(unresolved=True) is False
    assert vp.should_use_llm_observer(
        unresolved=True, llm_observer_enabled=True, endpoint_available=True
    ) is True


def test_arbitrate_with_llm_falls_back():
    assert vp.arbitrate_with_llm(lambda: "x") is None
    assert vp.arbitrate_with_llm(
        lambda: "x", enabled=True, endpoint_available=True, trigger=True
    ) == "x"
    # LLM failure -> None, caller falls back to the rule version.
    def _boom():
        raise TimeoutError("slow")

    assert vp.arbitrate_with_llm(
        _boom, enabled=True, endpoint_available=True, trigger=True
    ) is None
