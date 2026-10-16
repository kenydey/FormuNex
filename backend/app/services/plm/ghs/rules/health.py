"""Health hazards of mixtures: GB 30000.18 – 30000.27."""
from __future__ import annotations

from ..model import Component, DataGap, MixtureProperties, TraceEntry, severity
from ..ruleset import RuleSet
from .common import RuleOutput, contributor, fmt, outcome

_ROUTES = (("oral", "acute_tox_oral"), ("dermal", "acute_tox_dermal"), ("inhal", "acute_tox_inhal"))
_ATE_UNITS = {"oral": "mg/kg", "dermal": "mg/kg", "gas": "ppmV", "vapour": "mg/L", "dust_mist": "mg/L"}


# --------------------------------------------------------------------------- acute toxicity


def acute_toxicity(components: list[Component], props: MixtureProperties, rs: RuleSet) -> tuple[RuleOutput, dict]:
    """ATE additivity: 100 / ATEmix = Σ Ci / ATEi, with the unknown-ingredient correction."""
    p = rs["acute_toxicity"]
    out = RuleOutput()
    unknown_share: dict[str, float] = {}
    cutoff = float(p["relevant_cutoff_pct"])
    cats = {"1", "2", "3", "4"} | ({"5"} if p["include_category_5"] else set())
    unknown = sum(c.pct for c in components if not c.assessed and c.pct >= cutoff)
    for route, hazard_class in _ROUTES:
        form = props.effective_inhalation_form() if route == "inhal" else route
        unit = _ATE_UNITS[form]
        term = 0.0
        contributors = []
        for c in components:
            if c.pct < cutoff:
                continue
            h = c.worst(hazard_class)
            if h is None or h.category not in cats:
                continue
            if h.ate_value and (h.ate_unit or unit) == unit:
                ate, how = float(h.ate_value), f"ATE {fmt(h.ate_value)} {unit}"
            else:
                ate = float(p["point_estimates"][form][h.category])
                how = f"类别 {h.category} 换算值 {fmt(ate)} {unit}"
            term += c.pct / ate
            contributors.append(contributor(c, h.category, how))
        if unknown > 0:
            unknown_share[route] = round(unknown, 4)
        if not contributors:
            continue
        if unknown > float(p["unknown_threshold_pct"]):
            ate_mix = (100.0 - unknown) / term
            formula = f"(100 − {fmt(unknown)}) / ATEmix = Σ Ci/ATEi = {fmt(term)}"
        else:
            ate_mix = 100.0 / term
            formula = f"100 / ATEmix = Σ Ci/ATEi = {fmt(term)}"
        category = next((cat for limit, cat in p["category_limits"][form]
                         if ate_mix <= float(limit) and cat in cats), None)
        route_zh = {"oral": "经口", "dermal": "经皮", "inhal": f"吸入（{form}）"}[route]
        out.trace.append(TraceEntry(
            hazard_class=hazard_class, outcome=category, rule="ate_additivity", standard_ref="GB 30000.18",
            explanation=(f"急性毒性-{route_zh}：{formula}，ATEmix = {fmt(ate_mix)} {unit}"
                         + (f" → 类别 {category}" if category else " → 不分类")),
            contributors=contributors, numbers={"ate_mix": ate_mix, "unit": unit, "unknown_pct": unknown},
        ))
        if category:
            out.outcomes.append(outcome(hazard_class, category, "calculation"))
    if unknown > float(p["unknown_threshold_pct"]):
        out.gaps.append(DataGap(
            "unknown_acute_toxicity", None,
            f"混合物中 {fmt(unknown)}% 的组分急性毒性未知，SDS / 标签须注明（GB 30000.18）。",
        ))
    return out, unknown_share


# --------------------------------------------------------------------------- skin / eye


def _non_additive(components, hazard_class, corrosive_pct, irritant_pct):
    """Ingredients for which additivity does not apply (strong acids / bases, Table 3.2.4)."""
    hit_cat, hits = None, []
    for c in components:
        for h in c.of(hazard_class):
            if not h.non_additive:
                continue
            if h.category.startswith("1") and c.pct >= corrosive_pct:
                hit_cat = "1"
                hits.append(contributor(c, h.category, f"不适用加和法，≥{fmt(corrosive_pct)}%"))
            elif h.category.startswith("2") and c.pct >= irritant_pct and hit_cat is None:
                hit_cat = "2"
                hits.append(contributor(c, h.category, f"不适用加和法，≥{fmt(irritant_pct)}%"))
    return hit_cat, hits


