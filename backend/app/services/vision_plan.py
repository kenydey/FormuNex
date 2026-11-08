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


def _side_smiles(reaction, key: str) -> list[str]:
    """Side SMILES for one reaction side, dict or object shaped.

    P2-B31: needs_rgroup_fallback used getattr only, so dict-shaped
    reactions never counted a wildcard. Sides may be strings, dicts
    with a "smiles" key, or objects with a .smiles attribute.
    """
    if isinstance(reaction, dict):
        values = reaction.get(key, []) or []
    else:
        values = getattr(reaction, key, []) or []
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for side in values:
        if isinstance(side, str):
            out.append(side)
        elif isinstance(side, dict):
            out.append(side.get("smiles") or "")
        else:
            out.append(getattr(side, "smiles", "") or "")
    return [s for s in out if s]


def needs_rgroup_fallback(
    reactions: list, plan: dict, *, threshold: int = RGROUP_FALLBACK_THRESHOLD
) -> bool:
    """Re-run with the rgroup agent when the output carries wildcards and
    the plan has no rgroup agent. Below the threshold the wildcards are
    treated as marking noise, not an R-group table.

    P3-B23: the threshold applies to the TOTAL across all reactions in
    the output, not per reaction — five reactions with one wildcard each
    trip a threshold of 5. (Deliberate: a scattered wildcard pattern
    across a table is still an R-group table signal.)
    """
    if AGENT_RGROUP in (plan or {}).get("agents", []):
        return False
    total = 0
    for rxn in reactions or []:
        total += count_wildcards(
            _side_smiles(rxn, "reactants") + _side_smiles(rxn, "products")
        )
    return total >= threshold


def contradicts_itself(issue: dict) -> bool:
    """An issue contradicts itself when one key carries two different
    values in its assertions: [(key, value), ...].

    P2-B32: a malformed assertion (not a 2-tuple) counts as
    self-contradictory — the issue is dropped rather than crashing the
    filter. A non-dict issue has no assertions and never contradicts
    itself (see P2-B33).
    """
    if not isinstance(issue, dict):
        return False
    seen: dict[str, str] = {}
    for item in (issue.get("assertions", []) or []):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return True
        key, value = item
        if key in seen and seen[key] != value:
            return True
        seen[key] = value
    return False


def main_chain_consistent(
    claim_smiles: str, main_chain_smiles: str, *, chem=None, notes: list | None = None
) -> bool:
    """Main-chain lock: a claimed structure must contain the main chain
    as a substructure (RDKit HasSubstructMatch). Fail-open when RDKit is
    missing (True: do not filter); an unparseable claim is inconsistent
    (False: filter it out).

    P2-B34: the fail-open case is no longer silent — when chem is
    missing and a notes list is supplied, the reason is appended so the
    caller can surface "lock not in effect" instead of assuming it ran.
    """
    if chem is None:
        if notes is not None:
            notes.append("main_chain_lock_inactive_no_rdkit")
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
        # P2-B33: a non-dict issue carries no assertions and no smiles
        # to lock — it passes through (observer_user_message renders it)
        # instead of crashing on .get.
        claim = issue.get("smiles") if isinstance(issue, dict) else None
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
    # P3-B24: duplicate names used to silently overwrite earlier results
    # (and skew the failed count). Fail fast instead — no app caller
    # passes duplicates today, so this only fires on a wiring bug.
    _names = [name for name, _ in agent_fns]
    if len(set(_names)) != len(_names):
        raise ValueError(f"duplicate agent names: {sorted(set(n for n in _names if _names.count(n) > 1))}")
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
        # P1-B21: the str fallback was unreachable — .get crashed first.
        # Non-dict issues render as their own text.
        if isinstance(issue, dict):
            text = issue.get("text") or issue.get("claim") or str(issue)
        else:
            text = str(issue)
        lines.append(f"- {text}")
    return "\n".join(lines)
