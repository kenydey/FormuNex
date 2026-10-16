"""A single number for "how hazardous is this classification", for ranking and multi-objective search.

Not a regulatory quantity: it lets the cockpit minimise hazard as one Pareto
objective and lets DOE screening sort runs. Each hazard outcome scores
``severity(category) × weight(hazard class)``; the total is the sum. Weights
put CMR and acute toxicity above irritation, and category 1 above 4.
"""
from __future__ import annotations

from typing import Any

CATEGORY_SEVERITY: dict[str, float] = {
    "1": 10, "1A": 10, "1B": 8, "1C": 7, "2": 5, "2A": 5, "2B": 3, "3": 3, "4": 1.5, "5": 1, "lact": 4,
}
CLASS_WEIGHT: dict[str, float] = {
    "carcinogenicity": 2.0, "mutagenicity": 2.0, "reproductive": 2.0,
    "acute_tox_oral": 1.5, "acute_tox_dermal": 1.5, "acute_tox_inhal": 1.5,
    "stot_re": 1.5, "stot_se": 1.2, "resp_sensitizer": 1.5, "aspiration": 1.0,
    "skin_corrosion": 1.0, "eye_damage": 1.0, "skin_sensitizer": 1.0,
    "flammable_liquid": 1.0, "aquatic_acute": 0.8, "aquatic_chronic": 1.0, "ozone": 1.0,
}


def hazard_score(classification: dict[str, Any]) -> float:
    total = 0.0
    for h in classification.get("hazards") or []:
        sev = CATEGORY_SEVERITY.get(str(h.get("category")), 2.0)
        total += sev * CLASS_WEIGHT.get(str(h.get("hazard_class")), 1.0)
    return round(total, 3)
