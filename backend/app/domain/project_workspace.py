"""Project workspace schema — full NotebookLM-style project state persisted in SQLite."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .schemas import (
    AdaptiveDOEMetadata,
    ComprehensiveReport,
    DOEPlan,
    Evidence,
    Formulation,
    LoopReport,
    ModelInfo,
    ObjectiveSpec,
    ProductDomain,
    Requirement,
    ResearchResult,
)


def derive_release_specs(
    objectives: list[ObjectiveSpec | dict[str, Any]] | None,
    explicit: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """补齐计划 2-8: 从标为出厂指标的目标派生 release_specs（COA 出厂检验项）。

    派生规则：目标的 ref_min/ref_max/target_value → spec 的 min/max/target；
    display_name（无则 metric）→ label；unit 照搬；method 留空（出厂页可填标准号）。
    显式 release_specs 按 metric 优先（同 metric 不重复派生）；显式条目原样保留在前。
    目标为 dict 或 ObjectiveSpec 均可（调用方可能传 workspace 原始 JSON）。
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in explicit or []:
        item = dict(s)
        metric = str(item.get("metric") or "")
        if metric and metric in seen:
            continue  # 同 metric 显式重复：先到的优先（调用方/沿用 > 契约）
        if metric:
            seen.add(metric)
        out.append(item)
    for obj in objectives or []:
        data = obj.model_dump() if isinstance(obj, ObjectiveSpec) else (obj if isinstance(obj, dict) else {})
        if not data.get("is_release_indicator") or not data.get("metric"):
            continue
        metric = str(data["metric"])
        if metric in seen:
            continue
        seen.add(metric)
        out.append({
            "metric": metric,
            "label": str(data.get("display_name") or metric),
            "unit": str(data.get("unit") or ""),
            "min": data.get("ref_min"),
            "max": data.get("ref_max"),
            "target": data.get("target_value"),
            "method": "",
        })
    return out


def diff_spec_contract(
    old: dict[str, Any] | None,
    new: dict[str, Any] | None,
) -> list[str]:
    """补齐计划 2-9: 对比新旧 spec_contract，返回变化的顶层键。

    red_lines / release_specs / acceptance 等深层结构按值比较（!=），浅层键
    （voc_limit_g_l / forbidden_signal_word / process_window）同样按值比较。
    两边都缺失 / 均为空 → 无变化。
    """
    old = old or {}
    new = new or {}
    return sorted({k for k in set(old) | set(new) if old.get(k) != new.get(k)})


def record_contract_change(
    workspace: dict[str, Any],
    changed_keys: list[str],
) -> dict[str, Any] | None:
    """补齐计划 2-9: 向 workspace.contract_change_notices 追加一条未确认通知。

    通知目标为 DOE 与门禁（targets: ["doe", "gate"]），由下游确认后 acknowledged=true。
    changed_keys 为空 → 不记通知，返回 None。返回追加的通知条目。
    """
    if not changed_keys:
        return None
    from ..clock import utcnow

    notice = {
        "at": utcnow().isoformat(),
        "changed_keys": sorted(changed_keys),
        "targets": ["doe", "gate"],
        "acknowledged": False,
    }
    notices = workspace.setdefault("contract_change_notices", [])
    if isinstance(notices, list):
        notices.append(notice)
    else:
        workspace["contract_change_notices"] = [notice]
    return notice


class ProjectWorkspace(BaseModel):
    """Complete UI workspace for one research project."""

    search_query: str = ""
    source_types: list[str] = Field(default_factory=lambda: ["patents", "literature", "internet"])
    notebooklm_notebook_id: str | None = None
    sources: list[Evidence] = Field(default_factory=list)
    selected_sources: list[str] = Field(default_factory=list)
    chat_history: list[dict[str, Any]] = Field(default_factory=list)
    deep_report: ComprehensiveReport | None = None
    requirement: Requirement | None = None
    active_constraints: list[str] = Field(default_factory=list)
    research: ResearchResult | None = None
    leaderboard: list[Formulation] = Field(default_factory=list)
    doe_plan: DOEPlan | None = None
    adaptive_doe: AdaptiveDOEMetadata | None = None
    measured: dict[str, float] = Field(default_factory=dict)
    models: list[ModelInfo] = Field(default_factory=list)
    model_history: list[list[ModelInfo]] = Field(default_factory=list)
    train_message: str = ""
    campaign_state: str | None = None
    workbench_campaign_id: int | None = None
    workbench_adopted_plan_id: str | None = None
    workbench_objectives_snapshot: list[dict] = Field(default_factory=list)
    optimization_history: list[float] = Field(default_factory=list)
    loop_report: LoopReport | None = None
    rmse_history: list[dict[str, float]] = Field(default_factory=list)
    doe_engine: str = "auto"
    al_engine: str = "auto"
    optimize_engine: str = "auto"
    loop_doe_engine: str = "auto"
    recommend_source_types: list[str] = Field(default_factory=lambda: ["patents", "literature", "internet"])
    last_al_engine: str | None = None
    auto_loop_on_sync: bool = False
    # The UI's "run at most N rounds" cap and its round counter. Both used to be sent by the
    # client and silently dropped here (an unknown key never survives model_validate), so a reload
    # forgot the cap the user set and restarted the counter that bounds unattended auto-iterations.
    auto_loop_max_rounds: int = 5
    auto_loop_round: int = 0
    auto_adopt_next_doe_on_loop: bool = False
    # Top-5‴ #4: project-level dossier auto patch (global default still off).
    wiki_dossier_auto_patch: bool = False
    # Post-A′ #4: project-level prediction soft-correct (global default still off).
    prediction_bias_soft_correct: bool = False
    # W1-3 (P0-5): 项目级 agent 上下文 —— 存于 SQLite JSON payload, 无需迁移;
    #   为空则所有 prompt 注入逻辑跳过(向后兼容旧数据)。
    agent_context: str = ""
    # FormuNex W2 (方案 §5.2): the project's specification contract beyond the optimisation objectives —
    # process window, compliance red lines (substances, VOC limit, forbidden pictograms / signal word),
    # release specs (COA) and acceptance targets (release gate). Copied into a product revision's
    # ``spec_snapshot`` when a formulation is frozen.
    spec_contract: dict[str, Any] = Field(default_factory=dict)

    # 补齐计划 2-9: 契约变更通知（下游 DOE/门禁重新评估）。由 update_project 在
    # spec_contract 变化时追加；下游确认后 acknowledged=true。存 workspace JSON，无需迁移。
    contract_change_notices: list[dict[str, Any]] = Field(default_factory=list)


