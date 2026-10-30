"""ECHA CLP Annex VI (ATP) table mapping and validation (P1-1).

ECHA distributes harmonised Annex VI entries as Excel tables per ATP — there
is no public bulk API — so the CLP adapter stages ATP rows (converted to
JSON) instead of scraping. This module is the single place that maps Annex VI
hazard-class wording to the stable codes the GHS engine uses, and rejects
rows it cannot map rather than storing a wrong classification.

Acute toxicity is special: Annex VI gives the class as "Acute Tox." and the
route lives in the H-code (H30x oral, H31x dermal, H32x inhalation), so an
Acute Tox. row without a routable H-code is rejected.
"""

from __future__ import annotations

from typing import Any

# Annex VI class wording (normalised by _norm_class: trailing period
# stripped, lowercased, whitespace collapsed) -> stable code.
CLP_CLASS_MAP: dict[str, str] = {
    "expl": "explosive",
    "flam. gas": "flammable_gas",
    "chem. unstable gas": "chemically_unstable_gas",
    "aerosol": "aerosol",
    "ox. gas": "oxidizing_gas",
    "press. gas": "gas_under_pressure",
    "flam. liq": "flammable_liquid",
    "flam. sol": "flammable_solid",
    "self-react": "self_reactive",
    "pyr. liq": "pyrophoric_liquid",
    "pyr. sol": "pyrophoric_solid",
    "self-heat": "self_heating",
    "water-react": "water_reactive",
    "ox. liq": "oxidizing_liquid",
    "ox. sol": "oxidizing_solid",
    "org. perox": "organic_peroxide",
    "met. corr": "metal_corrosive",
    "skin corr": "skin_corrosion",
    "skin irrit": "skin_corrosion",
    "eye dam": "eye_damage",
    "eye irrit": "eye_damage",
    "resp. sens": "resp_sensitizer",
    "skin sens": "skin_sensitizer",
    "muta": "mutagenicity",
    "carc": "carcinogenicity",
    "repr": "reproductive",
    "stot se": "stot_se",
    "stot re": "stot_re",
    "asp. tox": "aspiration",
    "aquatic acute": "aquatic_acute",
    "aquatic chronic": "aquatic_chronic",
    "ozone": "ozone",
}

_ACUTE_ROUTE_BY_H: dict[str, str] = {
    **{f"H30{i}": "acute_tox_oral" for i in (0, 1, 2)},
    **{f"H31{i}": "acute_tox_dermal" for i in (0, 1, 2)},
    **{f"H32{i}": "acute_tox_inhal" for i in (0, 1, 2)},
}


def _norm_class(raw: str) -> str:
    return " ".join((raw or "").strip().rstrip(".").lower().split())


def _norm_category(raw: Any) -> str:
    cat = str(raw or "").strip()
    if cat.lower().rstrip(".") == "lact":
        return "lact"
    return cat


def map_entry(entry: dict[str, Any]) -> tuple[str, str]:
    """Map one staged ATP row to (stable hazard class, category).

    Raises ValueError with the reason when the row cannot be mapped.
    """
    cas = str(entry.get("cas") or "").strip()
    if not cas:
        raise ValueError("missing CAS number")
    cls = _norm_class(str(entry.get("hazard_class") or ""))
    if not cls:
        raise ValueError("missing hazard class")
    category = _norm_category(entry.get("category"))
    if not category:
        raise ValueError("missing category")
    if cls == "acute tox":
        h_code = str(entry.get("h_code") or "").strip().upper()
        code = _ACUTE_ROUTE_BY_H.get(h_code)
        if code is None:
            raise ValueError(
                f"Acute Tox. without a routable H-code (got {h_code!r})"
            )
        return code, category
    code = CLP_CLASS_MAP.get(cls)
    if code is None:
        raise ValueError(f"unmapped hazard class {entry.get('hazard_class')!r}")
    return code, category


def validate_entries(entries: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate staged ATP rows.

    Returns (valid rows with mapped ``hazard_class_code``/``category_code``
    added — the original Annex VI wording is kept untouched so the staged
    file stays auditable — report with accepted / rejected counts and
    per-row reasons). Rejected rows are never stored.
    """
    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if not isinstance(entries, list):
        return [], {"accepted": 0, "rejected": 1, "reasons": ["entries is not a list"]}
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            rejected.append({"index": idx, "reason": "entry is not an object"})
            continue
        try:
            code, category = map_entry(entry)
        except ValueError as exc:
            rejected.append(
                {"index": idx, "cas": entry.get("cas"), "reason": str(exc)}
            )
            continue
        row = dict(entry)
        row["hazard_class_code"] = code
        row["category_code"] = category
        valid.append(row)
    return valid, {
        "accepted": len(valid),
        "rejected": len(rejected),
        "reasons": rejected,
    }
