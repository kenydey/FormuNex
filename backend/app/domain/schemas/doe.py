"""Formulation / DOE / lab DTOs: specs, formulations, plans, runs, measurements."""
from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel
from pydantic import Field
from pydantic import computed_field
from pydantic import field_validator
from pydantic import model_validator

from .chat import EvidenceRef


class Supplier(BaseModel):
    """Structured supplier record harvested from PubChem ``Chemical Vendors``.

    Carries what the source actually exposes: vendor name, homepage URL and
    product page. Canonical persistence is ``suppliers`` + ``material_suppliers``
    (alembic 0028); ``materials.suppliers_json`` remains a transitional
    projection for older readers and is dual-written on upsert.

    Price / stock / delivery are intentionally NOT modelled — PubChem's vendor
    category provides no commercial terms (verified: it returns only
    ``SourceName`` / ``SourceURL`` / ``SourceRecordURL`` / ``RegistryID``), so
    such fields could never be filled and would render as empty placeholders.
    """
    name: str = Field(..., max_length=120, description="供应商名称")
    url: str | None = Field(default=None, description="供应商主页 URL")
    product_url: str | None = Field(default=None, description="产品页 URL")

class ProductDomain(str, Enum):
    """The metal surface treatment product families FormuNex targets."""

    anticorrosion_coating = "anticorrosion_coating"  # 防腐蚀涂料
    degreaser = "degreaser"  # 脱脂剂
    surface_treatment = "surface_treatment"  # 表面处理剂
    autodeposition_coating = "autodeposition_coating"

class Substrate(str, Enum):
    carbon_steel = "carbon_steel"
    galvanized_steel = "galvanized_steel"
    aluminum = "aluminum"
    stainless_steel = "stainless_steel"
    magnesium_alloy = "magnesium_alloy"
    # 补齐计划 2-11: 铸造/压铸铝合金与冷轧钢（汽车/家电常见基材）。
    a356 = "a356"  # A356 铸造铝合金
    adc12 = "adc12"  # ADC12 压铸铝合金
    cold_rolled_steel = "cold_rolled_steel"

EXTRA_SUBSTRATES_ENV = "FORMUNEX_EXTRA_SUBSTRATES"

_BUILTIN_SUBSTRATE_LABELS: dict[str, tuple[str, str]] = {
    "carbon_steel": ("碳钢", "Carbon steel"),
    "galvanized_steel": ("镀锌钢", "Galvanized steel"),
    "aluminum": ("铝合金", "Aluminium"),
    "stainless_steel": ("不锈钢", "Stainless steel"),
    "magnesium_alloy": ("镁合金", "Magnesium alloy"),
    "a356": ("A356 铸造铝合金", "A356 cast aluminium alloy"),
    "adc12": ("ADC12 压铸铝合金", "ADC12 die-cast aluminium alloy"),
    "cold_rolled_steel": ("冷轧钢", "Cold-rolled steel"),
}

def extra_substrates() -> list[dict[str, str]]:
    """Parse configured extra substrates from the environment (see above)."""
    import json
    import os

    raw = os.environ.get(EXTRA_SUBSTRATES_ENV, "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    out: list[dict[str, str]] = []
    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "").strip()
            if not value or value in _BUILTIN_SUBSTRATE_LABELS:
                continue
            out.append({
                "value": value,
                "label_zh": str(item.get("label_zh") or value),
                "label_en": str(item.get("label_en") or value),
            })
    return out

def substrate_options() -> list[dict[str, str]]:
    """All substrate options: builtins + configured extras (meta endpoint / UI)."""
    opts = [
        {"value": v, "label_zh": zh, "label_en": en}
        for v, (zh, en) in _BUILTIN_SUBSTRATE_LABELS.items()
    ]
    return opts + extra_substrates()

def coerce_substrate(value: Any) -> Substrate | str:
    """Validate a substrate value: builtin → Substrate member, configured extra → raw str.

    Unknown values raise ValueError (fail-closed: a typo'd substrate must not
    silently become a different material's process route).
    """
    if isinstance(value, Substrate):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError("基材不能为空")
    try:
        return Substrate(text)
    except ValueError:
        pass
    if any(e["value"] == text for e in extra_substrates()):
        return text
    allowed = [s.value for s in Substrate] + [e["value"] for e in extra_substrates()]
    raise ValueError(f"未知基材 {text!r}，可选：{', '.join(allowed)}")