class ProjectSummary(BaseModel):
    id: str
    title: str
    headline: str
    domain: str
    created_at: datetime
    updated_at: datetime
    source_count: int = 0
    chat_count: int = 0
    leaderboard_count: int = 0
    has_doe: bool = False
    has_optimize: bool = False
    has_loop: bool = False



class ProjectDetail(BaseModel):
    id: str
    title: str
    headline: str
    domain: str
    created_at: datetime
    updated_at: datetime
    workspace: ProjectWorkspace


class ProjectCreateRequest(BaseModel):
    title: str = ""
    requirement: Requirement | None = None


class ProjectUpdateRequest(BaseModel):
    """Partial/presence-based workspace update (2026-09-05).

    ``workspace`` 以 dict 传入并在 store 做**字段级合并**: 请求中出现的键才写入,
    未出现字段保持 DB 现值 —— 杜绝「空 state 覆盖」事故。两个保护例外:
    ``chat_history`` 由后端从 chat_messages 表重建镜像(权威在表);
    ``sources`` 非空→空的覆盖会被拒绝(记 warning)。
    """

    workspace: dict[str, Any]
    title: str | None = None
    # Explicit clear: allow ``workspace.sources == []`` to replace a non-empty list (the user deleted the
    # last item). Without it the guard above keeps the stored sources.
    clear_sources: bool = False


class LegacySnapshotPayload(BaseModel):
    """Import from browser localStorage SessionSnapshot."""

    id: str
    timestamp: str
    domain: str
    headline: str
    requirement: dict[str, Any]
    leaderboard: list[dict[str, Any]] = Field(default_factory=list)
    models: list[dict[str, Any]] = Field(default_factory=list)
    optimization_history: list[float] = Field(default_factory=list)


class MigrateLocalRequest(BaseModel):
    snapshots: list[LegacySnapshotPayload] = Field(default_factory=list)


def default_requirement() -> Requirement:
    from ..pipeline.workflow import default_objectives

    return Requirement(
        project_id="",  # 创建时由 store 回写 UUID(2026-09-05);空=未归属
        product_type="防腐蚀环氧底漆",
        application="carbon_steel",
        domain=ProductDomain.anticorrosion_coating,
        salt_spray_hours=500,
        film_weight_gsm=70,
        cure_temperature_c=80,
        cleaning_efficiency=90,
        voc_limit_gpl=420,
        objectives=default_objectives(ProductDomain.anticorrosion_coating),
    )


def workspace_from_legacy(snap: LegacySnapshotPayload) -> ProjectWorkspace:
    req = Requirement(**snap.requirement)
    return ProjectWorkspace(
        requirement=req,
        leaderboard=[Formulation(**f) for f in snap.leaderboard],
        models=[ModelInfo(**m) for m in snap.models],
        optimization_history=snap.optimization_history,
    )


def derive_title(workspace: ProjectWorkspace, fallback: str = "未命名项目") -> str:
    if workspace.search_query.strip():
        return workspace.search_query.strip()[:120]
    if workspace.requirement and workspace.requirement.product_type:
        return workspace.requirement.product_type[:120]
    return fallback


def derive_headline(workspace: ProjectWorkspace) -> str:
    if workspace.requirement:
        return workspace.requirement.headline()
    return "FormuNex project"


def summary_stats(workspace: ProjectWorkspace) -> dict[str, Any]:
    return {
        "source_count": len(workspace.sources),
        "chat_count": len(workspace.chat_history),
        "leaderboard_count": len(workspace.leaderboard),
        "has_doe": workspace.doe_plan is not None,
        "has_optimize": len(workspace.optimization_history) > 0,
        "has_loop": workspace.loop_report is not None,
    }