def skin_corrosion(components: list[Component], props: MixtureProperties, rs: RuleSet) -> RuleOutput:
    p = rs["skin_corrosion"]
    out = RuleOutput()
    candidates: list[tuple[str, str, str, list]] = []  # (category, basis, explanation, contributors)

    if props.ph is not None and (props.ph <= p["ph_low"] or props.ph >= p["ph_high"]):
        candidates.append(("1", "ph", f"混合物 pH = {props.ph}（≤{p['ph_low']} 或 ≥{p['ph_high']}），按极端 pH 判为类别 1"
                                          "（未考虑酸/碱储备，可经试验数据推翻）", []))
        out.gaps.append(DataGap("ph_without_reserve", "skin_corrosion",
                                "按极端 pH 判定皮肤腐蚀类别 1；如有酸/碱储备或体外试验数据可复核。"))

    cat, hits = _non_additive(components, "skin_corrosion", p["non_additive_corrosive_pct"],
                              p["non_additive_irritant_pct"])
    if cat:
        candidates.append((cat, "concentration_limit", f"含不适用加和法的组分 → 类别 {cat}", hits))

    cat1_ratio = 0.0
    cat2_score = 0.0
    sub_ratio = {"1A": 0.0, "1B": 0.0, "1C": 0.0}
    cat1_all_sub = True
    contributors = []
    for c in components:
        h = c.worst("skin_corrosion")
        if h is None or h.non_additive:
            continue
        if h.category.startswith("1"):
            limit = h.scl_for(h.category) or h.scl_for("1") or float(p["cat1_limit_pct"])
            ratio = c.pct / limit
            cat1_ratio += ratio
            cat2_score += p["cat1_weight"] * c.pct
            if h.category in sub_ratio:
                sub_ratio[h.category] += ratio
            else:
                cat1_all_sub = False
            contributors.append(contributor(c, h.category))
        elif h.category == "2":
            limit2 = h.scl_for("2") or float(p["cat2_limit_pct"])
            cat2_score += c.pct * float(p["cat2_limit_pct"]) / limit2
            contributors.append(contributor(c, "2"))
    if contributors:
        if cat1_ratio >= 1 - 1e-9:
            sub = "1"
            if cat1_all_sub and any(sub_ratio.values()):
                if sub_ratio["1A"] >= 1 - 1e-9:
                    sub = "1A"
                elif sub_ratio["1A"] + sub_ratio["1B"] >= 1 - 1e-9:
                    sub = "1B"
                else:
                    sub = "1C"
            candidates.append((sub, "calculation",
                               f"加和法：Σ 类别1 组分 Ci/限值 = {fmt(cat1_ratio)} ≥ 1 → 类别 {sub}", contributors))
        elif cat2_score >= float(p["cat2_limit_pct"]) - 1e-9:
            candidates.append(("2", "calculation",
                               f"加和法：10×Σ类别1 + Σ类别2 = {fmt(cat2_score)}% ≥ {p['cat2_limit_pct']}% → 类别 2",
                               contributors))
        else:
            out.trace.append(TraceEntry("skin_corrosion", None, "additivity", "GB 30000.19",
                                        f"加和法：10×Σ类别1 + Σ类别2 = {fmt(cat2_score)}% < {p['cat2_limit_pct']}% → 不分类",
                                        contributors))
    return _pick("skin_corrosion", candidates, out, "GB 30000.19")


