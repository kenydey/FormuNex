"""Database-facing entry points of the classification engine (API, release gate, editors)."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ....clock import utcnow
from ....db.models import FormulationVersion
from ....db.models_plm import ClassificationResultRow
from ....db.models_regulatory import GhsStatementRow, RegulatoryDataRelease
from .engine import classify
from .inputs import BuiltInputs, build_components
from .model import ClassificationResult, MixtureProperties
from .ruleset import DEFAULT_RULESET, load_ruleset

_PROPERTY_FIELDS = set(MixtureProperties.__dataclass_fields__)


def properties_from(data: dict[str, Any] | None) -> MixtureProperties:
    data = {k: v for k, v in (data or {}).items() if k in _PROPERTY_FIELDS and v is not None}
    return MixtureProperties(**data)


def classify_items(
    session: Session,
    items: list[dict[str, Any]],
    properties: dict[str, Any] | None = None,
    *,
    ruleset: str = DEFAULT_RULESET,
    mode: str = "max",
    include_transport: bool = True,
) -> ClassificationResult:
    return build_and_classify(session, items, properties, ruleset=ruleset, mode=mode,
                              include_transport=include_transport)[0]


def build_and_classify(
    session: Session,
    items: list[dict[str, Any]],
    properties: dict[str, Any] | None = None,
    *,
    ruleset: str = DEFAULT_RULESET,
    mode: str = "max",
    include_transport: bool = True,
    cached: bool = False,
) -> tuple[ClassificationResult, BuiltInputs]:
    """:func:`classify_items` that also returns the exploded components (red-line checks need both).
    ``cached=True`` serves the substance / material reads from the in-process cache (real-time editor)."""
    rs = load_ruleset(ruleset)
    lookup = None
    if cached:
        from .cache import CachedLookup

        lookup = CachedLookup(session, rs)
    built = build_components(session, items, rs, mode=mode, lookup=lookup)
    exploded = any(item.get("material_id") for item in items)
    result = classify(
        built.components, properties_from(properties), ruleset=rs, include_transport=include_transport,
        substance_transport=built.transport_entries,
        composition_basis="upper_bound" if exploded and mode == "max" else "exact",
    )
    result.data_gaps = built.gaps + result.data_gaps
    return result, built


def formulation_version_items(session: Session, formulation_version_id: str) -> list[dict[str, Any]]:
    fv = session.get(FormulationVersion, formulation_version_id)
    if fv is None:
        raise KeyError(formulation_version_id)
    items = []
    for ing in (fv.snapshot or {}).get("ingredients") or []:
        items.append({
            "name": ing.get("zh_name") or ing.get("name") or "",
            "pct": ing.get("weight_pct") or 0,
            "cas": ing.get("cas_no"),
            "material_id": ing.get("material_id"),
        })
    return items


def statement_texts(session: Session, codes: list[str]) -> dict[str, dict[str, str]]:
    if not codes:
        return {}
    rows = session.execute(select(GhsStatementRow).where(GhsStatementRow.code.in_(codes))).scalars()
    return {r.code: {"zh": r.text_zh, "en": r.text_en} for r in rows}


def latest_data_release(session: Session) -> str | None:
    return session.execute(
        select(RegulatoryDataRelease.id).order_by(RegulatoryDataRelease.created_at.desc()).limit(1)
    ).scalar_one_or_none()


def to_response(session: Session, result: ClassificationResult, *, cached: bool = False) -> dict[str, Any]:
    body = result.to_dict()
    if cached:
        from .cache import statement_texts as cached_texts

        texts = cached_texts(session, result.h_codes + result.p_codes)
    else:
        texts = statement_texts(session, result.h_codes + result.p_codes)
    body["statements"] = {code: texts.get(code, {"zh": "", "en": ""}) for code in result.h_codes + result.p_codes}
    rs = load_ruleset(result.rule_set_version)
    body["rule_set_status"] = rs.status
    return body


def persist(
    session: Session,
    result: ClassificationResult,
    *,
    formulation_version_id: str | None = None,
    revision_id: str | None = None,
) -> ClassificationResultRow:
    row = ClassificationResultRow(
        id=str(uuid.uuid4()), revision_id=revision_id, formulation_version_id=formulation_version_id,
        rule_set_version=result.rule_set_version, data_release_id=latest_data_release(session),
        inputs_hash=result.inputs_hash, result=result.to_dict(), trace=[t for t in result.to_dict()["trace"]],
        computed_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row
