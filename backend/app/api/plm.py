"""PLM & Release Center API — GB 30000 classification and the substance database (Workspace 5)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/plm", tags=["plm"])


class HazardIn(BaseModel):
    hazard_class: str
    category: str
    h_code: str | None = None
    scl: list[dict] | None = None
    m_factor_acute: int | None = None
    m_factor_chronic: int | None = None
    ate_value: float | None = None
    ate_unit: str | None = None
    non_additive: bool = False


class ComponentIn(BaseModel):
    name: str = ""
    pct: float = Field(ge=0, le=100)
    cas: str | None = None
    substance_id: str | None = None
    material_id: str | None = None
    # Given → used as-is; omitted → looked up in the substance database.
    hazards: list[HazardIn] | None = None
    assessed: bool = True
    trade_secret: bool = False


class PropertiesIn(BaseModel):
    phys_state: Literal["liquid", "solid", "gas", "aerosol", "paste"] = "liquid"
    flash_point_c: float | None = None
    initial_boiling_point_c: float | None = None
    ph: float | None = Field(default=None, ge=0, le=14)
    kinematic_viscosity_40c: float | None = Field(default=None, ge=0)
    metal_corrosion_rate_mm_per_year: float | None = Field(default=None, ge=0)
    sustained_combustion: bool | None = None
    inhalation_form: Literal["gas", "vapour", "dust_mist"] | None = None
    product_category: str | None = None
    sources: dict[str, str] = Field(default_factory=dict)


class ClassifyRequest(BaseModel):
    components: list[ComponentIn] = Field(min_length=1, max_length=500)
    properties: PropertiesIn = Field(default_factory=PropertiesIn)
    mode: Literal["max", "typical"] = "max"
    include_transport: bool = True
    formulation_version_id: str | None = None  # persist the result against this version


class ClassifyVersionRequest(BaseModel):
    properties: PropertiesIn = Field(default_factory=PropertiesIn)
    mode: Literal["max", "typical"] = "max"
    include_transport: bool = True
    persist: bool = True


def _session():
    from ..db.database import default_session_factory

    return default_session_factory()()


@router.get("/ghs/ruleset")
def get_ruleset() -> dict[str, Any]:
    from ..services.plm.ghs.ruleset import available_rulesets, load_ruleset

    rs = load_ruleset()
    return {"version": rs.version, "status": rs.status, "available": available_rulesets(), "parameters": rs.as_dict()}


@router.post("/ghs/classify")
def classify_composition(body: ClassifyRequest) -> dict[str, Any]:
    """Classify a composition (real-time: called by the formulation editor's compliance panel)."""
    from ..services.plm.ghs import service

    items = [c.model_dump(exclude_none=True) for c in body.components]
    with _session() as session:
        result = service.classify_items(session, items, body.properties.model_dump(), mode=body.mode,
                                        include_transport=body.include_transport)
        response = service.to_response(session, result)
        if body.formulation_version_id:
            row = service.persist(session, result, formulation_version_id=body.formulation_version_id)
            session.commit()
            response["classification_id"] = row.id
    return response


@router.post("/ghs/classify/formulation-versions/{version_id}")
def classify_formulation_version(version_id: str, body: ClassifyVersionRequest) -> dict[str, Any]:
    from ..services.plm.ghs import service

    with _session() as session:
        try:
            items = service.formulation_version_items(session, version_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="配方版本不存在") from None
        if not items:
            raise HTTPException(status_code=422, detail="该配方版本没有组分")
        result = service.classify_items(session, items, body.properties.model_dump(), mode=body.mode,
                                        include_transport=body.include_transport)
        response = service.to_response(session, result)
        if body.persist:
            row = service.persist(session, result, formulation_version_id=version_id)
            session.commit()
            response["classification_id"] = row.id
    return response


@router.get("/substances")
def search_substances(q: str = Query(..., min_length=1, max_length=200), limit: int = Query(20, ge=1, le=100)
                      ) -> list[dict[str, Any]]:
    """Look up substances by CAS (exact) or Chinese / English name (substring)."""
    from sqlalchemy import or_, select

    from ..db.models_regulatory import SubstanceClassificationRow, SubstanceRow
    from ..services.plm.regulatory import cas as cas_mod
    from ..services.plm.regulatory.source_priority import effective

    with _session() as session:
        norm = cas_mod.normalize(q)
        if norm:
            stmt = select(SubstanceRow).where(SubstanceRow.cas_no == norm)
        else:
            like = f"%{q.strip()}%"
            stmt = (select(SubstanceRow)
                    .where(or_(SubstanceRow.name_zh.like(like), SubstanceRow.name_en.ilike(like)))
                    .order_by(SubstanceRow.hazardous_catalog_no.is_(None), SubstanceRow.name_zh)
                    .limit(limit))
        subs = session.execute(stmt).scalars().all()
        ids = [s.id for s in subs]
        cls_rows = session.execute(
            select(SubstanceClassificationRow).where(SubstanceClassificationRow.substance_id.in_(ids))
        ).scalars().all() if ids else []
        by_sub: dict[str, list] = {}
        for r in cls_rows:
            by_sub.setdefault(r.substance_id, []).append(r)
        return [{
            "id": s.id, "cas_no": s.cas_no, "name_zh": s.name_zh, "name_en": s.name_en,
            "hazardous_catalog_no": s.hazardous_catalog_no, "highly_toxic": s.highly_toxic,
            "precursor_control": s.precursor_control, "explosive_precursor": s.explosive_precursor,
            "classifications": [{"hazard_class": r.hazard_class, "category": r.category, "h_code": r.h_code,
                                 "source": r.source, "is_minimum": r.is_minimum, "locked": r.locked_at is not None}
                                for r in effective(by_sub.get(s.id, []))],
        } for s in subs]
