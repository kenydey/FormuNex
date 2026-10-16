"""GB 30000.2~29-2013 hazard classes: stable codes, Chinese names and H codes.

One entry per hazard class. ``names`` lists every spelling seen in Chinese
regulatory texts (including the 《危险化学品目录》 variants and typos found in
the MSDS-Generator data); the first is the canonical GB 30000 name.
``h_codes`` maps a category to its hazard statement; ``None`` means the
category carries no H statement (e.g. self-reactive type G).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HazardClass:
    code: str
    part: str  # GB 30000 part, e.g. "30000.18"
    kind: str  # physical | health | environmental
    names: tuple[str, ...]
    h_codes: dict[str, str | None] = field(default_factory=dict)
    # Category to use when the text names the class without one (加压气体).
    default_category: str | None = None


_PEROXIDE_TYPES = {"A": "H240", "B": "H241", "C": "H242", "D": "H242", "E": "H242", "F": "H242", "G": None}

HAZARD_CLASSES: tuple[HazardClass, ...] = (
    HazardClass("explosive", "30000.2", "physical", ("爆炸物",),
                {"unstable": "H200", "1.1": "H201", "1.2": "H202", "1.3": "H203", "1.4": "H204",
                 "1.5": "H205", "1.6": None}),
    HazardClass("flammable_gas", "30000.3", "physical", ("易燃气体",), {"1": "H220", "2": "H221"}),
    HazardClass("chemically_unstable_gas", "30000.3", "physical", ("化学不稳定性气体",),
                {"A": "H230", "B": "H231"}),
    HazardClass("aerosol", "30000.4", "physical", ("易燃气溶胶", "气溶胶"), {"1": "H222", "2": "H223"}),
    HazardClass("oxidizing_gas", "30000.5", "physical", ("氧化性气体",), {"1": "H270"}),
    # 压缩 / 液化 / 溶解气体 → H280; 冷冻液化气体 → H281. Catalogue text does not say which.
    HazardClass("gas_under_pressure", "30000.6", "physical", ("加压气体",),
                {"gas": "H280", "refrigerated": "H281"}, default_category="gas"),
    HazardClass("flammable_liquid", "30000.7", "physical", ("易燃液体",),
                {"1": "H224", "2": "H225", "3": "H226", "4": "H227"}),
    HazardClass("flammable_solid", "30000.8", "physical", ("易燃固体",), {"1": "H228", "2": "H228"}),
    HazardClass("self_reactive", "30000.9", "physical", ("自反应物质和混合物", "自反应物质"), _PEROXIDE_TYPES),
    HazardClass("pyrophoric_liquid", "30000.10", "physical", ("自燃液体",), {"1": "H250"}),
    HazardClass("pyrophoric_solid", "30000.11", "physical", ("自燃固体",), {"1": "H250"}),
    HazardClass("self_heating", "30000.12", "physical", ("自热物质和混合物", "自热物质"),
                {"1": "H251", "2": "H252"}),
    HazardClass("water_reactive", "30000.13", "physical",
                ("遇水放出易燃气体的物质和混合物", "遇水放出易燃气体的物质"),
                {"1": "H260", "2": "H261", "3": "H261"}),
    HazardClass("oxidizing_liquid", "30000.14", "physical", ("氧化性液体",),
                {"1": "H271", "2": "H272", "3": "H272"}),
    HazardClass("oxidizing_solid", "30000.15", "physical", ("氧化性固体",),
                {"1": "H271", "2": "H272", "3": "H272"}),
    HazardClass("organic_peroxide", "30000.16", "physical", ("有机过氧化物",), _PEROXIDE_TYPES),
    HazardClass("metal_corrosive", "30000.17", "physical", ("金属腐蚀物",), {"1": "H290"}),
    HazardClass("acute_tox_oral", "30000.18", "health", ("急性毒性-经口",),
                {"1": "H300", "2": "H300", "3": "H301", "4": "H302", "5": "H303"}),
    HazardClass("acute_tox_dermal", "30000.18", "health", ("急性毒性-经皮",),
                {"1": "H310", "2": "H310", "3": "H311", "4": "H312", "5": "H313"}),
    HazardClass("acute_tox_inhal", "30000.18", "health", ("急性毒性-吸入",),
                {"1": "H330", "2": "H330", "3": "H331", "4": "H332", "5": "H333"}),
    HazardClass("skin_corrosion", "30000.19", "health", ("皮肤腐蚀/刺激",),
                {"1": "H314", "1A": "H314", "1B": "H314", "1C": "H314", "2": "H315", "3": "H316"}),
    HazardClass("eye_damage", "30000.20", "health", ("严重眼损伤/眼刺激", "严重眼损伤/眼刺激性"),
                {"1": "H318", "2": "H319", "2A": "H319", "2B": "H320"}),
    HazardClass("resp_sensitizer", "30000.21", "health", ("呼吸道致敏物",),
                {"1": "H334", "1A": "H334", "1B": "H334"}),
    HazardClass("skin_sensitizer", "30000.21", "health", ("皮肤致敏物",),
                {"1": "H317", "1A": "H317", "1B": "H317"}),
    HazardClass("mutagenicity", "30000.22", "health", ("生殖细胞致突变性",),
                {"1A": "H340", "1B": "H340", "2": "H341"}),
    HazardClass("carcinogenicity", "30000.23", "health", ("致癌性", "致癌物"),
                {"1A": "H350", "1B": "H350", "2": "H351"}),
    HazardClass("reproductive", "30000.24", "health", ("生殖毒性",),
                {"1A": "H360", "1B": "H360", "2": "H361", "lact": "H362"}),
    # Category 3 is H335 (呼吸道刺激) and / or H336 (麻醉效应) — the parser reads the effect.
    HazardClass("stot_se", "30000.25", "health",
                ("特异性靶器官毒性-一次接触", "异性靶器官毒性-一次接触"),
                {"1": "H370", "2": "H371", "3": None}),
    HazardClass("stot_re", "30000.26", "health", ("特异性靶器官毒性-反复接触",), {"1": "H372", "2": "H373"}),
    HazardClass("aspiration", "30000.27", "health", ("吸入危害",), {"1": "H304", "2": "H305"}),
    HazardClass("aquatic_acute", "30000.28", "environmental", ("危害水生环境-急性危害",),
                {"1": "H400", "2": "H401", "3": "H402"}),
    HazardClass("aquatic_chronic", "30000.28", "environmental", ("危害水生环境-长期危害",),
                {"1": "H410", "2": "H411", "3": "H412", "4": "H413"}),
    HazardClass("ozone", "30000.29", "environmental", ("危害臭氧层",), {"1": "H420"}),
)

BY_CODE: dict[str, HazardClass] = {hc.code: hc for hc in HAZARD_CLASSES}

# STOT SE 3 effect → H code.
STOT_SE3_EFFECTS: dict[str, str] = {"呼吸道刺激": "H335", "麻醉效应": "H336"}

# Pre-GHS dangerous-goods labels that still appear in catalogue text. They are
# not GB 30000 classes, so the parser reports them for human review.
LEGACY_LABELS: tuple[str, ...] = (
    "有毒固体", "有毒液体", "腐蚀性固体", "腐蚀性液体", "刺激性固体", "刺激性液体",
)


def h_code_for(code: str, category: str) -> str | None:
    hc = BY_CODE.get(code)
    if hc is None:
        return None
    return hc.h_codes.get(category)