def substrate_value(substrate: Any) -> str:
    """Plain-string value of a substrate (enum member or configured-extra str)."""
    if isinstance(substrate, Substrate):
        return substrate.value
    return str(substrate or "")

class ObjectiveDirection(str, Enum):
    maximize = "maximize"
    minimize = "minimize"
    match_target = "match_target"

class ObjectiveSpec(BaseModel):
    """One term of a multi-objective optimization goal (schema-driven contract)."""

    id: str = ""
    metric: str  # machine key, e.g. salt_spray_hours
    display_name: str = ""
    # 补齐计划 2-8: 出厂指标标志 —— 勾选的目标在定型/冻结时自动派生为 release_specs
    # （COA 出厂检验项），不再重复录入；显式 release_specs 按 metric 优先。
    is_release_indicator: bool = False
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    direction: str = Field(default="maximize")
    ref_min: float | None = None
    ref_max: float | None = None
    target_value: float | None = None
    unit: str = ""
    value_type: Literal["number", "rating"] = "number"

    @model_validator(mode="after")
    def _fill_defaults(self) -> ObjectiveSpec:
        if not self.id:
            object.__setattr__(self, "id", self.metric or uuid4().hex[:8])
        if self.direction not in {d.value for d in ObjectiveDirection}:
            # v29 M-6 意图：非法 direction 不静默转 maximize，抛错让 pydantic 转 422。
            raise ValueError(
                f"invalid direction {self.direction!r}: "
                f"expected one of {sorted(d.value for d in ObjectiveDirection)}"
            )
        return self

class LeverSpec(BaseModel):
    """A tunable DOE/optimization factor (ingredient wt% or process parameter)."""

    # P2-1: name 加长度限制（纵深防御，防 CSV 注入/超长截断）
    name: str = Field(min_length=1, max_length=100)
    low: float
    high: float
    unit: str = "wt%"
    # Up-4A: discrete factor support (C-4a machinery now has a production entry).
    kind: str = "continuous"
    levels: list[float | str] | None = None
    # U-5: 离散材料替换语义 —— 字符串水平 → 目标成分名的映射
    # (如 {"树脂B": "聚氨酯树脂X"})。None 表示无替换语义（保持 B-DOE-3 跳过）。
    material_map: dict[str, str] | None = None

    @model_validator(mode="after")
    def _validate_kind_levels(self) -> "LeverSpec":
        _validate_factor_kind_levels(self.kind, self.levels, self.name)
        # P1-2: 倒置边界在 API 层直接 422，不等到深层转换才报错
        if self.low > self.high:
            raise ValueError(f"因子 {self.name}: low ({self.low}) 不能大于 high ({self.high})")
        return self

class MaterialSpec(BaseModel):
    """Project-level raw material entry (extends global RAW_MATERIALS)."""

    name: str
    role: str = "additive"
    weight_pct: float = Field(default=0.0, ge=0, le=100)
    smiles: str | None = None
    formula: str | None = None
    cas_no: str | None = None
    zh_name: str | None = None
    price_cny_per_kg: float | None = None
    voc_contrib: float | None = None
    supplier: str | None = None

    suppliers: list[Supplier] = Field(default_factory=list)

class MetricPriorSpec(BaseModel):
    """Declarative prior for a custom metric (YAML or builtin alias)."""

    metric: str
    prior_yaml: str | None = None
    builtin_alias: str | None = None
    confidence: str = "cold-start"

