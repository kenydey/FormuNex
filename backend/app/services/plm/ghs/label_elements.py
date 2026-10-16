"""Label elements from a mixture classification (GB 30000 / GB 15258).

Pictograms, signal word, hazard statements and precautionary statements, with
the GHS precedence rules:

* skull and crossbones (GHS06) → no exclamation mark (GHS07);
* corrosion (GHS05) → no GHS07 for skin / eye irritation;
* health hazard (GHS08) for respiratory sensitisation → no GHS07 for skin
  sensitisation or skin / eye irritation;
* "危险" outranks "警告"; H314 makes H318 / H319 redundant.

The H → P table is a frozen, reviewable copy of the core precautionary sets of
GHS Rev.4 Annex 3 restricted to the P codes of GB 30000 (all codes are checked
against the bundled statement list in the tests).
"""
from __future__ import annotations

from .model import HazardOutcome, severity

PICTOGRAM_NAMES = {
    "GHS01": "爆炸弹", "GHS02": "火焰", "GHS03": "圆圈上方火焰", "GHS04": "气瓶", "GHS05": "腐蚀",
    "GHS06": "骷髅和交叉骨", "GHS07": "感叹号", "GHS08": "健康危害", "GHS09": "环境",
}


def _pictogram(hazard_class: str, category: str) -> list[str]:
    c = category
    if hazard_class == "explosive":
        return ["GHS01"] if c in ("unstable", "1.1", "1.2", "1.3", "1.4") else []
    if hazard_class in ("self_reactive", "organic_peroxide"):
        return {"A": ["GHS01"], "B": ["GHS01", "GHS02"]}.get(c, ["GHS02"] if c in "CDEF" else [])
    if hazard_class in ("flammable_gas",) and c == "1":
        return ["GHS02"]
    if hazard_class == "aerosol":
        return ["GHS02"]
    if hazard_class == "flammable_liquid":
        return ["GHS02"] if c in ("1", "2", "3") else []
    if hazard_class in ("flammable_solid", "pyrophoric_liquid", "pyrophoric_solid", "self_heating", "water_reactive"):
        return ["GHS02"]
    if hazard_class in ("oxidizing_gas", "oxidizing_liquid", "oxidizing_solid"):
        return ["GHS03"]
    if hazard_class == "gas_under_pressure":
        return ["GHS04"]
    if hazard_class == "metal_corrosive":
        return ["GHS05"]
    if hazard_class == "skin_corrosion":
        return ["GHS05"] if c.startswith("1") else ["GHS07"] if c == "2" else []
    if hazard_class == "eye_damage":
        return ["GHS05"] if c == "1" else ["GHS07"] if c in ("2", "2A") else []
    if hazard_class.startswith("acute_tox_"):
        return ["GHS06"] if c in ("1", "2", "3") else ["GHS07"] if c == "4" else []
    if hazard_class == "skin_sensitizer":
        return ["GHS07"]
    if hazard_class in ("resp_sensitizer", "mutagenicity", "carcinogenicity", "stot_re", "aspiration"):
        return ["GHS08"] if not (hazard_class == "aspiration" and c == "2") else []
    if hazard_class == "reproductive":
        return [] if c == "lact" else ["GHS08"]
    if hazard_class == "stot_se":
        return ["GHS08"] if c in ("1", "2") else ["GHS07"]
    if hazard_class == "aquatic_acute":
        return ["GHS09"] if c == "1" else []
    if hazard_class == "aquatic_chronic":
        return ["GHS09"] if c in ("1", "2") else []
    if hazard_class == "ozone":
        return ["GHS07"]
    return []


