"""Build engine components from the database.

A formulation line can name a raw material (exploded through
``material_compositions`` into substances), a substance (by id or CAS), or
carry its own classifications. Substance classifications come from
``substance_classifications`` filtered by source priority
(manual > supplier SDS > catalogue > PubChem > inferred).

Composition ranges are taken at their upper bound by default — the
conservative reading for classification; ``mode="typical"`` uses the typical
value instead. Every assumption that changes a result is reported as a data
gap rather than hidden.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ....db.models import MaterialRow
from ....db.models_regulatory import (
    MaterialCompositionRow,
    SubstanceClassificationRow,
    SubstanceRow,
    SubstanceTransportRow,
)
from ..regulatory import cas as cas_mod
from ..regulatory.source_priority import effective
from .model import Component, ComponentHazard, DataGap
from .ruleset import RuleSet

NOT_CLASSIFIED = "not_classified"  # manual row meaning "assessed, no hazard"


@dataclass
class BuiltInputs:
    components: list[Component] = field(default_factory=list)
    gaps: list[DataGap] = field(default_factory=list)
    transport_entries: dict[str, dict] = field(default_factory=dict)
    # Index of the input item each component came from (parallel to ``components``).
    origins: list[int] = field(default_factory=list)


def _hazards(rows: list[SubstanceClassificationRow]) -> list[ComponentHazard]:
    out = []
    for r in effective(rows):
        if r.hazard_class == NOT_CLASSIFIED:
            continue
        out.append(ComponentHazard(
            hazard_class=r.hazard_class, category=r.category, h_code=r.h_code, scl=r.scl,
            m_factor_acute=r.m_factor_acute, m_factor_chronic=r.m_factor_chronic,
            ate_value=r.ate_value, ate_unit=r.ate_unit, qualifier=r.qualifier or "", source=r.source,
            conditional="条件" in (r.qualifier or ""),
        ))
    return out


class _Lookup:
    """Every database read of the input builder goes through here, so a caching subclass
    (:mod:`.cache`, used by the real-time compliance WebSocket endpoint) can serve repeated lookups from memory."""

    def __init__(self, session: Session, rs: RuleSet) -> None:
        self.session = session
        self.assessed_cas = set(rs["assessed_not_classified_cas"])

    def substance(self, substance_id: str | None = None, cas: str | None = None) -> SubstanceRow | None:
        if substance_id:
            return self.substance_by_id(substance_id)
        norm = cas_mod.normalize(cas) if cas else None
        if not norm:
            return None
        return self.session.execute(select(SubstanceRow).where(SubstanceRow.cas_no == norm)).scalar_one_or_none()

    def substance_by_id(self, substance_id: str) -> SubstanceRow | None:
        return self.session.get(SubstanceRow, substance_id)

    def classifications(self, substance_id: str) -> list[SubstanceClassificationRow]:
        return list(self.session.execute(
            select(SubstanceClassificationRow).where(SubstanceClassificationRow.substance_id == substance_id)
        ).scalars().all())

    def material(self, material_id: str) -> MaterialRow | None:
        return self.session.get(MaterialRow, material_id)

    def compositions(self, material_id: str) -> list[MaterialCompositionRow]:
        return list(self.session.execute(
            select(MaterialCompositionRow).where(MaterialCompositionRow.material_id == material_id)
        ).scalars().all())

    def transport(self, substance_ids: list[str]) -> list[SubstanceTransportRow]:
        return list(self.session.execute(
            select(SubstanceTransportRow).where(SubstanceTransportRow.substance_id.in_(substance_ids))
        ).scalars())

    def component(self, sub: SubstanceRow, name: str, pct: float, via: list[str]) -> Component:
        rows = self.classifications(sub.id)
        assessed = bool(rows) or sub.hazardous_catalog_no is not None or (sub.cas_no in self.assessed_cas)
        return Component(name=name or sub.name_zh or sub.name_en or sub.cas_no or sub.id, pct=pct,
                         cas=sub.cas_no, substance_id=sub.id, hazards=_hazards(list(rows)),
                         assessed=assessed, via=via)


def _range_value(row: MaterialCompositionRow, mode: str) -> float | None:
    if mode == "typical":
        order = (row.pct_typical, row.pct_max, row.pct_min)
    else:
        order = (row.pct_max, row.pct_typical, row.pct_min)
    return next((float(v) for v in order if v is not None), None)


def build_components(session: Session, items: list[dict[str, Any]], rs: RuleSet, *, mode: str = "max",
                     lookup: _Lookup | None = None) -> BuiltInputs:
    """``items``: ``{"name", "pct", "material_id"?, "substance_id"?, "cas"?, "hazards"?, "assessed"?}``."""
    look = lookup or _Lookup(session, rs)
    built = BuiltInputs()
    for index, item in enumerate(items):
        _build_item(look, built, item, mode)
        built.origins.extend([index] * (len(built.components) - len(built.origins)))
    _transport_entries(look, built)
    return built


def _build_item(look: _Lookup, built: BuiltInputs, item: dict[str, Any], mode: str) -> None:
    name = str(item.get("name") or "")
    pct = float(item.get("pct") or 0)
    if pct <= 0:
        return
    if item.get("hazards") is not None:
        built.components.append(Component(
            name=name, pct=pct, cas=cas_mod.normalize(item.get("cas")) or item.get("cas"),
            hazards=[h if isinstance(h, ComponentHazard) else ComponentHazard(**h) for h in item["hazards"]],
            assessed=bool(item.get("assessed", True)), trade_secret=bool(item.get("trade_secret", False)),
        ))
        return
    if item.get("material_id"):
        _explode_material(look, built, item["material_id"], name, pct, mode)
        return
    sub = look.substance(item.get("substance_id"), item.get("cas"))
    if sub is None:
        built.components.append(Component(name=name or str(item.get("cas") or "?"), pct=pct,
                                          cas=item.get("cas"), assessed=False))
        built.gaps.append(DataGap("substance_not_found", None,
                                  f"{name or item.get('cas')}（{pct}%）不在物质库中，按“危害未知”计入。"))
        return
    built.components.append(look.component(sub, name, pct, []))


def _explode_material(look: _Lookup, built: BuiltInputs, material_id: str, name: str, pct: float, mode: str) -> None:
    mat = look.material(material_id)
    if mat is None:
        built.components.append(Component(name=name or material_id, pct=pct, assessed=False))
        built.gaps.append(DataGap("material_not_found", None, f"原料 {name or material_id} 不存在，按“危害未知”计入。"))
        return
    label = name or mat.zh_name or mat.name
    comps = look.compositions(mat.id)
    if comps:
        covered = 0.0
        for row in comps:
            share = _range_value(row, mode)
            sub = look.substance_by_id(row.substance_id)
            if share is None or sub is None:
                continue
            covered += share
            comp = look.component(sub, sub.name_zh or sub.name_en, pct * share / 100.0, [label])
            comp.trade_secret = row.is_trade_secret
            built.components.append(comp)
        # Whatever the supplier SDS does not declare is still in the drum: count it as unknown.
        if covered < 99.5:
            built.components.append(Component(name=f"{label} 未申报组分", pct=pct * (100 - covered) / 100.0,
                                              assessed=False, via=[label]))
            built.gaps.append(DataGap("material_composition_incomplete", None,
                                      f"原料 {label} 的申报组成合计 {covered:.1f}%，其余按“危害未知”计入。"))
        if mat.regulatory_status not in ("reviewed", "locked"):
            built.gaps.append(DataGap("material_not_reviewed", None,
                                      f"原料 {label} 的组成 / 法规数据尚未经法规专员复核。"))
        return
    sub = look.substance(cas=mat.cas_no) if mat.cas_no else None
    if sub is not None:
        built.components.append(look.component(sub, label, pct, [label]))
        return
    built.components.append(Component(name=label, pct=pct, cas=mat.cas_no, assessed=False, via=[label]))
    built.gaps.append(DataGap("material_without_composition", None,
                              f"原料 {label} 没有组成数据（供应商 SDS 第 3 节），按“危害未知”计入。", "blocking"))


def _transport_entries(look: _Lookup, built: BuiltInputs) -> None:
    ids = [c.substance_id for c in built.components if c.substance_id]
    if not ids:
        return
    # A substance can have several GB 12268 rows (one per packing group); the transport
    # engine picks the one matching the packing group it derives.
    for row in sorted(look.transport(ids), key=lambda r: (r.un_number, r.packing_group or "")):
        e = {
            "un_number": row.un_number, "proper_shipping_name_zh": row.proper_shipping_name_zh,
            "proper_shipping_name_en": row.proper_shipping_name_en, "transport_class": row.transport_class,
            "subsidiary_risk": row.subsidiary_risk, "packing_group": row.packing_group,
        }
        built.transport_entries.setdefault(row.substance_id, {**e, "rows": []})["rows"].append(e)