class Requirement(BaseModel):
    """User-supplied R&D requirement captured from the left input panel."""

    project_id: str = ""
    product_type: str = ""
    application: str = ""
    domain: ProductDomain
    substrate: Substrate | str = Substrate.carbon_steel
    # Anti-corrosion targets
    salt_spray_hours: float = Field(0, ge=0, description="Target neutral salt spray resistance (h)")
    film_weight_gsm: float = Field(0, ge=0, description="Target dry film weight / coating weight (g/m^2)")
    cure_temperature_c: float | None = Field(80, ge=0, le=400, description="Max acceptable cure temperature (C)")
    # Degreaser targets
    cleaning_efficiency: float = Field(0, ge=0, le=100, description="Target soil removal (%)")
    # Common constraints
    voc_limit_gpl: float | None = Field(420, ge=0, description="Max VOC (g/L); None disables VOC limit checks")
    # 补齐计划 2-10: 规格契约工艺窗口（cure_temp_c / cure_time_min / film_thickness_um /
    # application_method / notes）。derive_process_levers / check_process_window 读取；
    # 前端 useRequirement 从 spec_contract.process_window 合并进来。
    process_window: dict[str, Any] | None = Field(default=None, description="工艺窗口（规格契约）")
    # 项目自定义工艺窗口（与 process_window 平行，不动既有 4 键）：数值型
    # {key, label, unit, low, high} 或离散型 {key, label, values[]}；前端
    # useRequirement 从 spec_contract.process_window_custom 合并进来，后端
    # derive_process_levers / check_process_window 以通用分支读取。
    process_window_custom: list[dict[str, Any]] | None = Field(default=None, description="自定义工艺窗口参数")
    ph_target: float | None = Field(None, ge=0, le=14)
    notes: str = ""
    objectives: list[ObjectiveSpec] = Field(default_factory=list)
    levers: list[LeverSpec] = Field(default_factory=list)
    materials: list[MaterialSpec] = Field(default_factory=list)
    metric_priors: list[MetricPriorSpec] = Field(default_factory=list)
    constraint_values: dict[str, float] = Field(
        default_factory=dict,
        description="工艺约束 SSOT {名称: 值}，含 catalog 标签与自定义项",
    )
    # 自定义工艺约束结构化行（与 constraint_values 单值镜像并存）：{name, unit,
    # kind: upper|lower|range|target, low?, high?, value?}；区间行不写单值镜像。
    # 旧单值自定义条目前端加载时原地转行（kind=target），后端不做强制迁移。
    custom_constraints: list[dict[str, Any]] | None = Field(default=None, description="自定义工艺约束结构化行")
    active_formulation: Formulation | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_constraints(cls, data: Any) -> Any:
        """Merge deprecated ``constraints`` dict into ``constraint_values`` on load."""
        if not isinstance(data, dict):
            return data
        legacy = data.pop("constraints", None)
        if not legacy:
            return data
        cv = dict(data.get("constraint_values") or {})
        for key, raw in legacy.items():
            if raw is not None:
                cv.setdefault(key, float(raw))
        data["constraint_values"] = cv
        return data

    @field_validator("substrate", mode="before")
    @classmethod
    def _coerce_substrate_field(cls, v: Any) -> Substrate | str:
        # 补齐计划 2-11: 内置基材 → 枚举；配置基材 → 原字符串；未知 → 报错。
        if v is None or (isinstance(v, str) and not v.strip()):
            return Substrate.carbon_steel
        return coerce_substrate(v)

    def headline(self) -> str:
        label = self.product_type or self.domain.value
        app = self.application or substrate_value(self.substrate)
        bits = [label, f"on {app}"]
        if self.salt_spray_hours:
            bits.append(f"{self.salt_spray_hours:.0f}h salt spray")
        if self.film_weight_gsm:
            bits.append(f"{self.film_weight_gsm:.0f} g/m^2 film")
        if self.cleaning_efficiency:
            bits.append(f"{self.cleaning_efficiency:.0f}% cleaning")
        return ", ".join(bits)

class Ingredient(BaseModel):
    name: str
    role: str  # e.g. resin, hardener, inhibitor, surfactant, solvent, pigment
    smiles: str | None = None
    formula: str | None = None
    mf_structure: str | None = None
    cas_no: str | None = None
    zh_name: str | None = None
    molar_mass: float | None = None
    weight_pct: float = Field(ge=0, le=100)
    # Lab-table fields (LLM recommend / UI)
    component_type: str = ""
    equivalents: float | None = None
    mmol: float | None = None
    amount_display: str = ""
    notes: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    grounding_confidence: Literal["high", "medium", "low"] = "high"

    @field_validator("molar_mass", "equivalents", "mmol", mode="before")
    @classmethod
    def _coerce_numeric(cls, v: Any) -> Any:
        """Normalize numeric fields that may arrive as strings.

        LLM/Datalab responses sometimes carry numbers as strings (e.g.
        molar_mass="35.046" or "381.9 g/mol"). Bare ``float(...)`` drops
        unit-bearing values to None; extract the leading number instead so
        gate/chemistry parsers see a real float.
        """
        if v is None or isinstance(v, (int, float)):
            return v
        s = str(v).strip().replace(",", "").replace("约", "").replace("~", "")
        if not s:
            return None
        try:
            return float(s)
        except (TypeError, ValueError):
            m = re.search(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", s)
            return float(m.group()) if m else None

    @model_validator(mode="after")
    def _sync_formula_fields(self) -> Ingredient:
        if self.mf_structure and not self.formula:
            object.__setattr__(self, "formula", self.mf_structure)
        elif self.formula and not self.mf_structure:
            object.__setattr__(self, "mf_structure", self.formula)
        if not self.component_type and self.role:
            object.__setattr__(self, "component_type", self.role)
        return self

class FormulationExplain(BaseModel):
    """Batch C: structured 「为何推荐」 display contract (no ranking effect)."""

    objectives_hit: list[str] = Field(default_factory=list)
    constraints_miss: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, str]] = Field(default_factory=list)
    kg_signals: dict[str, Any] = Field(default_factory=dict)
    supply_flags: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    bias_corrected: bool = False
    bias_corrected_metrics: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # Post-A′ #2: Requirement field → recommend/DOE wiring audit (display).
    effect_trace: list[dict[str, Any]] = Field(default_factory=list)

