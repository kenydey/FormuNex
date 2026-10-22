"""“设为定型” (方案 §5.4): turn the winning experiment of an optimisation into a product revision candidate.

Creates a new product revision bound to the formulation version and the
winning experiment, carries the product's specification forward (release
specs, acceptance targets, red lines) unless a new one is given, records the
lineage (experiment → revision, formulation version → revision) and moves the
revision to *candidate*, ready for documents and the release gate (W5).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db.models import ExperimentRow, FormulationVersion, MeasurementRow, ProjectRow
from ...db.models_plm import ProductRevisionRow, ProductRow
from ...domain.project_workspace import derive_release_specs
from ..audit import hash_chain
from ..identity.rbac import Principal
from ..provenance import link_many_in_session
from .documents import service as docs
from .release import signoff

CARRIED_SPEC_KEYS = ("release_specs", "acceptance", "red_lines",
                      # Phase 1D (1-14): GHS constraints were dropped on carry-forward —
                      # the gate could never check them on a carried spec.
                      "voc_limit_g_l", "forbidden_pictograms", "forbidden_signal_word")


def select_winning(session: Session, *, product_id: str, formulation_version_id: str, experiment_id: int,
                   user_id: str, principal: Principal, spec_snapshot: dict[str, Any] | None = None,
                   note: str = "") -> tuple[ProductRevisionRow, list[str]]:
    product = session.get(ProductRow, product_id)
    if product is None:
        raise signoff.ReleaseError("产品不存在")
    if not principal.can("formulation.freeze", product.project_id):
        raise signoff.Forbidden("缺少权限 formulation.freeze")
    if session.get(FormulationVersion, formulation_version_id) is None:
        raise signoff.ReleaseError("配方版本不存在")
    exp = session.get(ExperimentRow, experiment_id)
    if exp is None:
        raise signoff.ReleaseError("实验记录不存在")
    warnings: list[str] = []
    if product.project_id and exp.project_id and exp.project_id != product.project_id:
        warnings.append(f"实验属于项目 {exp.project_id}，与产品所属项目 {product.project_id} 不同")
    reviewed = session.execute(select(func.count()).select_from(MeasurementRow).where(
        MeasurementRow.experiment_id == experiment_id, MeasurementRow.review_status == "reviewed")).scalar() or 0
    if not reviewed:
        warnings.append("该实验还没有已复核的测量值：发布门禁的“目标契约”检查只认已复核数据")

    spec = dict(spec_snapshot or {})
    if not spec_snapshot:
        prev = session.execute(select(ProductRevisionRow).where(ProductRevisionRow.product_id == product.id)
                               .order_by(ProductRevisionRow.created_at.desc()).limit(1)).scalar_one_or_none()
        if prev is not None:
            spec = {k: v for k, v in (prev.spec_snapshot or {}).items() if k in CARRIED_SPEC_KEYS}
    # 补齐计划 2-8: 标为出厂指标的目标自动派生 release_specs 进冻结快照
    # （显式 release_specs 按 metric 优先：调用方/沿用 > 契约显式 > 派生）。
    if product.project_id:
        proj = session.get(ProjectRow, product.project_id)
        if proj is not None:
            # P0-3: payload 本身就是平铺的 workspace dict（见 project_store），
            # 旧的嵌套读取恒为空，定型出厂指标派生 COA 一直失效。
            ws = proj.payload or {}
            objectives = ((ws.get("requirement") or {}).get("objectives") or [])
            contract_specs = ((ws.get("spec_contract") or {}).get("release_specs") or [])
            spec["release_specs"] = derive_release_specs(
                objectives, [*(spec.get("release_specs") or []), *contract_specs])
    spec["winning"] = {"experiment_id": experiment_id, "measured": exp.measured or {}, "note": note}

    rev = docs.create_revision(session, product.id, formulation_version_id=formulation_version_id,
                               spec_snapshot=spec, created_by=user_id)
    rev.winning_experiment_id = experiment_id
    # Phase 2D (2-22): chain edges go through the unified provenance service
    # (same session → the edges roll back with the business write).
    link_many_in_session(session, [
        ("experiment", str(experiment_id), "product_revision", rev.id, "selected_as_winning"),
        ("formulation_version", formulation_version_id, "product_revision", rev.id, "frozen_as"),
    ])
    session.flush()
    hash_chain.append(session, actor=user_id, action="revision.winning", entity_type="product_revision",
                      entity_id=rev.id, detail={"experiment_id": experiment_id,
                                                "formulation_version_id": formulation_version_id, "note": note})
    signoff.mark_candidate(session, rev.id, user_id=user_id, principal=principal)
    return rev, warnings
