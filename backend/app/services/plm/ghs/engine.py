"""GB 30000 mixture classification: components + mixture properties → hazards,
label elements, data gaps, SDS disclosure list and an auditable trace.

``classify`` is a pure function of its inputs and the rule set; the
``inputs_hash`` it returns identifies a result for caching and for the
release gate's "inputs unchanged since classification" check.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from ..transport import derive_transport
from . import bridging as bridging_mod
from .aqueous import suppressed_screening_classes as aqueous_suppressed_classes
from .bridging import BridgingInput
from .label_elements import build_label
from .model import ClassificationResult, Component, DataGap, MixtureProperties, TraceEntry, severity
from .rules import environment, health, physical
from .rules.common import RuleOutput
from .ruleset import DEFAULT_RULESET, RuleSet, load_ruleset


def inputs_hash(components: list[Component], props: MixtureProperties, rs: RuleSet,
                bridging: BridgingInput | None = None) -> str:
    payload = {
        "rule_set": rs.version,
        # Stage 3 / SR-059: bind the content, not just the version name —
        # a threshold edit that keeps the same version string must still
        # invalidate stored results. One-time consequence: hashes computed
        # under the old scheme never match a fresh hash, so the gate flags
        # stored classifications for recompute once after upgrade (fail
        # closed, intended).
        "rule_set_content": rs.content_hash,
        "components": sorted((asdict(c) for c in components), key=lambda d: (d["cas"] or "", d["name"])),
        "properties": asdict(props),
    }
    if bridging is not None:
        payload["bridging"] = bridging.fingerprint()
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _merge_duplicates(components: list[Component]) -> list[Component]:
    """The same substance reached through two raw materials is one ingredient: concentration
    limits apply to its total content, not to each occurrence."""
    merged: dict[str, Component] = {}
    out: list[Component] = []
    for c in components:
        key = c.substance_id or (c.cas or "")
        if not key:
            out.append(c)
            continue
        if key in merged:
            m = merged[key]
            m.pct += c.pct
            m.via.extend(v for v in c.via if v not in m.via)
            m.assessed = m.assessed and c.assessed
            # P1-C2: same substance via two raw materials keeps the UNION of
            # hazards; identical (class, category) pairs merge SCLs (stricter
            # limit wins) and M-factors (larger wins) instead of dropping the
            # second occurrence's hazards.
            for h in c.hazards:
                dup = next((x for x in m.hazards
                            if x.hazard_class == h.hazard_class and x.category == h.category), None)
                if dup is None:
                    m.hazards.append(h)
                    continue
                if h.scl:
                    merged_scl = {str(e.get("category")): e for e in (dup.scl or [])}
                    for e in h.scl:
                        cat = str(e.get("category"))
                        old = merged_scl.get(cat)
                        if old is None or (e.get("min_pct") is not None and
                                            (old.get("min_pct") is None or float(e["min_pct"]) < float(old["min_pct"]))):
                            merged_scl[cat] = e
                    dup.scl = list(merged_scl.values())
                if h.m_factor_acute is not None:
                    dup.m_factor_acute = max(dup.m_factor_acute or 0, h.m_factor_acute)
                if h.m_factor_chronic is not None:
                    dup.m_factor_chronic = max(dup.m_factor_chronic or 0, h.m_factor_chronic)
            continue
        clone = Component(**{**asdict(c), "hazards": list(c.hazards), "via": list(c.via)})
        merged[key] = clone
        out.append(clone)
    return out


def _disclosure(components: list[Component], rs: RuleSet) -> list[dict]:
    p = rs["disclosure"]
    low = set(p["low_classes"])
    rows = []
    for c in components:
        if not c.hazards:
            continue
        threshold = p["low_pct"] if any(h.hazard_class in low for h in c.hazards) else p["default_pct"]
        # Highly toxic to aquatic life with M > 1 matters below 1 % too.
        if any((h.m_factor_acute or 1) > 1 or (h.m_factor_chronic or 1) > 1 for h in c.hazards):
            threshold = p["low_pct"]
        if c.pct >= threshold - 1e-9:
            rows.append({
                "name": c.name, "cas": c.cas, "pct": round(c.pct, 4), "trade_secret": c.trade_secret,
                "hazards": [{"hazard_class": h.hazard_class, "category": h.category, "h_code": h.h_code}
                            for h in sorted(c.hazards, key=lambda h: severity(h.category))],
            })
    return sorted(rows, key=lambda r: -r["pct"])


def classify(
    components: list[Component],
    props: MixtureProperties | None = None,
    *,
    ruleset: str | RuleSet = DEFAULT_RULESET,
    include_transport: bool = True,
    substance_transport: dict[str, dict] | None = None,
    composition_basis: str = "exact",
    bridging: BridgingInput | None = None,
) -> ClassificationResult:
    """``composition_basis="upper_bound"``: contents are range maxima (raw-material SDS ranges),
    so a total above 100 % is expected and only a shortfall is reported.

    ``bridging``: when given, hazard classes the bridge covers take the reference
    mixture's classification instead of the calculated one (GB 30000 / GHS 1.3.2.2).
    """
    rs = ruleset if isinstance(ruleset, RuleSet) else load_ruleset(ruleset)
    props = props or MixtureProperties()
    comps = _merge_duplicates([c for c in components if c.pct > 0])
    result = ClassificationResult(rule_set_version=rs.version,
                                  inputs_hash=inputs_hash(comps, props, rs, bridging))

    total = sum(c.pct for c in comps)
    off = (100.0 - total) if composition_basis == "upper_bound" else abs(total - 100.0)
    if off > 0.5:
        result.data_gaps.append(DataGap(
            "composition_not_100", None, f"组成合计 {total:.2f}%（应为 100%）；分类按给定含量计算。",
            "blocking" if off > 5 else "warning"))
    for c in comps:
        if any(h.conditional for h in c.hazards):
            result.data_gaps.append(DataGap(
                "conditional_substance_classification", None,
                f"{c.label()} 在危化品目录中按形态 / 浓度给出不同分类，已从严取全部类别，请法规专员确认适用条件。"))
    # P1-C11: a minimum-category ("*") row is computed as if exact today
    # with no trace at all; at minimum it must surface as a data gap
    # (same level as conditional), so a reviewer sees the uncertainty.
    seen_min: set[tuple[str, str, str]] = set()
    for c in comps:
        for h in c.hazards:
            key = (c.name, h.hazard_class, h.category)
            if h.is_minimum and key not in seen_min:
                seen_min.add(key)
                result.data_gaps.append(DataGap(
                    "minimum_category", h.hazard_class,
                    f"{c.label()} 的 {h.hazard_class} 类别 {h.category} 为目录最低类别"
                    f"（至少该类别），当前按精确类别计算，请法规专员复核。"))

    out = RuleOutput()
    acute, unknown = health.acute_toxicity(comps, props, rs)
    out.extend(acute)
    skin = health.skin_corrosion(comps, props, rs)
    out.extend(skin)
    skin_cat = skin.outcomes[0].category if skin.outcomes else None
    out.extend(health.eye_damage(comps, props, rs, skin_cat))
    out.extend(health.sensitization(comps, props, rs))
    out.extend(health.cmr(comps, props, rs))
    out.extend(health.stot(comps, props, rs))
    out.extend(health.aspiration(comps, props, rs))
    # A rule set may disable a hazard group it does not adopt (OSHA HazCom
    # 2012: aquatic / ozone). Absent ``enabled`` means enabled (GB behaviour).
    if (rs.get("aquatic") or {}).get("enabled", True):
        aquatic, unknown_aquatic = environment.aquatic(comps, props, rs)
        out.extend(aquatic)
    else:
        unknown_aquatic = 0.0
    if (rs.get("ozone") or {}).get("enabled", True):
        out.extend(environment.ozone(comps, props, rs))
    out.extend(physical.flammable_liquid(comps, props, rs))
    out.extend(physical.aerosol(comps, props, rs))
    out.extend(physical.metal_corrosive(comps, props, rs))
    out.extend(physical.screening(comps, props, rs))

    if bridging is not None:
        bridged = bridging_mod.apply(comps, out.outcomes, bridging, rs)
        out.outcomes = bridged.outcomes
        out.trace.extend(bridged.trace)
        out.gaps.extend(bridged.gaps)

    # G3 aqueous suppression: in a water-dominant mixture (water strictly
    # largest, no FP < 60 °C), solute physical hazards are not inherited —
    # in this engine that surfaces only as screening gaps, so drop those
    # gaps for the suppress-set classes and leave a trace entry. Measured
    # outcomes (e.g. flammable_liquid from a measured flash point) are
    # never suppressed by this rule.
    suppressed = aqueous_suppressed_classes(comps, props)
    if suppressed:
        out.gaps = [g for g in out.gaps
                    if not (g.code.startswith("physical_test_required:")
                            and g.hazard_class in suppressed)]
        out.trace.append(TraceEntry(
            "aqueous_suppression", None, "suppressed", "水溶液规则",
            # P3-C9: 措辞与 is_aqueous 实现对齐 —— 水组分之和大于
            # 最大单组分（不是“水为唯一最大组分”）。
            f"水组分之和大于最大单组分且无低于 60 ℃ 的实测闪点，溶质物理危害不继承："
            f"已抑制筛查类 {sorted(suppressed)} 的试验缺口（实测物性判定不受影响）"))

    result.hazards = out.outcomes
    result.trace = out.trace
    result.data_gaps.extend(out.gaps)
    result.unknown_toxicity_pct = {**unknown, **({"aquatic": round(unknown_aquatic, 4)} if unknown_aquatic else {})}
    label = build_label(out.outcomes, max_label_p=int(rs["label"]["max_p_statements"]))
    result.pictograms = label["pictograms"]
    result.signal_word = label["signal_word"]
    result.h_codes = label["h_codes"]
    result.p_codes = label["p_codes"]
    result.label_p_codes = label["label_p_codes"]
    result.disclosure = _disclosure(comps, rs)
    if include_transport:
        result.transport = derive_transport(result, props, comps, substance_entries=substance_transport)
    return result