_DANGER: dict[str, set[str]] = {
    "explosive": {"unstable", "1.1", "1.2", "1.3", "1.5"},
    "flammable_gas": {"1"}, "aerosol": {"1"}, "oxidizing_gas": {"1"},
    "flammable_liquid": {"1", "2"}, "flammable_solid": {"1"},
    "self_reactive": {"A", "B", "C", "D"}, "organic_peroxide": {"A", "B", "C", "D"},
    "pyrophoric_liquid": {"1"}, "pyrophoric_solid": {"1"}, "self_heating": {"1"},
    "water_reactive": {"1", "2"}, "oxidizing_liquid": {"1", "2"}, "oxidizing_solid": {"1", "2"},
    "acute_tox_oral": {"1", "2", "3"}, "acute_tox_dermal": {"1", "2", "3"}, "acute_tox_inhal": {"1", "2", "3"},
    "skin_corrosion": {"1", "1A", "1B", "1C"}, "eye_damage": {"1"},
    "resp_sensitizer": {"1", "1A", "1B"},
    "mutagenicity": {"1A", "1B"}, "carcinogenicity": {"1A", "1B"}, "reproductive": {"1A", "1B"},
    "stot_se": {"1"}, "stot_re": {"1"}, "aspiration": {"1"},
}
# Categories that carry no signal word at all.
_NO_SIGNAL: dict[str, set[str]] = {
    "reproductive": {"lact"}, "aquatic_acute": {"2", "3"}, "aquatic_chronic": {"2", "3", "4"},
    "explosive": {"1.6"}, "self_reactive": {"G"}, "organic_peroxide": {"G"},
    # Chemically unstable gases add hazard statements only; the flammable-gas class gives the signal word.
    "chemically_unstable_gas": {"A", "B"},
}


def signal_word_for(hazard_class: str, category: str) -> str | None:
    if category in _NO_SIGNAL.get(hazard_class, set()):
        return None
    return "危险" if category in _DANGER.get(hazard_class, set()) else "警告"


