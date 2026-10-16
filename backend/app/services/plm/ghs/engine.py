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
from .label_elements import build_label
from .model import ClassificationResult, Component, DataGap, MixtureProperties, severity
from .rules import environment, health, physical
from .rules.common import RuleOutput
from .ruleset import DEFAULT_RULESET, RuleSet, load_ruleset


def inputs_hash(components: list[Component], props: MixtureProperties, rs: RuleSet) -> str:
    payload = {
        "rule_set": rs.version,
        "components": sorted((asdict(c) for c in components), key=lambda d: (d["cas"] or "", d["name"])),
        "properties": asdict(props),
    }
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
            continue
        clone = Component(**{**asdict(c), "hazards": list(c.hazards), "via": list(c.via)})
        clone.hazards = c.hazards
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
) -> ClassificationResult:
    """``composition_basis="upper_bound"``: contents are range maxima (raw-material SDS ranges),
    so a total above 100 % is expected and only a shortfall is reported."""
    rs = ruleset if isinstance(ruleset, RuleSet) else load_ruleset(ruleset)
    props = props or MixtureProperties()
    comps = _merge_duplicates([c for c in components if c.pct > 0])
    result = ClassificationResult(rule_set_version=rs.version, inputs_hash=inputs_hash(comps, props, rs))

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
    aquatic, unknown_aquatic = environment.aquatic(comps, props, rs)
    out.extend(aquatic)
    out.extend(environment.ozone(comps, props, rs))
    out.extend(physical.flammable_liquid(comps, props, rs))
    out.extend(physical.metal_corrosive(comps, props, rs))
    out.extend(physical.screening(comps, props, rs))

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
