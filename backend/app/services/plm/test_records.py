"""G6 toxicology / ecotoxicity test records (MSDS ToxRecord / EcoTestRecord /
EcoDataDetailed, stored per substance).

Records are measurements, not calculations: the value stays text (MSDS
allows ">" and ranges). SDS sections 11/12 render these records
additively — the existing ATE calculation and classification text are
never replaced by them, because a component record is not a mixture test.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.models_regulatory import (
    SubstanceEcoProfileRow,
    SubstanceEcoRecordRow,
    SubstanceRow,
    SubstanceToxRecordRow,
)

TOX_ENDPOINTS = ("LD50", "LC50")
TOX_ROUTES = ("oral", "dermal", "inhalation", "other")
ECO_GROUPS = ("fish", "daphnia", "algae")
ECO_ENDPOINTS = ("LC50", "EC50", "ErC50", "NOEC", "LOEC")


def _substance_or_raise(session: Session, substance_id: str) -> SubstanceRow:
    row = session.get(SubstanceRow, substance_id)
    if row is None:
        raise ValueError("物质不存在")
    return row


def _clean(data: dict[str, Any], allowed: dict[str, tuple[str, ...] | None]) -> dict[str, Any]:
    """Validate enum fields and strip strings; unknown keys are ignored."""
    out: dict[str, Any] = {}
    for key, choices in allowed.items():
        value = data.get(key)
        if value is None:
            continue
        value = str(value).strip()
        if choices is not None and value not in choices:
            raise ValueError(f"{key} 非法值：{value!r}（允许：{'、'.join(choices)}）")
        out[key] = value
    return out


def tox_dict(row: SubstanceToxRecordRow) -> dict[str, Any]:
    return {"id": row.id, "substance_id": row.substance_id, "endpoint": row.endpoint, "route": row.route,
            "value": row.value, "unit": row.unit, "species": row.species, "method": row.method,
            "source": row.source, "source_ref": row.source_ref}


def eco_dict(row: SubstanceEcoRecordRow) -> dict[str, Any]:
    return {"id": row.id, "substance_id": row.substance_id, "organism_group": row.organism_group,
            "endpoint": row.endpoint, "value": row.value, "unit": row.unit, "duration": row.duration,
            "species": row.species, "method": row.method, "source": row.source, "source_ref": row.source_ref}


def profile_dict(row: SubstanceEcoProfileRow | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"id": row.id, "substance_id": row.substance_id, "persistence": row.persistence,
            "bioaccum": row.bioaccum, "mobility": row.mobility}


def list_records(session: Session, substance_id: str) -> dict[str, Any]:
    _substance_or_raise(session, substance_id)
    tox = session.execute(select(SubstanceToxRecordRow)
                          .where(SubstanceToxRecordRow.substance_id == substance_id)
                          .order_by(SubstanceToxRecordRow.endpoint, SubstanceToxRecordRow.route)).scalars().all()
    eco = session.execute(select(SubstanceEcoRecordRow)
                          .where(SubstanceEcoRecordRow.substance_id == substance_id)
                          .order_by(SubstanceEcoRecordRow.organism_group, SubstanceEcoRecordRow.endpoint)).scalars().all()
    profile = session.execute(select(SubstanceEcoProfileRow)
                              .where(SubstanceEcoProfileRow.substance_id == substance_id)).scalar_one_or_none()
    return {"tox": [tox_dict(r) for r in tox], "eco": [eco_dict(r) for r in eco],
            "eco_profile": profile_dict(profile)}


def add_tox(session: Session, substance_id: str, data: dict[str, Any]) -> SubstanceToxRecordRow:
    _substance_or_raise(session, substance_id)
    clean = _clean(data, {"endpoint": TOX_ENDPOINTS, "route": TOX_ROUTES, "unit": None,
                          "species": None, "method": None, "source": None, "source_ref": None})
    value = str(data.get("value") or "").strip()
    if not value:
        raise ValueError("毒理记录数值不能为空")
    row = SubstanceToxRecordRow(id=str(uuid.uuid4()), substance_id=substance_id,
                                endpoint=clean.get("endpoint", "LD50"), route=clean.get("route", "oral"),
                                value=value, unit=clean.get("unit", "mg/kg") or "mg/kg",
                                species=clean.get("species", "大鼠") or "大鼠",
                                method=clean.get("method", ""), source=clean.get("source", "manual") or "manual",
                                source_ref=clean.get("source_ref", ""))
    # Pre-check instead of catching IntegrityError: a failed flush leaves
    # the session in pending-rollback state, which would discard earlier
    # work in the same session (e.g. a substance just created). The unique
    # constraint stays as a backstop for concurrent inserts.
    existing = session.execute(
        select(SubstanceToxRecordRow).where(
            SubstanceToxRecordRow.substance_id == substance_id,
            SubstanceToxRecordRow.endpoint == row.endpoint,
            SubstanceToxRecordRow.route == row.route,
            SubstanceToxRecordRow.species == row.species,
            SubstanceToxRecordRow.source == row.source)).scalar_one_or_none()
    if existing is not None:
        raise ValueError("该毒理记录已存在（同物质、同端点、同途径、同物种、同来源）")
    session.add(row)
    session.flush()
    return row


def delete_tox(session: Session, substance_id: str, record_id: str) -> None:
    row = session.get(SubstanceToxRecordRow, record_id)
    if row is None or row.substance_id != substance_id:
        raise ValueError("毒理记录不存在")
    session.delete(row)
    session.flush()


def add_eco(session: Session, substance_id: str, data: dict[str, Any]) -> SubstanceEcoRecordRow:
    _substance_or_raise(session, substance_id)
    clean = _clean(data, {"organism_group": ECO_GROUPS, "endpoint": ECO_ENDPOINTS, "unit": None,
                          "duration": None, "species": None, "method": None, "source": None,
                          "source_ref": None})
    if "organism_group" not in clean:
        raise ValueError("生态记录必须指定 organism_group（fish / daphnia / algae）")
    value = str(data.get("value") or "").strip()
    if not value:
        raise ValueError("生态记录数值不能为空")
    row = SubstanceEcoRecordRow(id=str(uuid.uuid4()), substance_id=substance_id,
                                organism_group=clean["organism_group"],
                                endpoint=clean.get("endpoint", "LC50"), value=value,
                                unit=clean.get("unit", "mg/L") or "mg/L",
                                duration=clean.get("duration", ""), species=clean.get("species", ""),
                                method=clean.get("method", ""), source=clean.get("source", "manual") or "manual",
                                source_ref=clean.get("source_ref", ""))
    existing = session.execute(
        select(SubstanceEcoRecordRow).where(
            SubstanceEcoRecordRow.substance_id == substance_id,
            SubstanceEcoRecordRow.organism_group == row.organism_group,
            SubstanceEcoRecordRow.endpoint == row.endpoint,
            SubstanceEcoRecordRow.species == row.species,
            SubstanceEcoRecordRow.source == row.source)).scalar_one_or_none()
    if existing is not None:
        raise ValueError("该生态记录已存在（同物质、同类群、同端点、同物种、同来源）")
    session.add(row)
    session.flush()
    return row


def delete_eco(session: Session, substance_id: str, record_id: str) -> None:
    row = session.get(SubstanceEcoRecordRow, record_id)
    if row is None or row.substance_id != substance_id:
        raise ValueError("生态记录不存在")
    session.delete(row)
    session.flush()


def upsert_profile(session: Session, substance_id: str, data: dict[str, Any]) -> SubstanceEcoProfileRow:
    _substance_or_raise(session, substance_id)
    clean = _clean(data, {"persistence": None, "bioaccum": None, "mobility": None})
    if not any(clean.get(k) for k in ("persistence", "bioaccum", "mobility")):
        raise ValueError("生态概况至少填写一项（持久性 / 生物累积性 / 迁移性）")
    row = session.execute(select(SubstanceEcoProfileRow)
                          .where(SubstanceEcoProfileRow.substance_id == substance_id)).scalar_one_or_none()
    if row is None:
        row = SubstanceEcoProfileRow(id=str(uuid.uuid4()), substance_id=substance_id)
        session.add(row)
    for key in ("persistence", "bioaccum", "mobility"):
        if key in clean:
            setattr(row, key, clean[key])
    session.flush()
    return row


def records_by_cas(session: Session, cas_numbers: list[str]) -> dict[str, dict[str, Any]]:
    """Test records grouped by CAS, for SDS sections 11/12 (mirrors _oel)."""
    if not cas_numbers:
        return {"tox": {}, "eco": {}, "profiles": {}}
    subs = session.execute(select(SubstanceRow).where(SubstanceRow.cas_no.in_(cas_numbers))).scalars().all()
    by_id = {s.id: s.cas_no for s in subs}
    tox: dict[str, list[dict]] = {}
    for r in session.execute(select(SubstanceToxRecordRow)
                             .where(SubstanceToxRecordRow.substance_id.in_(by_id))).scalars().all():
        tox.setdefault(by_id[r.substance_id], []).append(tox_dict(r))
    eco: dict[str, list[dict]] = {}
    for r in session.execute(select(SubstanceEcoRecordRow)
                             .where(SubstanceEcoRecordRow.substance_id.in_(by_id))).scalars().all():
        eco.setdefault(by_id[r.substance_id], []).append(eco_dict(r))
    profiles: dict[str, dict] = {}
    for r in session.execute(select(SubstanceEcoProfileRow)
                             .where(SubstanceEcoProfileRow.substance_id.in_(by_id))).scalars().all():
        profiles[by_id[r.substance_id]] = profile_dict(r)  # type: ignore[assignment]
    return {"tox": tox, "eco": eco, "profiles": profiles}
