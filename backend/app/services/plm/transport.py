"""Transport classification of a mixture (GB 6944 classes, GB 12268 entries).

Derived from the GB 30000 result: class 3 from the flammable-liquid category
(packing group from flash point / boiling point), class 6.1 from acute
toxicity, class 8 from skin corrosion sub-category or metal corrosion, class 9
(UN 3082 / 3077) for environmentally hazardous mixtures with no other class.
Several classes are resolved with the UN precedence-of-hazards table (UN Model
Regulations 2.0.3.3) for the 3 / 6.1 / 8 combinations a formulation lab meets.

A single-substance product uses its own GB 12268 entry when one is on file;
otherwise a generic "n.o.s." entry is chosen and the two main hazardous
ingredients are named as technical names. The result is always marked for
human confirmation — the engine never presents a UN number as final.
"""
from __future__ import annotations

from typing import Any

from .ghs.model import ClassificationResult, Component, MixtureProperties

# (class, subsidiary or None, phys) → (UN, name_zh, name_en)
_NOS: dict[tuple[str, str | None, str], tuple[str, str, str]] = {
    ("3", None, "liquid"): ("1993", "易燃液体，未另作规定的", "FLAMMABLE LIQUID, N.O.S."),
    ("3", "6.1", "liquid"): ("1992", "易燃液体，毒性，未另作规定的", "FLAMMABLE LIQUID, TOXIC, N.O.S."),
    ("3", "8", "liquid"): ("2924", "易燃液体，腐蚀性，未另作规定的", "FLAMMABLE LIQUID, CORROSIVE, N.O.S."),
    ("6.1", None, "liquid"): ("2810", "有机毒性液体，未另作规定的", "TOXIC LIQUID, ORGANIC, N.O.S."),
    ("6.1", "3", "liquid"): ("2929", "有机毒性液体，易燃，未另作规定的", "TOXIC LIQUID, FLAMMABLE, ORGANIC, N.O.S."),
    ("6.1", "8", "liquid"): ("2927", "有机毒性液体，腐蚀性，未另作规定的", "TOXIC LIQUID, CORROSIVE, ORGANIC, N.O.S."),
    ("6.1", None, "solid"): ("2811", "有机毒性固体，未另作规定的", "TOXIC SOLID, ORGANIC, N.O.S."),
    ("8", None, "liquid"): ("1760", "腐蚀性液体，未另作规定的", "CORROSIVE LIQUID, N.O.S."),
    ("8", "3", "liquid"): ("2920", "腐蚀性液体，易燃，未另作规定的", "CORROSIVE LIQUID, FLAMMABLE, N.O.S."),
    ("8", "6.1", "liquid"): ("2922", "腐蚀性液体，毒性，未另作规定的", "CORROSIVE LIQUID, TOXIC, N.O.S."),
    ("8", None, "solid"): ("1759", "腐蚀性固体，未另作规定的", "CORROSIVE SOLID, N.O.S."),
    ("9", None, "liquid"): ("3082", "对环境有害的液态物质，未另作规定的",
                            "ENVIRONMENTALLY HAZARDOUS SUBSTANCE, LIQUID, N.O.S."),
    ("9", None, "solid"): ("3077", "对环境有害的固态物质，未另作规定的",
                           "ENVIRONMENTALLY HAZARDOUS SUBSTANCE, SOLID, N.O.S."),
}
# Paints and paint-related material have their own entries.
_PAINT: dict[tuple[str, str | None], tuple[str, str, str]] = {
    ("3", None): ("1263", "涂料或涂料相关材料", "PAINT or PAINT RELATED MATERIAL"),
    ("8", None): ("3066", "涂料或涂料相关材料（腐蚀性）", "PAINT or PAINT RELATED MATERIAL (corrosive)"),
    ("3", "8"): ("3469", "涂料，易燃，腐蚀性", "PAINT, FLAMMABLE, CORROSIVE"),
    ("8", "3"): ("3470", "涂料，腐蚀性，易燃", "PAINT, CORROSIVE, FLAMMABLE"),
}
_PG_RANK = {"I": 0, "II": 1, "III": 2}

# UN 2.0.3.3 precedence for liquids: (row class+PG[+route], column class+PG) → winning (class, PG).
_PRECEDENCE: dict[tuple[str, str], tuple[str, str]] = {}
for _pg8 in ("I", "II", "III"):
    _PRECEDENCE[("3:I", f"8:{_pg8}")] = ("3", "I")
    _PRECEDENCE[("3:II", f"8:{_pg8}")] = ("8", "I") if _pg8 == "I" else ("3", "II")
    _PRECEDENCE[("3:III", f"8:{_pg8}")] = ("8", _pg8) if _pg8 in ("I", "II") else ("3", "III")
    _PRECEDENCE[("6.1:I", f"8:{_pg8}")] = ("6.1", "I")
    _PRECEDENCE[("6.1:II", f"8:{_pg8}")] = ("8", "I") if _pg8 == "I" else ("6.1", "II")
    _PRECEDENCE[("6.1:III", f"8:{_pg8}")] = ("8", _pg8)
for _pg6 in ("I", "II", "III"):
    _PRECEDENCE[("3:I", f"6.1:{_pg6}")] = ("3", "I")
    _PRECEDENCE[("3:II", f"6.1:{_pg6}")] = ("3", "I") if _pg6 == "I" else ("3", "II")
    _PRECEDENCE[("3:III", f"6.1:{_pg6}")] = ("6.1", _pg6) if _pg6 in ("I", "II") else ("3", "III")


