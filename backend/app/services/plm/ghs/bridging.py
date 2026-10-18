"""Bridging principles (GB 30000.18-.28 / GHS 1.3.2.2 and the per-chapter rules).

A mixture that was not itself tested can take the classification of a
*reference mixture* whose classification is established (tested, or signed
off), when the two are related in one of the ways GHS names:

* ``dilution`` — the target is the reference diluted with a diluent whose
  category in the hazard class is equal to or lower than that of the least
  hazardous original ingredient → same category as the reference;
* ``batching`` — another production batch of the same product (same
  ingredients, contents within the batch tolerance) → same classification;
* ``concentration`` — the reference is in the highest category (1 / 1A) and the
  target holds more of the ingredients in that category → same category;
* ``interpolation`` — two references in the same category with identical
  ingredients; every hazard-relevant content of the target lies between them →
  same category;
* ``substantially_similar`` — the target swaps one ingredient of the reference
  for another at the same content and with the same classification → same
  classification.

Everything here is decided from composition data; what GHS leaves to expert
judgement ("the diluent is not expected to affect the hazards of other
ingredients", same manufacturer and process) is reported as a warning to
confirm. A hazard class whose conditions are not met keeps the calculated
result, and the trace says why. Which principle applies to which hazard
class, and the content tolerances, live in the rule set (``bridging``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..regulatory.hazard_classes import BY_CODE
from .model import Component, DataGap, HazardOutcome, TraceEntry, severity
from .ruleset import RuleSet

PRINCIPLES = ("dilution", "batching", "concentration", "interpolation", "substantially_similar")
PRINCIPLE_ZH = {
    "dilution": "稀释", "batching": "批次", "concentration": "高危险混合物的浓缩",
    "interpolation": "同一类别内插", "substantially_similar": "实质上类似的混合物",
}
_HIGHEST = {"1", "1A"}


class BridgingError(ValueError):
    """The bridging request itself is unusable (unknown principle, missing reference)."""


@dataclass
class ReferenceMixture:
    components: list[Component]
    hazards: list[HazardOutcome]
    ref_id: str = ""  # classification result id / formulation version id, echoed into the trace
    label: str = ""

    def of(self, hazard_class: str) -> list[HazardOutcome]:
        return [h for h in self.hazards if h.hazard_class == hazard_class]

    def category(self, hazard_class: str) -> str | None:
        hs = self.of(hazard_class)
        return min((h.category for h in hs), key=severity) if hs else None


@dataclass
class BridgingInput:
    principle: str
    references: list[ReferenceMixture] = field(default_factory=list)

    def validate(self) -> None:
        if self.principle not in PRINCIPLES:
            raise BridgingError(f"未知的桥接原则 {self.principle!r}（可选：{'、'.join(PRINCIPLES)}）")
        need = 2 if self.principle == "interpolation" else 1
        if len(self.references) != need:
            raise BridgingError(f"桥接原则「{PRINCIPLE_ZH[self.principle]}」需要 {need} 个参考混合物")

    def fingerprint(self) -> dict[str, Any]:
        """What the result depends on (part of ``inputs_hash``)."""
        return {
            "principle": self.principle,
            "references": [{
                "id": r.ref_id,
                "hazards": sorted((h.hazard_class, h.category, h.effect) for h in r.hazards),
                "components": sorted((_key(c), round(c.pct, 6)) for c in r.components),
            } for r in self.references],
        }


@dataclass
class BridgingOutput:
    outcomes: list[HazardOutcome]
    trace: list[TraceEntry] = field(default_factory=list)
    gaps: list[DataGap] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)  # hazard classes taken from the reference


def _key(c: Component) -> str:
    return c.substance_id or c.cas or c.name


def _contents(components: list[Component]) -> dict[str, float]:
    out: dict[str, float] = {}
    for c in components:
        out[_key(c)] = out.get(_key(c), 0.0) + c.pct
    return out


def _by_key(components: list[Component]) -> dict[str, Component]:
    return {_key(c): c for c in components}


def _signature(c: Component | None, hazard_class: str) -> str | None:
    """The ingredient's category in *hazard_class* (None = not classified in it)."""
    h = c.worst(hazard_class) if c else None
    return h.category if h else None


def applicable_classes(rs: RuleSet, principle: str) -> list[str]:
    section = rs.get("bridging")
    if not section:
        raise BridgingError("规则集缺少 bridging 配置，无法使用桥接原则")
    return list((section.get("principles") or {}).get(principle) or [])


# --------------------------------------------------------------------------- per-principle checks
# Each returns {hazard_class: (ok, explanation)} plus warnings that apply to the whole bridge.