def eye_damage(components: list[Component], props: MixtureProperties, rs: RuleSet,
               skin_category: str | None) -> RuleOutput:
    p = rs["eye_damage"]
    out = RuleOutput()
    candidates: list[tuple[str, str, str, list]] = []
    if skin_category and skin_category.startswith("1"):
        candidates.append(("1", "implied", f"混合物皮肤腐蚀类别 {skin_category}，视为严重眼损伤类别 1", []))
    sp = rs["skin_corrosion"]
    if props.ph is not None and (props.ph <= sp["ph_low"] or props.ph >= sp["ph_high"]):
        candidates.append(("1", "ph", f"混合物 pH = {props.ph}，按极端 pH 判为严重眼损伤类别 1", []))
    for hazard_class in ("skin_corrosion", "eye_damage"):
        cat, hits = _non_additive(components, hazard_class, p["non_additive_corrosive_pct"],
                                  p["non_additive_irritant_pct"])
        if cat:
            candidates.append((cat, "concentration_limit", f"含不适用加和法的组分 → 类别 {cat}", hits))

    cat1_ratio = 0.0
    score2 = 0.0
    contributors = []
    only_2b = True
    for c in components:
        skin = c.worst("skin_corrosion")
        eye = c.worst("eye_damage")
        if skin and skin.non_additive:
            skin = None
        if eye and eye.non_additive:
            eye = None
        if (skin and skin.category.startswith("1")) or (eye and eye.category == "1"):
            h = eye if (eye and eye.category == "1") else skin
            limit = h.scl_for("1") or float(p["cat1_limit_pct"])
            cat1_ratio += c.pct / limit
            score2 += p["cat1_weight"] * c.pct
            only_2b = False
            contributors.append(contributor(c, f"{'眼' if h is eye else '皮肤'} {h.category}"))
        elif eye and eye.category in ("2", "2A", "2B"):
            limit2 = eye.scl_for(eye.category) or float(p["cat2_limit_pct"])
            score2 += c.pct * float(p["cat2_limit_pct"]) / limit2
            only_2b = only_2b and eye.category == "2B"
            contributors.append(contributor(c, f"眼 {eye.category}"))
    if contributors:
        if cat1_ratio >= 1 - 1e-9:
            candidates.append(("1", "calculation",
                               f"加和法：Σ(皮肤1 + 眼1) Ci/限值 = {fmt(cat1_ratio)} ≥ 1 → 类别 1", contributors))
        elif score2 >= float(p["cat2_limit_pct"]) - 1e-9:
            cat = "2B" if only_2b else "2A"
            candidates.append((cat, "calculation",
                               f"加和法：10×Σ(皮肤1 + 眼1) + Σ眼2 = {fmt(score2)}% ≥ {p['cat2_limit_pct']}% → 类别 {cat}",
                               contributors))
        else:
            out.trace.append(TraceEntry("eye_damage", None, "additivity", "GB 30000.20",
                                        f"加和法：10×Σ(皮肤1 + 眼1) + Σ眼2 = {fmt(score2)}% → 不分类", contributors))
    return _pick("eye_damage", candidates, out, "GB 30000.20")


def _pick(hazard_class, candidates, out: RuleOutput, ref: str) -> RuleOutput:
    """Keep the most severe candidate; every candidate stays in the trace."""
    if not candidates:
        return out
    best = min(candidates, key=lambda c: severity(c[0]))
    for cat, basis, text, contributors in candidates:
        out.trace.append(TraceEntry(hazard_class, cat, basis, ref, text, contributors))
    out.outcomes.append(outcome(hazard_class, best[0], best[1]))
    return out


# --------------------------------------------------------------------------- concentration-limit classes


def _threshold_class(components, hazard_class, limits: dict[str, float], ref: str,
                     category_map=None) -> RuleOutput:
    """Generic concentration limits: an ingredient of category X at ≥ limit[X] puts the mixture in X."""
    out = RuleOutput()
    triggered: dict[str, list] = {}
    for c in components:
        for h in c.of(hazard_class):
            limit = h.scl_for(h.category)
            if limit is None:
                limit = limits.get(h.category)
            if limit is None:
                continue
            if c.pct >= float(limit) - 1e-9:
                cat = (category_map or {}).get(h.category, h.category)
                triggered.setdefault(cat, []).append(contributor(c, h.category, f"≥ {fmt(limit)}%"))
    if not triggered:
        return out
    # Lactation is an additional category, kept alongside the main one.
    extra = [k for k in triggered if k == "lact"]
    main = [k for k in triggered if k != "lact"]
    for cat in ([min(main, key=severity)] if main else []) + extra:
        out.outcomes.append(outcome(hazard_class, cat, "concentration_limit"))
        out.trace.append(TraceEntry(hazard_class, cat, "concentration_limit", ref,
                                    f"组分浓度达到通用浓度限值 → 类别 {cat}", triggered[cat]))
    return out


def sensitization(components: list[Component], props: MixtureProperties, rs: RuleSet) -> RuleOutput:
    p = rs["sensitization"]
    out = RuleOutput()
    out.extend(_threshold_class(components, "skin_sensitizer",
                                {"1A": p["skin_cat1a_pct"], "1": p["skin_cat1_pct"], "1B": p["skin_cat1_pct"]},
                                "GB 30000.21"))
    resp1 = p["resp_cat1_gas_pct"] if props.phys_state == "gas" else p["resp_cat1_pct"]
    out.extend(_threshold_class(components, "resp_sensitizer",
                                {"1A": p["resp_cat1a_pct"], "1": resp1, "1B": resp1}, "GB 30000.21"))
    return out


def cmr(components: list[Component], props: MixtureProperties, rs: RuleSet) -> RuleOutput:
    out = RuleOutput()
    for hazard_class, ref in (("mutagenicity", "GB 30000.22"), ("carcinogenicity", "GB 30000.23"),
                              ("reproductive", "GB 30000.24")):
        out.extend(_threshold_class(components, hazard_class, rs["cmr"][hazard_class], ref))
    return out