# Core precautionary statements per hazard statement (GHS Rev.4 Annex 3, GB 30000 codes).
H_TO_P: dict[str, list[str]] = {
    # physical
    "H200": ["P201", "P202", "P210", "P240", "P250", "P280", "P370+P380", "P372", "P373", "P401", "P501"],
    "H201": ["P210", "P230", "P240", "P250", "P280", "P370+P380", "P372", "P373", "P401", "P501"],
    "H202": ["P210", "P230", "P240", "P250", "P280", "P370+P380", "P372", "P373", "P401", "P501"],
    "H203": ["P210", "P230", "P240", "P250", "P280", "P370+P380", "P372", "P373", "P401", "P501"],
    "H204": ["P210", "P240", "P250", "P280", "P370+P380", "P372", "P373", "P374", "P401", "P501"],
    "H205": ["P210", "P230", "P240", "P250", "P280", "P372", "P373", "P401", "P501"],
    "H220": ["P210", "P377", "P381", "P403"],
    "H221": ["P210", "P377", "P381", "P403"],
    "H222": ["P210", "P211", "P251", "P410+P412"],
    "H223": ["P210", "P211", "P251", "P410+P412"],
    "H224": ["P210", "P233", "P240", "P241", "P242", "P243", "P280", "P303+P361+P353", "P370+P378",
             "P403+P235", "P501"],
    "H225": ["P210", "P233", "P240", "P241", "P242", "P243", "P280", "P303+P361+P353", "P370+P378",
             "P403+P235", "P501"],
    "H226": ["P210", "P233", "P240", "P241", "P242", "P243", "P280", "P303+P361+P353", "P370+P378",
             "P403+P235", "P501"],
    "H227": ["P210", "P280", "P370+P378", "P403+P235", "P501"],
    "H228": ["P210", "P240", "P241", "P280", "P370+P378"],
    "H230": ["P202"],
    "H231": ["P202"],
    "H240": ["P210", "P220", "P234", "P280", "P370+P380+P375", "P403+P235", "P411", "P420", "P501"],
    "H241": ["P210", "P220", "P234", "P280", "P370+P380+P375", "P403+P235", "P411", "P420", "P501"],
    "H242": ["P210", "P220", "P234", "P280", "P370+P378", "P403+P235", "P411", "P420", "P501"],
    "H250": ["P210", "P222", "P280", "P302+P334", "P370+P378", "P422"],
    "H251": ["P235+P410", "P280", "P407", "P413", "P420"],
    "H252": ["P235+P410", "P280", "P407", "P413", "P420"],
    "H260": ["P223", "P231+P232", "P280", "P335+P334", "P370+P378", "P402+P404", "P501"],
    "H261": ["P223", "P231+P232", "P280", "P335+P334", "P370+P378", "P402+P404", "P501"],
    "H270": ["P220", "P244", "P370+P376", "P403"],
    "H271": ["P210", "P220", "P221", "P280", "P283", "P306+P360", "P371+P380+P375", "P370+P378", "P501"],
    "H272": ["P210", "P220", "P221", "P280", "P370+P378", "P501"],
    "H280": ["P410+P403"],
    "H281": ["P282", "P336", "P315", "P403"],
    "H290": ["P234", "P390", "P406"],
    # health
    "H300": ["P264", "P270", "P301+P310", "P321", "P330", "P405", "P501"],
    "H301": ["P264", "P270", "P301+P310", "P321", "P330", "P405", "P501"],
    "H302": ["P264", "P270", "P301+P312", "P330", "P501"],
    "H303": ["P312"],
    "H304": ["P301+P310", "P331", "P405", "P501"],
    "H305": ["P301+P310", "P331"],
    "H310": ["P262", "P264", "P270", "P280", "P302+P352", "P310", "P321", "P361+P364", "P405", "P501"],
    "H311": ["P280", "P302+P352", "P312", "P321", "P361+P364", "P405", "P501"],
    "H312": ["P280", "P302+P352", "P312", "P321", "P362+P364", "P501"],
    "H313": ["P312"],
    "H314": ["P260", "P264", "P280", "P301+P330+P331", "P303+P361+P353", "P363", "P304+P340",
             "P305+P351+P338", "P310", "P321", "P405", "P501"],
    "H315": ["P264", "P280", "P302+P352", "P321", "P332+P313", "P362+P364"],
    "H316": ["P332+P313"],
    "H317": ["P261", "P272", "P280", "P302+P352", "P333+P313", "P321", "P362+P364", "P501"],
    "H318": ["P280", "P305+P351+P338", "P310"],
    "H319": ["P264", "P280", "P305+P351+P338", "P337+P313"],
    "H320": ["P264", "P305+P351+P338", "P337+P313"],
    "H330": ["P260", "P271", "P284", "P304+P340", "P310", "P320", "P403+P233", "P405", "P501"],
    "H331": ["P261", "P271", "P304+P340", "P311", "P321", "P403+P233", "P405", "P501"],
    "H332": ["P261", "P271", "P304+P340", "P312"],
    "H333": ["P304+P312"],
    "H334": ["P261", "P284", "P304+P340", "P342+P311", "P501"],
    "H335": ["P261", "P271", "P304+P340", "P312", "P403+P233", "P405", "P501"],
    "H336": ["P261", "P271", "P304+P340", "P312", "P403+P233", "P405", "P501"],
    "H340": ["P201", "P202", "P281", "P308+P313", "P405", "P501"],
    "H341": ["P201", "P202", "P281", "P308+P313", "P405", "P501"],
    "H350": ["P201", "P202", "P281", "P308+P313", "P405", "P501"],
    "H351": ["P201", "P202", "P281", "P308+P313", "P405", "P501"],
    "H360": ["P201", "P202", "P281", "P308+P313", "P405", "P501"],
    "H361": ["P201", "P202", "P281", "P308+P313", "P405", "P501"],
    "H362": ["P201", "P260", "P263", "P264", "P270", "P308+P313"],
    "H370": ["P260", "P264", "P270", "P308+P311", "P321", "P405", "P501"],
    "H371": ["P260", "P264", "P270", "P308+P311", "P405", "P501"],
    "H372": ["P260", "P264", "P270", "P314", "P501"],
    "H373": ["P260", "P314", "P501"],
    # environmental
    "H400": ["P273", "P391", "P501"],
    "H401": ["P273", "P501"],
    "H402": ["P273", "P501"],
    "H410": ["P273", "P391", "P501"],
    "H411": ["P273", "P391", "P501"],
    "H412": ["P273", "P501"],
    "H413": ["P273", "P501"],
    "H420": ["P502"],
}

# A more urgent response makes the milder one redundant.
_P_SUPERSEDES: dict[str, set[str]] = {
    "P310": {"P311", "P312"},
    "P311": {"P312"},
    "P301+P310": {"P301+P312"},
    "P342+P311": set(),
    "P362+P364": set(),
    "P361+P364": {"P362+P364"},
    "P308+P311": set(),
}

