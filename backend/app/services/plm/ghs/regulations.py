"""Applicable-regulation registry (G2).

A regulation is a market jurisdiction with its own classification rule set:
``CN`` (GB 30000), ``EU`` (CLP) and ``US`` (OSHA HazCom) in phase 1. Adding a
jurisdiction later (e.g. Japan, JIS Z 7252/7253) means one new rule-set file
plus one registry entry — no structural change, no company-model change.

The registry maps a regulation code to the rule-set version the engine
already knows how to load (``ghs.ruleset.load_ruleset``); the engine itself
is rule-set driven, so CLP/OSHA reuse the GB-parameterised rule code with
different parameters. A regulation with no registry entry cannot be selected
anywhere (company config, generation, gate) — unknown codes fail loudly.
"""
from __future__ import annotations

DEFAULT_REGULATION = "CN"

# code -> {name, name_en, ruleset, standard, sds_section15}
# ``sds_section15`` is the jurisdiction's own core SDS legislation, rendered
# as SDS section 15 base entries. Per-jurisdiction ingredient listings
# (REACH Annex XVII, TSCA, ...) are NOT populated in phase 1: only CN ships
# ingredient listings (hazard catalogue / IECSC); other regulations render
# their core legislation lines only.
REGULATIONS: dict[str, dict[str, object]] = {
    "CN": {
        "name": "中国",
        "name_en": "China",
        "ruleset": "gb30000-2013.v1",
        "standard": "GB 30000",
        "sds_section15": [],
    },
    "EU": {
        "name": "欧盟",
        "name_en": "European Union",
        "ruleset": "clp-1272-2008.v1",
        "standard": "EC 1272/2008 (CLP)",
        "sds_section15": [
            ("(EC) No 1272/2008（CLP 法规）", "Regulation (EC) No 1272/2008 (CLP)"),
            ("(EC) No 1907/2006（REACH 法规，附件 II）", "Regulation (EC) No 1907/2006 (REACH), Annex II"),
        ],
    },
    "US": {
        "name": "美国",
        "name_en": "United States",
        "ruleset": "osha-hazcom-2012.v1",
        "standard": "OSHA HazCom 29 CFR 1910.1200",
        "sds_section15": [
            ("OSHA 危害通识标准 29 CFR 1910.1200", "OSHA Hazard Communication Standard, 29 CFR 1910.1200"),
        ],
    },
}


def regulation_codes() -> list[str]:
    """Registered regulation codes, in registry order."""
    return list(REGULATIONS)


def regulation_info(code: str) -> dict[str, object]:
    """Registry entry for ``code``; KeyError for an unregistered code."""
    return REGULATIONS[code]


def ruleset_for(code: str) -> str:
    """Rule-set version implementing ``code``; KeyError for an unregistered code."""
    return str(REGULATIONS[code]["ruleset"])


def standard_for(code: str) -> str:
    """Human-readable standard name for ``code`` (SDS section 16 basis)."""
    return str(REGULATIONS[code]["standard"])


def section15_for(code: str) -> list[tuple[str, str]]:
    """SDS section 15 base entries for ``code`` (empty for CN: the SDS builder
    renders the full Chinese legislation list for CN)."""
    entries = REGULATIONS[code]["sds_section15"]
    return [(str(z), str(e)) for z, e in entries]  # type: ignore[union-attr]


def validate_regulations(codes: object) -> list[str]:
    """Validate a company / generation regulation selection.

    Returns the codes deduplicated, registry order preserved. Unknown codes
    raise ValueError (never silently dropped); a non-list input raises
    ValueError too. An empty list is valid at the company level (it means
    "no default — generation must select explicitly"); generation rejects
    an empty selection separately.
    """
    if not isinstance(codes, list):
        raise ValueError("适用法规必须是列表")
    out: list[str] = []
    for code in codes:
        c = str(code)
        if c not in REGULATIONS:
            raise ValueError(f"未知适用法规：{c}")
        if c not in out:
            out.append(c)
    return out
