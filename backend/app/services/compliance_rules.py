"""Compliance screening — RoHS heavy metals and EU REACH SVHC candidates.

Deterministic, zero-LLM rule engine built on the raw-material catalog plus a
curated name/CAS list. Used by the recommendation ranker (soft penalty) and
the DOE generator (hard infeasible gate for definite heavy-metal hits).

Design
------
* **RoHS restricted substances** (EU Directive 2011/65/EU): Pb, Cd, Hg, Cr(VI)
  (plus PBB/PBDE — not represented in the coating-material catalog). A
  formulation ingredient that matches one of these is a *hard* compliance
  violation → ``infeasible`` in the DOE gate, and a strong penalty + warning
  in the recommend path.
* **SVHC candidates** (REACH candidate list): broader, advisory list. Matches
  are ``warn``-level — the chemist must confirm regulatory status before
  commercialisation.
* Every entry carries a CAS number (for exact matching) and/or name patterns
  (for catalog names where CAS is absent). Sources are noted in comments.
* Unknown materials are never penalised — only what the list actually knows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..domain.knowledge import RAW_MATERIALS
from ..domain.schemas import Formulation

logger = logging.getLogger(__name__)

# ── RoHS restricted substances relevant to metal-treatment coatings ─────────
# EU Directive 2011/65/EU (RoHS), Annex II — max concentration 0.1 wt% (Cd 0.01%).

# (catalog-material name prefix / exact, CAS, display name)
_ROHS_LEAD = (
    ("Lead oxide", "1317-36-8", "PbO 氧化铅"),
    ("Lead carbonate", "598-63-0", "碱式碳酸铅"),
    ("Red lead", "1314-41-6", "红丹"),
    ("Lead chromate", "7758-97-6", "铅铬黄"),
)
_ROHS_CADMIUM = (
    ("Cadmium", "7440-43-9", "镉"),
    ("Cadmium oxide", "1306-19-0", "氧化镉"),
)
_ROHS_MERCURY = (
    ("Mercury", "7439-97-6", "汞"),
    ("Mercuric oxide", "21908-53-2", "氧化汞"),
)
_ROHS_CHROMIUM6 = (
    ("Chromium trioxide", "1333-82-0", "CrO₃ 铬酸酐"),
    ("Sodium chromate", "7775-11-3", "铬酸钠"),
    ("Potassium dichromate", "7778-50-9", "重铬酸钾"),
    ("Strontium chromate", "7789-06-2", "锶铬黄"),
    ("Zinc chromate", "13530-65-9", "锌铬黄"),
    ("Barium chromate", "10294-40-3", "钡铬黄"),
)

# ── REACH SVHC candidates (expanded from the legacy 3-name list) ────────────
# EU REACH Article 57 / Candidate List — advisory; confirm regulatory status.
_SVHC = (
    ("Zinc molybdate", "13767-32-3", "钼酸锌（SVHC 候选）"),
    ("Cerium nitrate", "7789-18-6", "硝酸铈（SVHC 候选）"),
    ("Sodium nitrite", "7632-00-0", "亚硝酸钠（SVHC 候选）"),
    ("Boric acid", "10043-35-3", "硼酸（SVHC 候选）"),
    ("Cobalt(II) chloride", "7646-79-9", "氯化钴（SVHC 候选）"),
    ("Cobalt sulfate", "10124-43-3", "硫酸钴（SVHC 候选）"),
    ("NMP", "872-50-4", "N-甲基吡咯烷酮（SVHC 候选）"),
    ("Diisobutyl phthalate", "84-69-5", "DIBP 邻苯二甲酸二异丁酯（SVHC 候选）"),
)

_ROHS_BY_CAS: dict[str, str] = {}
_SVHC_BY_CAS: dict[str, str] = {}
_ROHS_BY_NAME: dict[str, str] = {}
_SVHC_BY_NAME: dict[str, str] = {}
# P1-C12: RoHS Annex II limits — 0.1 wt% generally, 0.01 wt% for cadmium.
# (The matched display label alone does not say which family it is, so
# limits ride in parallel maps built from the same tuples.)
_ROHS_LIMIT_BY_CAS: dict[str, float] = {}
_ROHS_LIMIT_BY_NAME: dict[str, float] = {}


def _build_index() -> None:
    for name, cas, display in _ROHS_LEAD + _ROHS_CADMIUM + _ROHS_MERCURY + _ROHS_CHROMIUM6:
        _ROHS_BY_CAS[cas] = display
        _ROHS_BY_NAME[name.lower()] = display
    for name, cas, display in _ROHS_CADMIUM:
        _ROHS_LIMIT_BY_CAS[cas] = 0.01
        _ROHS_LIMIT_BY_NAME[name.lower()] = 0.01
    for name, cas, display in _ROHS_LEAD + _ROHS_MERCURY + _ROHS_CHROMIUM6:
        _ROHS_LIMIT_BY_CAS[cas] = 0.1
        _ROHS_LIMIT_BY_NAME[name.lower()] = 0.1
    for name, cas, display in _SVHC:
        _SVHC_BY_CAS[cas] = display
        _SVHC_BY_NAME[name.lower()] = display


_build_index()


@dataclass
class ComplianceResult:
    compliant: bool
    status: str = "pass"  # pass | warn | infeasible
    reasons: list[str] = field(default_factory=list)
    rohs_hits: list[str] = field(default_factory=list)
    svhc_hits: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.compliant


def _match_ingredient(
    name: str,
    cas_no: str | None,
    by_cas: dict[str, str],
    by_name: dict[str, str],
) -> str | None:
    """Return the display label when the ingredient matches, else None."""
    if cas_no:
        hit = by_cas.get(cas_no.strip())
        if hit:
            return hit
    return by_name.get((name or "").strip().lower())


def check_compliance(form: Formulation) -> ComplianceResult:
    """Deterministic compliance screen for a formulation.

    RoHS restricted-substance matches are hard violations (infeasible);
    SVHC candidate matches are advisory (warn).
    """
    reasons: list[str] = []
    rohs_hits: list[str] = []
    rohs_below_limit: list[str] = []
    svhc_hits: list[str] = []
    suspected: list[str] = []

    for ing in form.ingredients:
        if ing.weight_pct <= 0:
            continue

        rohs = _match_ingredient(ing.name, ing.cas_no, _ROHS_BY_CAS, _ROHS_BY_NAME)
        if rohs:
            # P1-C12: the old code flagged ANY RoHS match as infeasible,
            # contradicting its own "0.1 wt% (Cd 0.01%)" header. Below the
            # limit it is a warning, not a hard gate. Note the basis caveat:
            # formulation content is compared here, while RoHS legally
            # applies to homogeneous materials — that basis question is
            # still with EHS (see deep-review §7.2).
            limit = None
            if ing.cas_no:
                limit = _ROHS_LIMIT_BY_CAS.get(ing.cas_no.strip())
            if limit is None:
                limit = _ROHS_LIMIT_BY_NAME.get((ing.name or "").strip().lower(), 0.1)
            label = f"{ing.name}（{rohs}）"
            if ing.weight_pct >= limit:
                if label not in rohs_hits:
                    rohs_hits.append(label)
                reason = f"{ing.name}: 检出 RoHS 受限物质 {rohs}（含量 {ing.weight_pct:g}% ≥ 限值 {limit:g}%）"
                if reason not in reasons:
                    reasons.append(reason)
            else:
                if label not in rohs_below_limit:
                    rohs_below_limit.append(label)
                reason = (f"{ing.name}: 检出 RoHS 受限物质 {rohs}，含量 {ing.weight_pct:g}% "
                          f"低于限值 {limit:g}%（按配方含量计；均质材料口径待 EHS 确认），仅提示")
                if reason not in reasons:
                    reasons.append(reason)
            continue

        svhc = _match_ingredient(ing.name, ing.cas_no, _SVHC_BY_CAS, _SVHC_BY_NAME)
        if svhc:
            label = f"{ing.name}（{svhc}）"
            if label not in svhc_hits:
                svhc_hits.append(label)
            reason = f"{ing.name}: REACH SVHC 候选 {svhc}，商业化前需确认合规状态"
            if reason not in reasons:
                reasons.append(reason)
            continue

        # Catalog metadata flag (legacy svhc: true entries) — only as a
        # fallback when the curated lists do not already cover the material.
        spec = RAW_MATERIALS.get(ing.name, {})
        if spec.get("svhc"):
            label = f"{ing.name}（REACH SVHC 候选）"
            if label not in svhc_hits:
                svhc_hits.append(label)
            reason = f"{ing.name}: REACH SVHC 候选，商业化前需确认合规状态"
            if reason not in reasons:
                reasons.append(reason)
            continue

        # P2-C14: no exact match — but the ingredient name EMBEDS a full
        # curated restricted name ("Lead oxide dispersion", a no-CAS
        # "Red lead primer"). That is a possible match, downgraded to
        # warn: never a hard gate on a substring, never silently passed.
        # A loose single-word stem rule (e.g. any "zinc…") is NOT applied
        # here: it would false-positive on benign Zinc oxide and needs
        # an EHS-approved stem list first.
        lowered = (ing.name or "").strip().lower()
        sus = None
        for key, display in _ROHS_BY_NAME.items():
            if key in lowered:
                sus = f"疑似 RoHS 受限物质 {display}（名称含 {key}，未确证）"
                break
        if sus is None:
            for key, display in _SVHC_BY_NAME.items():
                if key in lowered:
                    sus = f"疑似 REACH SVHC 候选 {display}（名称含 {key}，未确证）"
                    break
        if sus:
            reason = f"{ing.name}: {sus}，请核对 CAS 后确认"
            if reason not in reasons:
                reasons.append(reason)
            if sus not in suspected:
                suspected.append(sus)

    if rohs_hits:
        return ComplianceResult(
            compliant=False,
            status="infeasible",
            reasons=reasons,
            rohs_hits=rohs_hits,
            svhc_hits=svhc_hits,
        )
    if svhc_hits or rohs_below_limit or suspected:
        return ComplianceResult(
            compliant=True,
            status="warn",
            reasons=reasons,
            svhc_hits=svhc_hits,
        )
    return ComplianceResult(compliant=True, status="pass")


def rohs_restricted(name: str, cas_no: str | None) -> str | None:
    """The RoHS restricted-substance label for one ingredient, or None (release gate red line)."""
    return _match_ingredient(name, cas_no, _ROHS_BY_CAS, _ROHS_BY_NAME)
