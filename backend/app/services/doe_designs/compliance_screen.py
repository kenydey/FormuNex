"""Pre-screen DOE runs for compliance before anyone mixes them (方案 §5.3: "DOE 生成后对每个试验点预跑合规评估").

Each run becomes a composition (varied components + fixed ingredients), is
classified with the GB 30000 engine, and checked against the project's red
lines:

* forbidden / limited substances (``{"cas", "max_pct", "name", "reason"}`` —
  the same shape as ``spec_snapshot.red_lines`` used by the release gate),
  counted after raw materials are exploded into substances;
* RoHS restricted substances;
* target GHS constraints: forbidden pictograms (e.g. GHS06 / GHS08), a
  forbidden signal word (e.g. 危险), a maximum hazard score.

Violating runs are flagged (``ok = false`` + reasons) so the UI can drop or
replace them; every run gets a hazard score for ranking.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ..compliance_rules import rohs_restricted
from ..plm.ghs import service as ghs_service
from ..plm.ghs.hazard_score import hazard_score
from ..plm.ghs.inputs import build_components
from ..plm.ghs.ruleset import load_ruleset

TOTAL_TOLERANCE = 0.05


@dataclass
class Ingredient:
    name: str
    cas: str | None = None
    material_id: str | None = None
    substance_id: str | None = None


@dataclass
class RedLines:
    substances: list[dict[str, Any]] = field(default_factory=list)
    forbidden_pictograms: list[str] = field(default_factory=list)
    forbidden_signal_word: str | None = None  # "危险" forbids Danger; "警告" forbids any signal word
    max_hazard_score: float | None = None
    check_rohs: bool = True


def _item(ing: Ingredient, pct: float) -> dict[str, Any]:
    out: dict[str, Any] = {"name": ing.name, "pct": pct}
    for k in ("cas", "material_id", "substance_id"):
        if getattr(ing, k):
            out[k] = getattr(ing, k)
    return out


def screen(session: Session, runs: list[dict[str, Any]], ingredients: dict[str, Ingredient], *,
           fixed: list[tuple[Ingredient, float]] | None = None, red_lines: RedLines | None = None,
           properties: dict[str, Any] | None = None) -> dict[str, Any]:
    red = red_lines or RedLines()
    fixed = fixed or []
    rs = load_ruleset()
    cache: dict[str, dict[str, Any]] = {}
    results = []
    for idx, run in enumerate(runs, start=1):
        items = [_item(ingredients[name], float(run[name])) for name in ingredients if name in run]
        items += [_item(ing, pct) for ing, pct in fixed]
        total = sum(i["pct"] for i in items)
        key = json.dumps(sorted((i["name"], round(i["pct"], 6)) for i in items), ensure_ascii=False)
        if key not in cache:
            cache[key] = _evaluate(session, items, rs, red, properties, total)
        results.append({"run": run.get("_run", idx), **cache[key]})
    flagged = [r["run"] for r in results if not r["ok"]]
    return {"runs": results, "flagged": flagged, "ok_count": len(results) - len(flagged),
            "rule_set_version": rs.version}


def _evaluate(session: Session, items: list[dict[str, Any]], rs, red: RedLines, properties, total: float
              ) -> dict[str, Any]:
    reasons: list[str] = []
    if abs(total - 100.0) > TOTAL_TOLERANCE:
        reasons.append(f"组成合计 {total:.3f}%，不等于 100%")
    result = ghs_service.classify_items(session, items, properties, include_transport=False)
    cls = result.to_dict()
    built = build_components(session, items, rs)
    by_cas: dict[str, float] = {}
    for comp in built.components:
        if comp.cas:
            by_cas[comp.cas] = by_cas.get(comp.cas, 0.0) + comp.pct
        if red.check_rohs:
            hit = rohs_restricted(comp.name, comp.cas)
            if hit:
                reasons.append(f"{comp.name}：{hit}")
    for item in items:
        if item.get("cas") and item["cas"] not in by_cas:
            by_cas[item["cas"]] = item["pct"]
    for rl in red.substances:
        found = by_cas.get(rl.get("cas") or "", 0.0)
        limit = float(rl.get("max_pct") or 0)
        if rl.get("cas") and found > limit:
            reasons.append(f"{rl.get('name') or rl['cas']}：{found:g}% 超过红线 {limit:g}%"
                           + (f"（{rl['reason']}）" if rl.get("reason") else ""))
    pictos = [p["code"] for p in cls.get("pictograms", [])]
    bad = sorted(set(pictos) & set(red.forbidden_pictograms))
    if bad:
        reasons.append(f"出现禁止的象形图 {', '.join(bad)}")
    sw = cls.get("signal_word")
    if red.forbidden_signal_word and sw and (sw == red.forbidden_signal_word or red.forbidden_signal_word == "警告"):
        reasons.append(f"信号词为“{sw}”")
    score = hazard_score(cls)
    if red.max_hazard_score is not None and score > red.max_hazard_score:
        reasons.append(f"危害评分 {score:g} 超过上限 {red.max_hazard_score:g}")
    return {"ok": not reasons, "reasons": reasons, "hazard_score": score, "signal_word": sw, "pictograms": pictos,
            "h_codes": cls.get("h_codes", []),
            "hazards": [f"{h['hazard_class']}:{h['category']}" for h in cls.get("hazards", [])],
            "data_gaps": len(cls.get("data_gaps", [])),
            "unknown_pct": max((cls.get("unknown_toxicity_pct") or {}).values(), default=0.0)}
