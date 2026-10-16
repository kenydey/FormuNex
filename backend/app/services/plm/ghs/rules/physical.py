"""Physical hazards of mixtures.

Physical hazards are decided by measurement, not by summation: flammable
liquids by closed-cup flash point and initial boiling point (GB 30000.7),
metal corrosion only by UN test C.1 (GB 30000.17 — pH does not decide it).
For the remaining physical classes the engine never assigns a category from
composition; an ingredient in one of them raises a data gap asking for the
test or an expert decision.
"""
from __future__ import annotations

from ...regulatory.hazard_classes import BY_CODE
from ..model import Component, DataGap, MixtureProperties, TraceEntry
from ..ruleset import RuleSet
from .common import RuleOutput, contributor, fmt, outcome


def flammable_liquid(components: list[Component], props: MixtureProperties, rs: RuleSet) -> RuleOutput:
    p = rs["flammable_liquid"]
    out = RuleOutput()
    if not props.is_liquid:
        return out
    fp, ibp = props.flash_point_c, props.initial_boiling_point_c
    flammable = [c for c in components if c.of("flammable_liquid") and c.pct >= 1.0]
    src = props.sources.get("flash_point_c", "")
    if fp is None:
        if flammable:
            out.gaps.append(DataGap(
                "flash_point_missing", "flammable_liquid",
                f"含易燃液体组分（{'、'.join(c.name for c in flammable[:3])}），缺少闭杯闪点实测值，"
                "无法判定易燃液体类别（GB 30000.7）。", "blocking"))
        return out
    note = f"闭杯闪点 {fp} ℃" + (f"（{src}）" if src else "")
    cat = None
    if fp < p["cat1"]["flash_point_lt"]:
        if ibp is None:
            out.gaps.append(DataGap("boiling_point_missing", "flammable_liquid",
                                    f"闪点 {fp} ℃ < 23 ℃，需初沸点区分类别 1 / 2；暂按类别 1 从严判定。", "blocking"))
            cat = "1"
        else:
            cat = "1" if ibp <= p["cat1"]["ibp_le"] else "2"
            note += f"，初沸点 {ibp} ℃"
    elif p["cat3"]["flash_point_ge"] <= fp <= p["cat3"]["flash_point_le"]:
        ex = p["sustained_combustion_exemption"]
        if props.sustained_combustion is False and ex["flash_point_gt"] < fp <= ex["flash_point_le"]:
            out.trace.append(TraceEntry("flammable_liquid", None, "sustained_combustion", "GB 30000.7",
                                        f"{note}，持续燃烧试验（L.2）为否 → 可不划为类别 3"))
            return out
        cat = "3"
    elif p["cat4"]["flash_point_gt"] < fp <= p["cat4"]["flash_point_le"]:
        cat = "4"
    out.trace.append(TraceEntry("flammable_liquid", cat, "measured", "GB 30000.7",
                                f"{note} → " + (f"类别 {cat}" if cat else "不分类"),
                                [contributor(c, c.worst("flammable_liquid").category) for c in flammable],
                                {"flash_point_c": fp, "initial_boiling_point_c": ibp}))
    if cat:
        out.outcomes.append(outcome("flammable_liquid", cat, "measured"))
    return out


def metal_corrosive(components: list[Component], props: MixtureProperties, rs: RuleSet) -> RuleOutput:
    out = RuleOutput()
    limit = float(rs["metal_corrosive"]["corrosion_rate_mm_per_year"])
    rate = props.metal_corrosion_rate_mm_per_year
    if rate is not None:
        cat = "1" if rate > limit else None
        out.trace.append(TraceEntry("metal_corrosive", cat, "measured", "GB 30000.17",
                                    f"UN C.1 腐蚀速率 {rate} mm/年 " + (f"> {limit} → 类别 1" if cat else f"≤ {limit} → 不分类")))
        if cat:
            out.outcomes.append(outcome("metal_corrosive", "1", "measured"))
        return out
    suspects = [c for c in components if c.of("metal_corrosive") and c.pct >= 1.0]
    if suspects:
        out.gaps.append(DataGap(
            "metal_corrosion_test_missing", "metal_corrosive",
            f"含金属腐蚀物组分（{'、'.join(c.name for c in suspects[:3])}），"
            "金属腐蚀性只能按 UN C.1 试验判定（钢 / 铝腐蚀速率 > 6.25 mm/年），pH 不能替代。"))
    return out


def screening(components: list[Component], props: MixtureProperties, rs: RuleSet) -> RuleOutput:
    p = rs["physical_screening"]
    out = RuleOutput()
    for hazard_class in p["classes"]:
        hits = [c for c in components if c.of(hazard_class) and c.pct >= float(p["screening_pct"])]
        if not hits:
            continue
        name = BY_CODE[hazard_class].names[0]
        out.gaps.append(DataGap(
            f"physical_test_required:{hazard_class}", hazard_class,
            f"含{name}组分（{'、'.join(f'{c.name} {fmt(c.pct)}%' for c in hits[:3])}），"
            f"混合物是否属于{name}须按 {BY_CODE[hazard_class].part} 试验或专家判断确定，不能由组成计算。",
            "blocking"))
        out.trace.append(TraceEntry(hazard_class, None, "requires_test", f"GB {BY_CODE[hazard_class].part}",
                                    f"{name}：不由组成计算，需试验确认",
                                    [contributor(c, c.worst(hazard_class).category) for c in hits]))
    if props.phys_state in ("gas", "aerosol"):
        out.gaps.append(DataGap("gas_or_aerosol", None,
                                "气体 / 气溶胶产品的易燃性、加压气体等分类需按 GB 30000.3/.4/.6 单独评估。", "blocking"))
    return out