def _check_batching(target, ref: ReferenceMixture, classes, p) -> tuple[dict, list[str]]:
    tol = float(p["batching_tolerance_pct"])
    t, r = _contents(target), _contents(ref.components)
    problems = []
    if set(t) != set(r):
        problems.append("组分不同：" + "、".join(sorted(set(t) ^ set(r)))[:200])
    worst = max((abs(t.get(k, 0) - r.get(k, 0)) for k in set(t) | set(r)), default=0.0)
    if worst > tol + 1e-9:
        problems.append(f"含量差异最大 {worst:.3g}%，超过批次容差 {tol:g}%")
    why = "；".join(problems) or f"组分相同，含量差异 ≤ {tol:g}%"
    warnings = ["批次桥接：须确认目标批次与参考批次为同一制造商按同一工艺生产，且无影响危害的显著变化。"]
    return {hc: (not problems, why) for hc in classes}, warnings


def _check_dilution(target, ref: ReferenceMixture, classes, p) -> tuple[dict, list[str]]:
    tol = float(p["content_tolerance_pct"])
    t, r = _contents(target), _contents(ref.components)
    tk, rk = _by_key(target), _by_key(ref.components)
    missing = [k for k in r if t.get(k, 0) <= 0]
    if missing or not r:
        why = "目标配方缺少参考配方组分：" + "、".join(rk[k].name for k in missing) if missing else "参考配方没有组成"
        return {hc: (False, why) for hc in classes}, []
    factor = min(t[k] / r[k] for k in r)
    if factor > 1 + 1e-6:
        return {hc: (False, f"目标配方不是参考配方的稀释（稀释系数 {factor:.3g} > 1）") for hc in classes}, []
    # Whatever exceeds factor × reference content is diluent: new ingredients, or more of one already present.
    diluent: dict[str, float] = {k: v for k, v in t.items() if k not in r}
    for k in r:
        excess = t[k] - factor * r[k]
        if excess > tol:
            diluent[k] = excess
    off = abs(sum(diluent.values()) - 100.0 * (1 - factor))
    if off > max(tol, 0.5):
        return {hc: (False, f"稀释剂用量与稀释系数不一致（偏差 {off:.3g}%）") for hc in classes}, []
    unknown = [tk[k].name for k in diluent if not tk[k].assessed]
    if unknown:
        return {hc: (False, f"稀释剂 {'、'.join(unknown)} 危害未知") for hc in classes}, []
    result = {}
    for hc in classes:
        originals = [_signature(rk[k], hc) for k in r if _signature(rk[k], hc)]
        least = max(originals, key=severity) if originals else None
        bad = []
        for k in diluent:
            cat = _signature(tk[k], hc)
            if cat is None:
                continue
            if least is None or severity(cat) < severity(least):
                bad.append(f"{tk[k].name}（类别 {cat}）")
        if bad:
            result[hc] = (False, "稀释剂危害高于原组分中危害最低者（" + (f"类别 {least}" if least else "无该危害")
                          + "）：" + "、".join(bad))
        else:
            result[hc] = (True, f"稀释系数 {factor:.3g}，稀释剂（{'、'.join(tk[k].name for k in diluent) or '—'}）"
                          + "在该危害类别中不高于原组分中危害最低者")
    warnings = ["稀释桥接：须确认稀释剂不会影响其他组分的危害（GHS 1.3.2.2.2）。"]
    return result, warnings


def _check_concentration(target, ref: ReferenceMixture, classes, p) -> tuple[dict, list[str]]:
    tol = float(p["content_tolerance_pct"])
    t = _contents(target)
    rk = _by_key(ref.components)
    tk = _by_key(target)
    result = {}
    for hc in classes:
        cat = ref.category(hc)
        if cat not in _HIGHEST:
            result[hc] = (False, f"参考混合物在该类别为 {cat or '不分类'}，不是最高类别")
            continue
        actives = [k for k, c in rk.items() if (_signature(c, hc) or "") in _HIGHEST]
        new = [tk[k].name for k in tk if k not in rk and (_signature(tk[k], hc) is not None)]
        lower = [rk[k].name for k in actives if t.get(k, 0) < rk[k].pct - tol]
        if new:
            result[hc] = (False, f"目标配方新增了该类别的组分：{'、'.join(new)}")
        elif lower:
            result[hc] = (False, f"类别 {cat} 组分含量未增加：{'、'.join(lower)}")
        else:
            result[hc] = (True, f"参考混合物为类别 {cat}，该类别组分（{'、'.join(rk[k].name for k in actives) or '—'}）"
                          "含量不低于参考混合物")
    return result, []


def _check_interpolation(target, refs: list[ReferenceMixture], classes, p) -> tuple[dict, list[str]]:
    tol = float(p["content_tolerance_pct"])
    a, b = refs
    t, ca, cb = _contents(target), _contents(a.components), _contents(b.components)
    if not (set(t) == set(ca) == set(cb)):
        why = "目标配方与两个参考混合物的组分不完全相同"
        return {hc: (False, why) for hc in classes}, []
    by = {**_by_key(a.components), **_by_key(b.components), **_by_key(target)}
    result = {}
    for hc in classes:
        cat_a, cat_b = a.category(hc), b.category(hc)
        if cat_a is None or cat_a != cat_b:
            result[hc] = (False, f"两个参考混合物类别不同（{cat_a or '不分类'} / {cat_b or '不分类'}）")
            continue
        actives = [k for k in t if _signature(by[k], hc) is not None]
        outside = [by[k].name for k in actives
                   if not (min(ca[k], cb[k]) - tol <= t[k] <= max(ca[k], cb[k]) + tol)]
        if outside:
            result[hc] = (False, f"有效组分含量不在两个参考混合物之间：{'、'.join(outside)}")
        else:
            result[hc] = (True, f"两个参考混合物均为类别 {cat_a}，有效组分（{'、'.join(by[k].name for k in actives) or '—'}）"
                          "含量位于两者之间")
    return result, []


