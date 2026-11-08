"""Three-library B5: prompt organisation (one agent, one file).

Prompt files live in ``app/resources/prompts/<agent>.txt`` and follow the
MERMaid shape: header / instructions / tail, with a single
``<INSERT_HERE>`` placeholder where field-table values are injected.
Assembly filters the injected keys (the A4 optimisation-run field list is
the natural allowed set); the SMILES immutability rule is enforced twice:
in the prompt text itself, and by verify_prefilled_smiles after the call.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "resources" / "prompts"
PLACEHOLDER = "<INSERT_HERE>"

# Inline fallbacks when a prompt file is missing (same rules, condensed).
_FALLBACKS = {
    "planner": (
        "# Planner\n# Default to the reaction agent; add table / rgroup "
        "agents only when the input shape requires them.\n"
        "Pre-filled condition SMILES are copied verbatim; the only "
        "exception is adding or removing an ion charge.\n"
        f"{PLACEHOLDER}\n"
        "# Output: {\"agents\": [...]} as JSON.\n"
    ),
    "observer": (
        "# Observer\n# Drop self-contradictory issues; a claimed structure "
        "must contain the main chain. Suggestions only, never overrides.\n"
        f"{PLACEHOLDER}\n"
        "# Output: {\"issues\": [...]} as JSON.\n"
    ),
    "checker": (
        "# Checker\n# Answer yes / no / unsure per question key. "
        "Pre-filled condition SMILES are copied verbatim; the only "
        "exception is adding or removing an ion charge.\n"
        f"{PLACEHOLDER}\n"
        "# Output: {\"answers\": {...}} as JSON.\n"
    ),
}

_BRACKET_RE = re.compile(r"\[([^\]]*)\]")


def load_prompt(agent: str) -> str:
    """Prompt text for one agent: file first, inline fallback second.

    P1-B13 (hardening; audit 2026-11-05: no caller in app/ passes
    user input as ``agent`` — only assemble_prompt in this module
    calls it, and assemble_prompt itself has no app/ caller — but a
    traversal here would read arbitrary ``*.txt``). The resolved
    path must stay inside PROMPTS_DIR.
    """
    root = PROMPTS_DIR.resolve()
    path = (root / f"{agent}.txt").resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"prompt agent escapes prompts dir: {agent!r}")
    if path.is_file():
        return path.read_text(encoding="utf-8")
    if agent in _FALLBACKS:
        # P3-B26: a missing template used to fall back silently, so a
        # caller could not tell "deliberate fallback" from "template file
        # lost to drift". The fallback still applies, but now it logs.
        logger.warning(
            "prompt template for agent %r missing under %s; using inline fallback",
            agent, root,
        )
        return _FALLBACKS[agent]
    raise ValueError(f"unknown prompt agent: {agent!r}")


def assemble_prompt(
    agent: str,
    values: dict,
    *,
    allowed_keys: list[str] | None = None,
) -> str:
    """Inject ``key: value`` lines for the allowed keys present in values
    at the <INSERT_HERE> placeholder. Raises when the template has no
    placeholder (a template bug should fail loud, not silently drop the
    field table)."""
    text = load_prompt(agent)
    if PLACEHOLDER not in text:
        raise ValueError(f"prompt template for {agent!r} has no {PLACEHOLDER}")
    keys = allowed_keys if allowed_keys is not None else list(values)
    lines = [f"{key}: {values[key]}" for key in keys if key in values]
    return text.replace(PLACEHOLDER, "\n".join(lines), 1)


def _strip_ion_marks(smiles: str) -> str:
    """Remove +/- inside brackets only (single-bond '-' outside brackets
    is a bond symbol and is kept)."""
    return _BRACKET_RE.sub(lambda m: "[" + m.group(1).replace("+", "").replace("-", "") + "]", smiles)


def verify_prefilled_smiles(
    original: dict[str, str], updated: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Post-call immutability check for pre-filled condition SMILES.

    A pre-filled value must reappear verbatim in the updated text, except
    for ion charge-mark changes (compared after stripping bracket
    charges). Any other change — including a missing key, a non-string
    candidate (LLM null/number/list), extending the chain, or adding a
    whole ion — is replaced with "None" and flagged
    ``prefilled_smiles_changed:<key>``. Keys without a pre-filled
    value pass through untouched.

    P1-B11 (2026-11-04 scheme behavior change): the comparison is
    full-string equality after charge stripping, NOT substring
    containment — substring let the LLM extend the chain (CCO ->
    CCOCC) or append a whole atom ([Na+]CCO) without a flag.
    P1-B12: a non-string candidate counts as missing, never crashes.
    """
    out = dict(updated)
    flags: list[str] = []
    for key, prefilled in (original or {}).items():
        if not isinstance(prefilled, str) or not prefilled:
            continue
        candidate = out.get(key, "")
        if not isinstance(candidate, str):
            candidate = ""
        if _strip_ion_marks(prefilled) == _strip_ion_marks(candidate):
            continue
        out[key] = "None"
        flags.append(f"prefilled_smiles_changed:{key}")
    return out, flags