def stot(components: list[Component], props: MixtureProperties, rs: RuleSet) -> RuleOutput:
    p = rs["stot"]
    out = RuleOutput()
    for hazard_class, ref in (("stot_se", "GB 30000.25"), ("stot_re", "GB 30000.26")):
        triggered: dict[str, list] = {}
        for c in components:
            h = next((x for x in sorted(c.of(hazard_class), key=lambda x: severity(x.category))
                      if x.category in ("1", "2")), None)
            if h is None:
                continue
            if h.category == "1" and c.pct >= (h.scl_for("1") or p["cat1_pct"]):
                triggered.setdefault("1", []).append(contributor(c, "1", f"≥ {p['cat1_pct']}%"))
            elif h.category == "1" and c.pct >= p["cat1_to_cat2_pct"]:
                triggered.setdefault("2", []).append(contributor(c, "1", f"{p['cat1_to_cat2_pct']}–{p['cat1_pct']}%"))
            elif h.category == "2" and c.pct >= (h.scl_for("2") or p["cat2_pct"]):
                triggered.setdefault("2", []).append(contributor(c, "2", f"≥ {p['cat2_pct']}%"))
        if triggered:
            cat = min(triggered, key=severity)
            out.outcomes.append(outcome(hazard_class, cat, "concentration_limit"))
            out.trace.append(TraceEntry(hazard_class, cat, "concentration_limit", ref,
                                        f"组分浓度达到限值 → 类别 {cat}", triggered[cat]))
    # STOT SE 3: additive per effect (respiratory irritation / narcotic effects).
    for effect, name in (("H335", "呼吸道刺激"), ("H336", "麻醉效应")):
        total, contributors = 0.0, []
        for c in components:
            if any(effect in h.effects() for h in c.of("stot_se") if h.category == "3"):
                total += c.pct
                contributors.append(contributor(c, "3", name))
        if not contributors:
            continue
        hit = total >= p["se3_sum_pct"] - 1e-9
        out.trace.append(TraceEntry("stot_se", "3" if hit else None, "additivity", "GB 30000.25",
                                    f"特异性靶器官毒性-一次接触 类别3（{name}）：Σ = {fmt(total)}% "
                                    + (f"≥ {p['se3_sum_pct']}% → 类别 3" if hit else f"< {p['se3_sum_pct']}% → 不分类"),
                                    contributors))
        if hit:
            out.outcomes.append(outcome("stot_se", "3", "calculation", effect=effect))
    return out


def aspiration(components: list[Component], props: MixtureProperties, rs: RuleSet) -> RuleOutput:
    p = rs["aspiration"]
    out = RuleOutput()
    levels = [("1", p["cat1_sum_pct"], p["cat1_max_viscosity_40c"])]
    if p["include_category_2"]:
        levels.append(("2", p["cat2_sum_pct"], p["cat2_max_viscosity_40c"]))
    for cat, limit, max_visc in levels:
        members = [c for c in components if any(h.category == cat for h in c.of("aspiration"))]
        total = sum(c.pct for c in members)
        if not members or total < limit - 1e-9:
            continue
        contributors = [contributor(c, cat) for c in members]
        v = props.kinematic_viscosity_40c
        if v is None:
            out.gaps.append(DataGap("viscosity_missing", "aspiration",
                                    f"吸入危害类别 {cat} 组分合计 {fmt(total)}%，缺少 40 ℃ 运动黏度，无法判定（GB 30000.27）。",
                                    "blocking"))
            out.trace.append(TraceEntry("aspiration", None, "data_gap", "GB 30000.27",
                                        f"Σ类别{cat} = {fmt(total)}% ≥ {limit}%，但缺少 40 ℃ 运动黏度", contributors))
            return out
        if v <= max_visc:
            out.outcomes.append(outcome("aspiration", cat, "calculation"))
            out.trace.append(TraceEntry("aspiration", cat, "concentration_and_viscosity", "GB 30000.27",
                                        f"Σ类别{cat} = {fmt(total)}% ≥ {limit}% 且 40 ℃ 运动黏度 {v} ≤ {max_visc} mm²/s → 类别 {cat}",
                                        contributors, {"viscosity_40c": v}))
            return out
        out.trace.append(TraceEntry("aspiration", None, "concentration_and_viscosity", "GB 30000.27",
                                    f"Σ类别{cat} = {fmt(total)}% 但 40 ℃ 运动黏度 {v} > {max_visc} mm²/s → 不分类",
                                    contributors, {"viscosity_40c": v}))
    return out
