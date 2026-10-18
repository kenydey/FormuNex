"""GET /api/meta/* — static metadata for the frontend (default levers, etc.)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..domain.project_spec import default_levers_for
from ..domain.schemas import LeverSpec, ProductDomain, coerce_substrate, substrate_options

router = APIRouter(prefix="/api/meta", tags=["meta"])


class DefaultLeversResponse(BaseModel):
    levers: list[LeverSpec]


class SubstrateOption(BaseModel):
    value: str
    label_zh: str
    label_en: str


@router.get("/substrates", response_model=list[SubstrateOption])
def list_substrates() -> list[SubstrateOption]:
    """补齐计划 2-11: 基材选项（内置枚举 + FORMUNEX_EXTRA_SUBSTRATES 配置扩展）。

    前端从此处取选项（失败时回退内置硬编码），新增基材无需改前端代码。
    """
    return [SubstrateOption(**o) for o in substrate_options()]


@router.get("/default-levers", response_model=DefaultLeversResponse)
def get_default_levers(
    domain: ProductDomain = Query(..., description="Product domain"),
    # 补齐计划 2-11: str 而非 Substrate 枚举，以便配置基材也能取默认杠杆
    # （未知基材由 coerce_substrate 校验，400 而非静默回退）。
    substrate: str = Query("carbon_steel", description="Substrate"),
    cure_temperature_c: float | None = Query(None, description="Cure temp for process levers"),
) -> DefaultLeversResponse:
    """Return default DOE levers for a domain/substrate (SSOT = project_spec.resolve_levers)."""
    try:
        substrate_value = coerce_substrate(substrate)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    levers = default_levers_for(
        domain,
        substrate_value,
        cure_temperature_c=cure_temperature_c,
    )
    return DefaultLeversResponse(levers=levers)