class Formulation(BaseModel):
    name: str
    domain: ProductDomain
    ingredients: list[Ingredient]
    rationale: str = ""
    source: str = ""  # e.g. "manual", "ai_modify", "recommend"
    # v20-3: predicted 是原始目标值（如 {"salt_spray_h": 720.0}），
    # score 是 0-1 归一化加权分（用于排序）。前端应双字段展示。
    predicted: dict[str, float] = Field(default_factory=dict)
    predicted_std: dict[str, float] = Field(default_factory=dict)
    prediction_tiers: dict[str, str] = Field(default_factory=dict)
    score: float | None = None
    warnings: list[str] = Field(default_factory=list)
    # KG compatibility adjustment detail (second priority). Populated by
    # kg_recommend_score when KG is enabled; None otherwise.
    kg_compat: dict | None = None
    # Top-5‴ #2: metrics whose predicted values were soft-corrected by
    # prediction_bias.mean_error (flag-gated; empty when off).
    bias_corrected_metrics: list[str] = Field(default_factory=list)
    # Batch C: structured explain for FormulaLeaderboard (optional on older rows).
    explain: FormulationExplain | None = None
    # Stable client-side identity of a card (the UI stamps it when a card becomes the DOE baseline, so the
    # "baseline" badge matches by identity rather than by name). Opaque to the backend — but it has to
    # survive a save/reload, or the badge disappears while the baseline itself (requirement.active_formulation)
    # is still in force.
    client_uid: str | None = None

    def total_pct(self) -> float:
        return round(sum(i.weight_pct for i in self.ingredients), 4)

class RecommendedFormulaComponent(BaseModel):
    """One row in the structured lab recipe table (LLM output + UI)."""

    component_type: str = ""
    name: str
    zh_name: str = ""
    cas_no: str = ""
    mf: str = ""
    smiles: str | None = None
    molar_mass: float | None = None
    equivalents: float | None = None
    mmol: float | None = None
    amount_display: str = ""
    weight_pct: float | None = Field(default=None, ge=0, le=100)
    notes: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    # v20-1: 引用可解释性明细（与 evidence_refs 一一对应，向后兼容）。
    evidence_details: list[EvidenceRef] = Field(default_factory=list)
    grounding_confidence: Literal["high", "medium", "low"] = "high"
    # N-6: 推荐解释 —— 为什么选这个成分（基于接地可信度自动生成）
    selection_reason: str = ""

class RecommendedFormula(BaseModel):
    """Structured formulation recommendation from the LLM recommend engine."""

    name: str
    domain: ProductDomain
    rationale: str = ""
    objectives_summary: str = ""
    components: list[RecommendedFormulaComponent] = Field(default_factory=list)
    predicted: dict[str, float] = Field(default_factory=dict)
    score: float | None = None
    warnings: list[str] = Field(default_factory=list)
    engine: Literal["llm", "offline"] = "llm"

class RecommendedFormulaListResponse(BaseModel):
    """Wrapper for LLM structured output and API responses."""

    formulas: list[RecommendedFormula] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    engine: Literal["llm", "offline"] = "offline"