# Hazard-class priority for ordering statements on the label: physical hazards first (as
# GB 15258 labels list them), then health by severity, then environment.
_CLASS_PRIORITY = [
    "explosive", "flammable_gas", "aerosol", "flammable_liquid", "flammable_solid", "self_reactive",
    "organic_peroxide", "pyrophoric_liquid", "pyrophoric_solid", "self_heating", "water_reactive",
    "oxidizing_gas", "oxidizing_liquid", "oxidizing_solid", "gas_under_pressure", "metal_corrosive",
    "acute_tox_inhal", "acute_tox_oral", "acute_tox_dermal", "carcinogenicity", "mutagenicity",
    "reproductive", "resp_sensitizer", "stot_se", "stot_re", "skin_corrosion", "eye_damage", "aspiration",
    "skin_sensitizer", "aquatic_acute", "aquatic_chronic", "ozone",
]


def _order_key(o: HazardOutcome) -> tuple[int, int, int]:
    danger = 0 if signal_word_for(o.hazard_class, o.category) == "危险" else 1
    pri = _CLASS_PRIORITY.index(o.hazard_class) if o.hazard_class in _CLASS_PRIORITY else 99
    return danger, pri, severity(o.category)


def build_label(outcomes: list[HazardOutcome], *, max_label_p: int = 6) -> dict:
    ordered = sorted(outcomes, key=_order_key)

    # Pictograms with their reasons, then the precedence rules.
    reasons: dict[str, list[str]] = {}
    for o in ordered:
        for pic in _pictogram(o.hazard_class, o.category):
            reasons.setdefault(pic, []).append(f"{o.hazard_class}:{o.category}")
    if "GHS07" in reasons:
        why = reasons["GHS07"]
        drop = False
        if "GHS06" in reasons:
            drop = True
        else:
            irritation = {r for r in why if r.split(":")[0] in ("skin_corrosion", "eye_damage")}
            skin_sens = {r for r in why if r.startswith("skin_sensitizer")}
            others = set(why) - irritation - skin_sens
            resp_sens = any(r.startswith("resp_sensitizer") for r in reasons.get("GHS08", []))
            if not others and (("GHS05" in reasons and not skin_sens) or (resp_sens and (irritation or skin_sens))):
                drop = True
        if drop:
            reasons.pop("GHS07")
    pictograms = [{"code": c, "name_zh": PICTOGRAM_NAMES[c], "reasons": reasons[c]} for c in sorted(reasons)]

    words = {signal_word_for(o.hazard_class, o.category) for o in outcomes}
    signal = "危险" if "危险" in words else "警告" if "警告" in words else None

    h_codes: list[str] = []
    for o in ordered:
        for h in o.h_codes:
            if h not in h_codes:
                h_codes.append(h)
    if "H314" in h_codes:
        h_codes = [h for h in h_codes if h not in ("H318", "H319", "H320")]
    if "H318" in h_codes:
        h_codes = [h for h in h_codes if h not in ("H319", "H320")]

    p_codes: list[str] = []
    for h in h_codes:
        for p in H_TO_P.get(h, []):
            if p not in p_codes:
                p_codes.append(p)
    redundant = set().union(*(_P_SUPERSEDES.get(p, set()) for p in p_codes)) if p_codes else set()
    p_codes = [p for p in p_codes if p not in redundant]

    # Label subset (GB 15258): every hazard statement gets its first precautionary statement before
    # any gets a second (round-robin), and response / storage / disposal each keep one slot when the
    # SDS list has one.
    per_h = [[p for p in H_TO_P.get(h, []) if p in p_codes] for h in h_codes]
    round_robin: list[str] = []
    for i in range(max((len(x) for x in per_h), default=0)):
        for lst in per_h:
            if i < len(lst) and lst[i] not in round_robin:
                round_robin.append(lst[i])
    label_p: list[str] = []
    for group in ("3", "4", "5"):
        first = next((p for p in round_robin if p[1] == group), None)
        if first and len(label_p) < max_label_p:
            label_p.append(first)
    for p in round_robin:
        if len(label_p) >= max_label_p:
            break
        if p not in label_p:
            label_p.append(p)
    label_p = sorted(label_p, key=lambda p: (p[1], round_robin.index(p)))
    return {"pictograms": pictograms, "signal_word": signal, "h_codes": h_codes, "p_codes": p_codes,
            "label_p_codes": label_p}
