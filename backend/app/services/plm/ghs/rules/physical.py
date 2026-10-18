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


# GHS 2.3.1.2 flammable components for the aerosol content test.
_FLAMMABLE_COMPONENT_CLASSES = (
    "flammable_gas", "flammable_liquid", "flammable_solid", "self_reactive",
    "pyrophoric_liquid", "pyrophoric_solid", "self_heating", "water_reactive", "organic_peroxide",
)


def aerosol(components: list[Component], props: MixtureProperties, rs: RuleSet) -> RuleOutput:
    """Flammable aerosol (GB 30000.4): decided by test, never by summation alone.

    Applies only when ``phys_state == "aerosol"``. The most severe category whose
    criteria are met wins; a product with flammable components but no test data
    at all raises a blocking data gap instead of a guess.
    """
    out = RuleOutput()
    if props.phys_state != "aerosol":
        return out
    p = rs["aerosol"]
    flammable_pct = sum(c.pct for c in components
                        if any(c.of(hc) for hc in _FLAMMABLE_COMPONENT_CLASSES))
    hoc = props.heat_of_combustion_kj_g
    ign = props.ignition_distance_cm
    foam = props.foam_test_result or {}
    fh = foam.get("flame_height_cm")
    fd = foam.get("flame_duration_s")
    has_test = hoc is not None or ign is not None or (fh is not None and fd is not None)
    if not has_test:
        if flammable_pct > 0:
            out.gaps.append(DataGap(
                "aerosol_test_data_missing", "aerosol",
                f"气溶胶含易燃组分 {fmt(flammable_pct)}%，缺少燃烧热 / 点火距离 / 泡沫试验数据，"
                "无法按 GB 30000.4 判定易燃气溶胶类别。", "blocking"))
        else:
            out.trace.append(TraceEntry("aerosol", None, "not_applicable", "GB 30000.4",
                                        "气溶胶不含易燃组分 → 不分类为易燃气溶胶"))
        return out
    if flammable_pct <= 0:
        out.trace.append(TraceEntry("aerosol", None, "not_applicable", "GB 30000.4",
                                    "气溶胶不含易燃组分 → 不分类为易燃气溶胶"))
        return out

    cat, why = None, ""
    # Category 1: any one of the three test routes.
    if hoc is not None and flammable_pct >= float(p["flammable_pct"]["cat1_min"]) \
            and hoc >= float(p["heat_of_combustion_kj_g"]["cat1"]):
        cat, why = "1", (f"易燃组分 {fmt(flammable_pct)}% ≥ 85%，燃烧热 {fmt(hoc)} kJ/g ≥ 30 kJ/g")
    elif ign is not None and ign >= float(p["ignition_distance_cm"]["cat1_min"]):
        cat, why = "1", f"喷雾点火距离 {fmt(ign)} cm ≥ 75 cm"
    elif fh is not None and fd is not None and any(
            fh >= float(r["height_cm"]) and fd >= float(r["duration_s"]) for r in p["foam"]["cat1"]):
        cat, why = "1", f"泡沫试验火焰高度 {fmt(fh)} cm、持续 {fmt(fd)} s，达到类别 1 判据"
    # Category 2: any one of the three test routes (only when category 1 is not met).
    if cat is None:
        if hoc is not None and hoc >= float(p["heat_of_combustion_kj_g"]["cat2"]):
            cat, why = "2", f"燃烧热 {fmt(hoc)} kJ/g ≥ 20 kJ/g（未达到类别 1）"
        elif ign is not None and ign >= float(p["ignition_distance_cm"]["cat2_min"]):
            cat, why = "2", f"喷雾点火距离 {fmt(ign)} cm ≥ 15 cm（未达到类别 1）"
        elif fh is not None and fd is not None \
                and fh >= float(p["foam"]["cat2"]["height_cm"]) and fd >= float(p["foam"]["cat2"]["duration_s"]):
            cat, why = "2", f"泡沫试验火焰高度 {fmt(fh)} cm、持续 {fmt(fd)} s（未达到类别 1）"
    # Category 3: ≤ 1 % flammable components with heat of combustion < 20 kJ/g.
    if cat is None and hoc is not None and flammable_pct <= float(p["flammable_pct"]["cat3_max"]) \
            and hoc < float(p["heat_of_combustion_kj_g"]["cat2"]):
        cat, why = "3", (f"易燃组分 {fmt(flammable_pct)}% ≤ 1%，燃烧热 {fmt(hoc)} kJ/g < 20 kJ/g"
                         "，且未达到类别 1 / 2")
    out.trace.append(TraceEntry("aerosol", cat, "measured", "GB 30000.4",
                                (why + f" → 类别 {cat}") if cat else
                                (why or "未达到类别 1 / 2 / 3 判据") + " → 不分类",
                                numbers={"flammable_pct": round(flammable_pct, 4),
                                         "heat_of_combustion_kj_g": hoc,
                                         "ignition_distance_cm": ign,
                                         "foam_flame_height_cm": fh,
                                         "foam_flame_duration_s": fd}))
    if cat:
        out.outcomes.append(outcome("aerosol", cat, "measured"))
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
    if props.phys_state == "gas":
        # Gases still need a separate GB 30000.3 / .5 / .6 assessment. Aerosols no
        # longer hit this gap: aerosol() above classifies them from test data.
        out.gaps.append(DataGap("gas_or_aerosol", None,
                                "气体产品的易燃性、加压气体等分类需按 GB 30000.3/.5/.6 单独评估。", "blocking"))
    return out