class InferredSystem(BaseModel):
    """LLM-inferred formulation-system constraints (P2 persistence carrier).

    Schema mirrors ``FormulationSystem`` so an inferred entry can be promoted
    into the static knowledge base with no translation.
    """

    system_name: str = ""
    must_include_roles: list[str] = Field(default_factory=list)
    must_exclude: str = ""
    constraints: list[str] = Field(default_factory=list)
    metric_ranges: dict[str, tuple[float, float]] = Field(default_factory=dict)
    confidence: Literal["high", "medium", "low"] = "medium"

class FactorCandidate(BaseModel):
    """Suggested DOE factor with literature/KB rationale."""

    name: str
    low: float
    high: float
    unit: str = "wt%"
    # C-4a: factor kind — "continuous" (range [low, high]) or "discrete"
    # (fixed level set). Discrete levels are consumed per-engine:
    # BayBE maps to NumericalDiscreteParameter/CategoricalParameter,
    # native decode() maps coded [-1, 1] onto the level index, pydoe
    # fails closed (LHS cannot sample a discrete set).
    kind: str = "continuous"
    levels: list[float | str] | None = None
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    source: str = "kb+levers"

    @model_validator(mode="after")
    def _validate_kind_levels(self) -> "FactorCandidate":
        _validate_factor_kind_levels(self.kind, self.levels, self.name)
        return self

class DOEFactor(BaseModel):
    name: str
    low: float
    high: float
    unit: str = ""
    kind: str = "continuous"
    levels: list[float | str] | None = None
    # v7: material_map 已删除 —— 该字段写而不读（reconstruct 实际从 LeverSpec 取），
    # 留着会误导。LeverSpec.material_map 是唯一 SSOT。

    @model_validator(mode="after")
    def _validate_kind_levels(self) -> "DOEFactor":
        _validate_factor_kind_levels(self.kind, self.levels, self.name)
        if self.kind == "continuous" and self.low > self.high:
            raise ValueError(
                f"Factor {self.name!r}: low ({self.low}) must not exceed high ({self.high})"
            )
        return self

def _validate_factor_kind_levels(kind: str, levels: list[float | str] | None, name: str) -> None:
    """Shared kind/levels invariant for DOEFactor and FactorCandidate."""
    if kind not in ("continuous", "discrete"):
        raise ValueError(f"Factor {name!r}: kind must be 'continuous' or 'discrete', got {kind!r}")
    if kind == "discrete":
        if not levels or len(levels) < 2:
            raise ValueError(
                f"Factor {name!r}: discrete kind requires at least 2 levels, got {levels!r}"
            )

class DOERun(BaseModel):
    run_id: int
    coded: dict[str, float]
    # C-4a: discrete factors decode to a level value, which may be a string
    # (e.g. solvent names) rather than a number.
    natural: dict[str, float | str]
    ai_suggested: bool = False
    # Closed-loop chemical feasibility flag (KG material-compatibility gate).
    # Set when the candidate's material skeleton shares an INHIBITS relation in
    # the knowledge graph. The whole batch shares one formulation skeleton, so
    # an infeasible skeleton marks every run.
    infeasible: bool = False
    infeasible_reason: str | None = None

class DOEPlan(BaseModel):
    design: str  # full_factorial / fractional_factorial / plackett_burman / ccd / lhs
    factors: list[DOEFactor]
    runs: list[DOERun]
    notes: str = ""
    plan_id: str = ""  # assigned + cached by the workflow for export/round-trip
    domain: ProductDomain | None = None  # carried so exported runs round-trip on import
    # C-4b: plan lifecycle status (draft|active|completed|aborted); defaults to
    # draft when a plan is built. Serialized separately into doe_plans.status.
    status: str = "draft"

class RunExplanation(BaseModel):
    """Why a recommended DOE run was selected (P1 adaptive DOE)."""

    run_id: int
    strategy: Literal["exploration", "exploitation", "balanced", "constraint_fill"] = "exploration"
    summary: str
    nearest_experiment_ids: list[str] = Field(default_factory=list)
    predicted_delta_pct: float | None = None
    acquisition_score: float | None = None
    constraint_warnings: list[str] = Field(default_factory=list)

class AnomalyFlag(BaseModel):
    """Flagged historical experiment that may need review or re-test."""

    experiment_id: str
    type: Literal["high_residual", "physical_limit", "outlier_in_factor_space"]
    severity: Literal["info", "warning", "critical"] = "warning"
    note: str
    predicted: float | None = None
    actual: float | None = None