def _candidates(result: ClassificationResult, props: MixtureProperties) -> dict[str, str]:
    """{class: packing group} for every transport class the GHS result implies."""
    cats = {}
    for o in result.hazards:
        cats.setdefault(o.hazard_class, []).append(o.category)
    out: dict[str, str] = {}
    fl = cats.get("flammable_liquid", [])
    if any(c in ("1", "2", "3") for c in fl):
        if "1" in fl:
            out["3"] = "I"
        elif "2" in fl:
            out["3"] = "II"
        else:
            out["3"] = "III"
    tox_pg = None
    for cls in ("acute_tox_oral", "acute_tox_dermal", "acute_tox_inhal"):
        for c in cats.get(cls, []):
            pg = {"1": "I", "2": "II", "3": "III"}.get(c)
            if pg and (tox_pg is None or _PG_RANK[pg] < _PG_RANK[tox_pg]):
                tox_pg = pg
    if tox_pg:
        out["6.1"] = tox_pg
    skin = cats.get("skin_corrosion", [])
    corr_pg = None
    for c in skin:
        pg = {"1A": "I", "1B": "II", "1C": "III", "1": "II"}.get(c)
        if pg and (corr_pg is None or _PG_RANK[pg] < _PG_RANK[corr_pg]):
            corr_pg = pg
    if corr_pg is None and "metal_corrosive" in cats:
        corr_pg = "III"
    if corr_pg:
        out["8"] = corr_pg
    return out


def derive_transport(result: ClassificationResult, props: MixtureProperties, components: list[Component],
                     *, substance_entries: dict[str, dict] | None = None) -> dict[str, Any]:
    """Return a transport proposal; ``needs_review`` is always True."""
    phys = "solid" if props.phys_state == "solid" else "liquid"
    notes: list[str] = []
    env = any(o.hazard_class == "aquatic_acute" and o.category == "1" for o in result.hazards) or any(
        o.hazard_class == "aquatic_chronic" and o.category in ("1", "2") for o in result.hazards)
    cand = _candidates(result, props)
    if not cand and not env:
        return {"regulated": False, "needs_review": True, "basis": "无运输危险性（按 GB 30000 分类推导）",
                "notes": ["未考虑气体 / 爆炸物 / 氧化性等需试验确定的类别"]}

    primary, pg, subsidiary = None, None, None
    if cand:
        items = sorted(cand.items(), key=lambda kv: _PG_RANK[kv[1]])
        if len(items) == 1:
            primary, pg = items[0]
        else:
            keys = [f"{k}:{v}" for k, v in cand.items()]
            # Pairwise precedence; class 3 / 6.1 rows against 6.1 / 8 columns.
            order = {"3": 0, "6.1": 1, "8": 2}
            keys.sort(key=lambda k: order[k.split(":")[0]])
            winner = keys[0]
            for other in keys[1:]:
                res = _PRECEDENCE.get((winner, other))
                if res is None:
                    notes.append(f"{winner} 与 {other} 的优先顺序不在内置表中，需人工判定")
                    continue
                winner = f"{res[0]}:{res[1]}"
            primary, pg = winner.split(":")
            subs = [k for k in cand if k != primary]
            subsidiary = subs[0] if subs else None
            if len(subs) > 1:
                notes.append(f"存在多个次要危险性 {subs}，已取第一个选择条目，需人工确认")
    else:
        primary, pg = "9", "III"

    entry = None
    single = [c for c in components if c.pct >= 99.0]
    if single and substance_entries and single[0].substance_id in substance_entries:
        e = substance_entries[single[0].substance_id]
        rows = e.get("rows") or [e]
        pick = next((r for r in rows if r.get("packing_group") == pg), rows[0])
        entry = (pick["un_number"], pick["proper_shipping_name_zh"], pick["proper_shipping_name_en"])
        notes.append("单一物质产品，使用该物质的 GB 12268 品名条目")
        if len(rows) > 1 and pick.get("packing_group") != pg:
            notes.append(f"品名表中该物质无包装类别 {pg} 的条目，已取第一个条目，需人工确认")
        if pick.get("transport_class") and pick["transport_class"] != primary:
            notes.append(f"品名表条目类别 {pick['transport_class']} 与分类推导的 {primary} 不一致，需人工确认")
    if entry is None and props.product_category == "paint" and (primary, subsidiary) in _PAINT:
        entry = _PAINT[(primary, subsidiary)]
    if entry is None:
        entry = _NOS.get((primary, subsidiary, phys)) or _NOS.get((primary, None, phys))
        if entry is not None and (primary, subsidiary, phys) not in _NOS:
            notes.append(f"无 {primary}+{subsidiary} 的组合条目，已按主危险性选择，需人工确认")
    if entry is None:
        return {"regulated": True, "needs_review": True, "transport_class": primary, "packing_group": pg,
                "subsidiary_risk": subsidiary, "un_number": None,
                "basis": "无法自动选择 UN 编号，请人工指定", "notes": notes}

    technical = []
    if "未另作规定" in entry[1]:
        ranked = sorted((c for c in components if c.hazards), key=lambda c: -c.pct)
        technical = [c.name for c in ranked[:2]]
    return {
        "regulated": True,
        "needs_review": True,
        "un_number": entry[0],
        "proper_shipping_name_zh": entry[1],
        "proper_shipping_name_en": entry[2],
        "technical_names": technical,
        "transport_class": primary,
        "subsidiary_risk": subsidiary,
        "packing_group": pg,
        "environmentally_hazardous": env,
        "marine_pollutant": env,
        "basis": f"由 GB 30000 分类推导：{cand or '仅环境危害'}",
        "notes": notes,
    }
