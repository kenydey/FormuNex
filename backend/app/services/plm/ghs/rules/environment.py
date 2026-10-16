"""Environmental hazards of mixtures: GB 30000.28 (aquatic, summation method) and .29 (ozone)."""
from __future__ import annotations

from ..model import Component, DataGap, MixtureProperties, TraceEntry
from ..ruleset import RuleSet
from .common import RuleOutput, contributor, fmt, outcome


def _relevant(c: Component, category: str, p) -> bool:
    cutoff = p["relevant_cutoff_highly_toxic_pct"] if category == "1" else p["relevant_cutoff_pct"]
    return c.pct >= float(cutoff) - 1e-9


def aquatic(components: list[Component], props: MixtureProperties, rs: RuleSet) -> tuple[RuleOutput, float]:
    p = rs["aquatic"]
    out = RuleOutput()
    limit = float(p["limit_pct"])
    default_m = int(p["default_m_factor"])

    # Acute: S1 = Σ A1·M; Acute 2: 10·S1 + ΣA2; Acute 3: 100·S1 + 10·ΣA2 + ΣA3.
    sums = {"1": 0.0, "2": 0.0, "3": 0.0}
    acute_contrib = []
    for c in components:
        h = c.worst("aquatic_acute")
        if h is None or h.category not in sums or not _relevant(c, h.category, p):
            continue
        m = (h.m_factor_acute or default_m) if h.category == "1" else 1
        sums[h.category] += c.pct * m
        acute_contrib.append(contributor(c, h.category, f"M={m}" if h.category == "1" else ""))
    if acute_contrib:
        s1, s2, s3 = sums["1"], 10 * sums["1"] + sums["2"], 100 * sums["1"] + 10 * sums["2"] + sums["3"]
        cat = "1" if s1 >= limit else "2" if s2 >= limit else "3" if s3 >= limit else None
        out.trace.append(TraceEntry(
            "aquatic_acute", cat, "summation", "GB 30000.28",
            f"急性：Σ(急1×M) = {fmt(s1)}%，10×Σ(急1×M)+Σ急2 = {fmt(s2)}%，100×Σ(急1×M)+10×Σ急2+Σ急3 = {fmt(s3)}%"
            + (f" → 类别 {cat}" if cat else f"（均 < {limit}%）→ 不分类"),
            acute_contrib, {"s1": s1, "s2": s2, "s3": s3},
        ))
        if cat:
            out.outcomes.append(outcome("aquatic_acute", cat, "calculation"))

    # Chronic: C1 = ΣC1·M; C2 = 10·C1 + ΣC2; C3 = 100·C1 + 10·ΣC2 + ΣC3; C4 = ΣC1+C2+C3+C4.
    sums = {"1": 0.0, "2": 0.0, "3": 0.0, "4": 0.0}
    raw = {"1": 0.0, "2": 0.0, "3": 0.0, "4": 0.0}
    chronic_contrib = []
    for c in components:
        h = c.worst("aquatic_chronic")
        if h is None or h.category not in sums or not _relevant(c, h.category, p):
            continue
        m = (h.m_factor_chronic or default_m) if h.category == "1" else 1
        sums[h.category] += c.pct * m
        raw[h.category] += c.pct
        chronic_contrib.append(contributor(c, h.category, f"M={m}" if h.category == "1" else ""))
    if chronic_contrib:
        c1 = sums["1"]
        c2 = 10 * sums["1"] + sums["2"]
        c3 = 100 * sums["1"] + 10 * sums["2"] + sums["3"]
        c4 = sum(raw.values())
        cat = "1" if c1 >= limit else "2" if c2 >= limit else "3" if c3 >= limit else "4" if c4 >= limit else None
        out.trace.append(TraceEntry(
            "aquatic_chronic", cat, "summation", "GB 30000.28",
            f"长期：Σ(慢1×M) = {fmt(c1)}%，10×Σ(慢1×M)+Σ慢2 = {fmt(c2)}%，"
            f"100×Σ(慢1×M)+10×Σ慢2+Σ慢3 = {fmt(c3)}%，Σ慢1..4 = {fmt(c4)}%"
            + (f" → 类别 {cat}" if cat else f"（均 < {limit}%）→ 不分类"),
            chronic_contrib, {"c1": c1, "c2": c2, "c3": c3, "c4": c4},
        ))
        if cat:
            out.outcomes.append(outcome("aquatic_chronic", cat, "calculation"))

    unknown = sum(c.pct for c in components if not c.assessed and c.pct >= float(p["relevant_cutoff_pct"]))
    if unknown > 0 and out.outcomes:
        out.gaps.append(DataGap("unknown_aquatic_hazard", "aquatic_chronic",
                                f"混合物中 {fmt(unknown)}% 的组分对水生环境的危害未知，SDS 须注明（GB 30000.28）。"))
    return out, unknown


def ozone(components: list[Component], props: MixtureProperties, rs: RuleSet) -> RuleOutput:
    out = RuleOutput()
    limit = float(rs["ozone"]["cat1_pct"])
    hits = [contributor(c, "1") for c in components
            if any(h.category == "1" for h in c.of("ozone")) and c.pct >= limit - 1e-9]
    if hits:
        out.outcomes.append(outcome("ozone", "1", "concentration_limit"))
        out.trace.append(TraceEntry("ozone", "1", "concentration_limit", "GB 30000.29",
                                    f"含危害臭氧层类别1 组分 ≥ {limit}% → 类别 1", hits))
    return out