class AdaptiveDOEMetadata(BaseModel):
    """Shared adaptive-DOE fields attached to active-learning responses."""

    strategy_label: Literal["exploration", "balanced", "exploitation"] = "exploration"
    strategy_rationale: str = ""
    run_explanations: list[RunExplanation] = Field(default_factory=list)
    anomalies: list[AnomalyFlag] = Field(default_factory=list)
    recommended_next_action: str = ""
    budget_remaining: int | None = None

class BaybeRecommendResult(AdaptiveDOEMetadata):
    """Stateless baybe roundtrip payload (not persisted to DB)."""

    plan: DOEPlan
    campaign_state: str
    engine: str = "baybe"
    # v15: 实际喂给 GP 的 lab 点数（df_meas_clean 行数，含 workbench 通道）
    lab_points_used: int = 0
    # KG chemical-compatibility verdict for the shared formulation skeleton.
    # None when KG is disabled or no material resolved. Structured as
    # {"feasible": bool, "status": str, "reasons": list[str]}.
    chemical_feasibility: dict | None = None
    # v11: deterministic physical-constraint verdict (acid stability +
    # compliance) for the shared skeleton. None when the gate was skipped.
    # Structured as {"feasible", "status", "reasons", "acid_stability",
    # "compliance"}.
    physical_constraints: dict | None = None

class ActiveDoeResult(AdaptiveDOEMetadata):
    """Active-learning DOE response — plan plus optional baybe campaign state."""

    plan: DOEPlan
    campaign_state: str | None = None
    engine: str = "legacy"
    # KG chemical-compatibility verdict for the shared formulation skeleton.
    # Mirrors BaybeRecommendResult.chemical_feasibility (None when KG disabled).
    chemical_feasibility: dict | None = None
    # v11: deterministic physical-constraint verdict (acid stability +
    # compliance). Mirrors BaybeRecommendResult.physical_constraints.
    physical_constraints: dict | None = None
    # v16 P2-4: 实际喂给 GP 的 lab 点数（血缘；legacy 路径为 0）。
    lab_points_used: int = 0
    # id under which /doe/active stored the plan in ``doe_plans`` ("" = not
    # persisted, so it cannot be dispatched / read back by id).
    plan_id: str = ""

class OptimizationResult(BaseModel):
    iterations: int
    objective: str
    objectives: list[ObjectiveSpec] = Field(default_factory=list)
    history: list[float]
    top_formulations: list[Formulation]
    # Which optimizer engine produced this result (e.g. "numpy-ucb",
    # "optuna-tpe", "botorch-ei", "baybe"). Default preserves
    # backward compatibility.
    engine: str = "numpy-ucb"
    # P1 #22: history/observe values source. Virtual loops feed the optimizer
    # with predictor scores — not lab measurements — so the curve is self-
    # consistency of the surrogate, not real improvement.
    # "predictor_virtual" | "lab" | "skipped"
    measurement_source: str = "predictor_virtual"
    # How many measured lab experiments were shown to the optimizer (0 for a
    # purely virtual run). Lets the UI say "吸收了 N 条实验数据".
    lab_points_used: int = 0
    # 补齐 2-16: per-iteration acquisition values of this run:
    # [{iteration, acquisition (None when the engine has no scalar acquisition),
    # acquisition_name, best_score, engine}]. Empty for engines (BayBE) that do
    # not report a scalar acquisition. Persisted with the task result, so the
    # cockpit trend page reads it via GET /api/tasks/{task_id}.
    acquisition_history: list[dict] = Field(default_factory=list)

class Measurement(BaseModel):
    """One lab-observed value, with the context that makes it comparable.

    A bare number is not a result. 720 hours of salt spray under ASTM B117 and
    under ISO 9227 are different experiments, and a value with no spec limit
    cannot be judged pass or fail. Everything beyond ``metric``/``value`` is
    optional so existing callers keep working, but the fields exist so a QC
    report can land here without losing what makes it auditable.
    """

    metric: str
    value: float
    unit: str = ""
    # Standard the measurement was taken under: ASTM B117 / ISO 9227 / GB/T 1771.
    test_method: str = ""
    instrument: str = ""
    operator: str = ""
    measured_at: datetime | None = None
    # Acceptance window. ``passed`` is derived when limits are known.
    spec_min: float | None = None
    spec_max: float | None = None
    passed: bool | None = None
    # Provenance: the ingested QC report this value was read from.
    source_document_id: str | None = None
    note: str = ""

    @model_validator(mode="after")
    def _derive_passed(self) -> Measurement:
        if self.passed is None and (self.spec_min is not None or self.spec_max is not None):
            ok = True
            if self.spec_min is not None and self.value < self.spec_min:
                ok = False
            if self.spec_max is not None and self.value > self.spec_max:
                ok = False
            object.__setattr__(self, "passed", ok)
        return self

