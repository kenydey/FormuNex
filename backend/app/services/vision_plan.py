"""Three-library B1 (rule version): Planner + Observer + failure isolation.

Pure orchestration helpers for vision / structure recognition. The rule
Planner is the default; LLM Planner / Observer live behind the gating
predicates in the B1-LLM step and are OFF by default. Nothing here calls
a model: agent functions are injected by the caller (see run_agents),
so every rule is testable with fake callables.
"""
from __future__ import annotations

# R-group fallback: re-run with the rgroup agent when the output carries
# at least this many wildcard atoms and the plan has no rgroup agent.
RGROUP_FALLBACK_THRESHOLD = 5

AGENT_REACTION = "reaction"
AGENT_TABLE = "table"
AGENT_RGROUP = "rgroup"


def build_plan(
    *,
    has_table: bool = False,
    has_rgroup_table: bool = False,
    multi_enabled: bool = False,
) -> dict:
    """Rule Planner: input shape -> agent list. No model call.

    orchestration_multi_enabled (multi_enabled) is OFF by default: the
    plan is exactly the current single-path call (reaction agent only).
    With multi on, table / R-group-table inputs add agents.
    """
    agents = [AGENT_REACTION]
    if multi_enabled:
        if has_table:
            agents.append(AGENT_TABLE)
        if has_rgroup_table:
            agents.append(AGENT_RGROUP)
    return {"agents": agents}


def should_use_llm_planner(
    *,
    conflicts: bool,
    llm_planner_enabled: bool = False,
    endpoint_available: bool = False,
) -> bool:
    """LLM Planner arbitration, gated: only when multi-path results
    conflict AND the switch is on AND an LLM endpoint is available.
    Otherwise the rule Planner stands. An LLM conclusion never overrides
    the RDKit gate or the 0.3 confidence cap (those run downstream)."""
    return bool(conflicts and llm_planner_enabled and endpoint_available)


def should_use_llm_observer(
    *,
    unresolved: bool,
    llm_observer_enabled: bool = False,
    endpoint_available: bool = False,
) -> bool:
    """LLM Observer suggestions, gated: only when rule Observer issues
    remain unresolved AND the switch is on AND an endpoint is available.
    Suggestions only — they never rewrite reactions."""
    return bool(unresolved and llm_observer_enabled and endpoint_available)


def arbitrate_with_llm(
    llm_call,
    *,
    enabled: bool = False,
    endpoint_available: bool = False,
    trigger: bool = False,
):
    """Call an injected LLM callable only when enabled + endpoint +
    trigger all hold; any LLM failure / timeout returns None so the
    caller falls back to the rule version immediately."""
    if not (enabled and endpoint_available and trigger):
        return None
    try:
        return llm_call()
    except Exception:
        return None


def count_wildcards(side_smiles_list: list[str]) -> int:
    """Wildcard atoms ('*') across side SMILES strings."""
    return sum((smi or "").count("*") for smi in side_smiles_list)


def needs_rgroup_fallback(
    reactions: list, plan: dict, *, threshold: int = RGROUP_FALLBACK_THRESHOLD
) -> bool:
    """Re-run with the rgroup agent when the output carries wildcards and
    the plan has no rgroup agent. Below the threshold the wildcards are
    treated as marking noise, not an R-group table."""
    if AGENT_RGROUP in (plan or {}).get("agents", []):
        return False
    total = 0
    for rxn in reactions or []:
        sides = list(getattr(rxn, "reactants", []) or []) + list(
            getattr(rxn, "products", []) or []
        )
        total += count_wildcards([getattr(s, "smiles", "") or "" for s in sides])
    return total >= threshold


def contradicts_itself(issue: dict) -> bool:
    """An issue contradicts itself when one key carries two different
    values in its assertions: [(key, value), ...]."""
    seen: dict[str, str] = {}
    for key, value in (issue or {}).get("assertions", []) or []:
        if key in seen and seen[key] != value:
            return True
        seen[key] = value
    return False


def main_chain_consistent(
    claim_smiles: str, main_chain_smiles: str, *, chem=None
) -> bool:
    """Main-chain lock: a claimed structure must contain the main chain
    as a substructure (RDKit HasSubstructMatch). Fail-open when RDKit is
    missing (True: do not filter); an unparseable claim is inconsistent
    (False: filter it out)."""
    if chem is None:
        return True
    try:
        claim = chem.MolFromSmiles(claim_smiles or "")
        chain = chem.MolFromSmiles(main_chain_smiles or "")
        if claim is None or chain is None:
            return False
        return bool(claim.HasSubstructMatch(chain))
    except Exception:
        return False


def filter_observer_issues(
    issues: list[dict], *, main_chain_smiles: str | None = None, chem=None
) -> list[dict]:
    """Observer filter: drop self-contradictory issues, then (when a main
    chain is given) drop claims failing the main-chain lock. Order and
    surviving issues are preserved."""
    out: list[dict] = []
    for issue in issues or []:
        if contradicts_itself(issue):
            continue
        claim = issue.get("smiles")
        if (
            claim
            and main_chain_smiles
            and not main_chain_consistent(claim, main_chain_smiles, chem=chem)
        ):
            continue
        out.append(issue)
    return out


def run_agents(agent_fns: list[tuple[str, object]]) -> tuple[dict, list[str]]:
    """Failure isolation: run each (name, callable) agent in order.

    A single agent's exception drops only that agent's contribution and
    records its name in failed_agents; only when EVERY agent fails is a
    RuntimeError raised. Results map name -> return value.
    """
    results: dict = {}
    failed: list[str] = []
    for name, fn in agent_fns:
        try:
            results[name] = fn()
        except Exception:
            failed.append(name)
    if agent_fns and len(failed) == len(agent_fns):
        raise RuntimeError(f"all agents failed: {failed}")
    return results, failed


def observer_user_message(issues: list[dict]) -> str:
    """Observer issues as a prepended user message for final synthesis.
    Included whether or not a re-run was triggered."""
    lines = ["Observer notes:"]
    for issue in issues or []:
        text = issue.get("text") or issue.get("claim") or str(issue)
        lines.append(f"- {text}")
    return "\n".join(lines)