def _check_similar(target, ref: ReferenceMixture, classes, p) -> tuple[dict, list[str]]:
    tol = float(p["content_tolerance_pct"])
    t, r = _contents(target), _contents(ref.components)
    tk, rk = _by_key(target), _by_key(ref.components)
    added, removed = [k for k in t if k not in r], [k for k in r if k not in t]
    if len(added) != 1 or len(removed) != 1:
        why = "实质类似要求目标配方只替换参考配方的一个组分（" + f"新增 {len(added)} 个，去除 {len(removed)} 个）"
        return {hc: (False, why) for hc in classes}, []
    new, old = tk[added[0]], rk[removed[0]]
    if abs(new.pct - old.pct) > tol:
        why = f"替换组分含量不同（{old.name} {old.pct:g}% → {new.name} {new.pct:g}%）"
        return {hc: (False, why) for hc in classes}, []
    drift = [rk[k].name for k in r if k in t and abs(t[k] - r[k]) > tol]
    if drift:
        return {hc: (False, f"其余组分含量有变化：{'、'.join(drift)}") for hc in classes}, []
    if not (new.assessed and old.assessed):
        return {hc: (False, "替换组分危害未知") for hc in classes}, []
    result = {}
    for hc in classes:
        a, c = _signature(old, hc), _signature(new, hc)
        if a != c:
            result[hc] = (False, f"{old.name}（{a or '不分类'}）与 {new.name}（{c or '不分类'}）类别不同")
        else:
            result[hc] = (True, f"{old.name} → {new.name}，含量相同、该危害类别相同（{a or '不分类'}）")
    warnings = [f"实质类似桥接：须确认 {new.name} 不影响其他组分的危害（GHS 1.3.2.2.6）。"]
    return result, warnings


def apply(target: list[Component], calculated: list[HazardOutcome], bridging: BridgingInput, rs: RuleSet
          ) -> BridgingOutput:
    """Replace the calculated outcomes of the hazard classes the bridge covers; keep the rest."""
    bridging.validate()
    section = rs.get("bridging")
    if not section:
        raise BridgingError("规则集缺少 bridging 配置，无法使用桥接原则")
    p = section
    classes = applicable_classes(rs, bridging.principle)
    refs = bridging.references
    check = {
        "dilution": lambda: _check_dilution(target, refs[0], classes, p),
        "batching": lambda: _check_batching(target, refs[0], classes, p),
        "concentration": lambda: _check_concentration(target, refs[0], classes, p),
        "interpolation": lambda: _check_interpolation(target, refs, classes, p),
        "substantially_similar": lambda: _check_similar(target, refs[0], classes, p),
    }[bridging.principle]
    verdicts, warnings = check()
    out = BridgingOutput(outcomes=list(calculated))
    rule = f"bridging:{bridging.principle}"
    ref_ids = [r.ref_id for r in refs if r.ref_id]
    name = PRINCIPLE_ZH[bridging.principle]
    for hc in classes:
        ok, why = verdicts[hc]
        calc = sorted({o.category for o in calculated if o.hazard_class == hc}, key=severity)
        numbers = {"principle": bridging.principle, "references": ref_ids, "calculated": calc}
        part = f"GB {BY_CODE[hc].part}" if hc in BY_CODE else "GB 30000"
        if not ok:
            out.trace.append(TraceEntry(hc, calc[0] if calc else None, f"{rule}:not_applicable", part,
                                        f"桥接原则（{name}）不适用：{why}；按计算法结果", numbers=numbers))
            continue
        bridged = [HazardOutcome(o.hazard_class, o.category, list(o.h_codes), "bridging", o.effect)
                   for o in (refs[0].of(hc) if bridging.principle != "interpolation" else
                             [o for o in refs[0].of(hc) if o.category == refs[0].category(hc)])]
        out.outcomes = [o for o in out.outcomes if o.hazard_class != hc] + bridged
        out.applied.append(hc)
        result = "、".join(sorted({o.category for o in bridged}, key=severity))
        out.trace.append(TraceEntry(hc, bridged[0].category if bridged else None, rule, part,
                                    f"桥接原则（{name}）：{why} → " + (f"类别 {result}" if result else "不分类")
                                    + (f"（计算法为 {'、'.join(calc)}）" if calc and calc != [o.category for o in bridged]
                                       else ""),
                                    numbers=numbers))
    if out.applied:
        out.gaps.extend(DataGap("bridging_confirmation", None, w) for w in warnings)
    else:
        out.gaps.append(DataGap("bridging_not_applicable", None,
                                f"桥接原则（{name}）的条件均不满足，全部按计算法分类。"))
    return out