REAL_SOURCES = frozenset({"lab", "workbench"})

VIRTUAL_SOURCES = frozenset({"baybe_opt", "predictor_virtual"})

class ExperimentRecord(BaseModel):
    """A single measured DOE/lab result fed back into the platform.

    ``factors`` are the formulation levers in natural units (matching a DOE
    run's ``natural`` values, e.g. ingredient wt% and cure temperature).

    Results live in ``measurements`` as typed rows. ``measured`` remains the
    flat ``{metric: value}`` view every consumer already reads — training, the
    BayBE adapter, active learning, DOE explain/anomaly — and is accepted as
    input so legacy payloads, stored JSON files, SQLite rows and Datalab blocks
    all still load unchanged.
    """

    domain: ProductDomain
    project_id: str = ""
    # v7: 离散因子（如材料名）允许 str；数值消费方自行过滤。
    factors: dict[str, float | str] = Field(default_factory=dict)
    cure_temperature_c: float | None = None
    measurements: list[Measurement] = Field(default_factory=list)
    source: str = "lab"
    label: str = ""

    @model_validator(mode="before")
    @classmethod
    def _lift_legacy_measured(cls, data):
        """Accept ``measured={metric: value}`` and store it as typed rows.

        This is the whole backward-compatibility story: every existing caller,
        every JSON file on disk, and every Datalab ``formumind_training`` block
        passes ``measured``, and none of them need to change.
        """
        if not isinstance(data, dict):
            return data
        raw = data.get("measured")
        if not raw or data.get("measurements"):
            return data
        if isinstance(raw, dict):
            data = dict(data)
            data["measurements"] = [
                {"metric": k, "value": v}
                for k, v in raw.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
            ]
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def measured(self) -> dict[str, float]:
        """Flat metric → value view.

        A computed field rather than a plain property so it survives
        ``model_dump()`` — the JSON store and the Datalab block writer both
        serialize records, and a rolled-back binary must still be able to read
        what a newer one wrote.
        """
        return {m.metric: m.value for m in self.measurements}

    def measurement_for(self, metric: str) -> Measurement | None:
        return next((m for m in self.measurements if m.metric == metric), None)

class ExperimentSubmission(BaseModel):
    records: list[ExperimentRecord]
    retrain: bool = True

class ModelInfo(BaseModel):
    domain: ProductDomain
    project_id: str = ""
    metric: str
    backend: str  # sklearn-rf / numpy-ridge
    n_samples: int
    r2: float
    cv_r2: float | None = None
    rmse: float
    # P1 #20: versioning for disk-backed surrogates (joblib under data/models/).
    trained_at: str | None = None  # ISO-8601 UTC
    data_hash: str | None = None  # hash of training rows for this (project, metric)
    feature_version: str | None = None  # features.feature_set_version()
    version_id: str | None = None  # artifact folder / filename stem
    # P1 #19: absolute residual quantile from k-fold OOF (≈90% coverage half-width).
    conformal_q90: float | None = None
    uncertainty_calibrated: bool = False
    # A rollback pins the chosen version: retrains archive new versions beside it without
    # taking over. ``newer_version_id`` is the newest archived version when it is not the
    # one being served (so the UI can say "locked at X, Y is waiting").
    pinned: bool = False
    newer_version_id: str | None = None

class TrainingReport(BaseModel):
    trained: list[ModelInfo]
    total_records: int
    message: str = ""

class LoopRequest(Requirement):
    """Requirement extended with closed-loop iteration controls."""

    optimize_iterations: int = 24
    n_suggest: int = 4
    optimize_engine: str = "auto"
    doe_engine: str = "auto"
    workbench_campaign_id: int | None = None
    campaign_state: str | None = None
    prior_rmse_history: list[dict[str, float]] = Field(default_factory=list)
    prior_optimization: OptimizationResult | None = None
    prior_next_doe: DOEPlan | None = None
    budget_remaining: int | None = None
    # v16: DOE 冷启动种子。None = 确定性默认 0；整数 = 可复现。  # v23-fix: 与 v22 的两处对齐
    seed: int | None = None

class LoopReport(AdaptiveDOEMetadata):
    """One turn of the experiment → retrain → optimize → next-DOE loop."""

    domain: str
    total_records: int
    model_info: list[ModelInfo] = Field(default_factory=list)
    rmse_by_metric: dict[str, float] = Field(default_factory=dict)
    optimization: OptimizationResult
    next_doe: DOEPlan
    engine: str = "offline"
    campaign_state: str | None = None
    converged: bool = False
    loop_message: str = ""
    # KG chemical-compatibility verdict for the recommended batch's skeleton.
    chemical_feasibility: dict | None = None
    # v11: deterministic physical-constraint verdict (acid stability +
    # compliance) for the recommended batch's skeleton.
    physical_constraints: dict | None = None
    # 成本/碳足迹摘要（top 配方均值），供前端展示成本趋势
    cost_summary: dict | None = None

class IntentParseRequest(BaseModel):
    text: str

class IntentResult(BaseModel):
    requirement: Requirement
    confidence: float  # 0..1 heuristic confidence
    extracted_fields: list[str]  # which Requirement fields were populated
    engine: str  # "llm" | "offline-heuristic"
    warnings: list[str] = Field(default_factory=list)

class DatalabSampleResponse(BaseModel):
    """Datalab POST /new-sample/ sample_list_entry contract."""

    item_id: str
    name: str | None = None
    refcode: str | None = None
    status: str | None = None

    model_config = {"extra": "ignore"}

class DatalabItemEnvelope(BaseModel):
    """Datalab GET /get-item-data/{id} outer wrapper."""

    item_data: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}

class DatalabDeleteResponse(BaseModel):
    """Datalab POST /delete-sample/ response."""

    status: str

    model_config = {"extra": "ignore"}

class HardConstraint(BaseModel):
    """A requirement the formulation must satisfy, not merely trade against.

    ObjectiveSpec cannot express this: its weight only tilts a score, so a
    candidate violating a VOC ceiling still competes on the strength of its
    other metrics. A hard constraint removes it from consideration instead.
    """

    metric: str
    op: Literal["le", "ge", "between"] = "le"
    value: float
    value_max: float | None = None  # upper bound when op == "between"
    label: str = ""

    def violation(self, actual: float | None) -> float:
        """How far outside the constraint, in metric units. 0.0 = satisfied.

        A missing metric counts as satisfied — the predictor emits a
        domain-dependent, conditionally sparse key set, and treating absence as
        an infinite violation would reject every candidate on an unrelated
        metric the model simply does not produce.
        """
        if actual is None:
            return 0.0
        if self.op == "le":
            return max(0.0, float(actual) - self.value)
        if self.op == "ge":
            return max(0.0, self.value - float(actual))
        high = self.value_max if self.value_max is not None else self.value
        return max(0.0, self.value - float(actual), float(actual) - high)

class TargetSpec(BaseModel):
    """What the design must achieve (hard) and what it should optimise (soft)."""

    hard: list[HardConstraint] = Field(default_factory=list)
    soft: list[ObjectiveSpec] = Field(default_factory=list)

class InverseDesignRequest(Requirement):
    """Requirement extended with inverse-design search controls."""

    targets: TargetSpec = Field(default_factory=TargetSpec)
    population: int = Field(default=48, ge=8, le=400)
    generations: int = Field(default=30, ge=1, le=300)
    seed_with_llm: bool = True
    seed: int = 42

class DesignCandidate(BaseModel):
    """One formulation on (or near) the frontier."""

    formulation: Formulation
    pareto_rank: int | None = None
    feasible: bool = True
    violation: float = 0.0
    materials: list[str] = Field(default_factory=list)

class InverseDesignResult(BaseModel):
    topic: str = ""
    domain: str
    candidates: list[DesignCandidate] = Field(default_factory=list)
    pareto_frontier_ids: list[str] = Field(default_factory=list)
    tradeoff: TradeOffAnalysis | None = None
    generations: int = 0
    evaluations: int = 0
    rejected_infeasible: int = 0
    seeded_from: dict[str, int] = Field(default_factory=dict)
    engine: str = "nsga2-numpy"
    warnings: list[str] = Field(default_factory=list)

from ..tradeoff_schemas import TradeOffAnalysis  # noqa: E402

# Forward refs to later-defined classes (as in the original tail).
Requirement.model_rebuild()
InverseDesignRequest.model_rebuild()
