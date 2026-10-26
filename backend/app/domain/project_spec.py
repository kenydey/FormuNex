"""ProjectSpec helpers — normalize free-text requirements and derive DOE levers."""
from __future__ import annotations

from typing import Any

from .levers import substrate_default_levers
from .schemas import (
    DOEFactor,
    Formulation,
    LeverSpec,
    ObjectiveSpec,
    ProductDomain,
    Requirement,
    Substrate,
    substrate_value,
)

# Roles typically held fixed (solvent fills to 100%).
_FIXED_ROLES = frozenset({"solvent", "pigment", "filler", "additive"})
_PROCESS_KEYS = frozenset({"cure_temperature_c"})

_DOMAIN_LABELS: dict[ProductDomain, str] = {
    ProductDomain.anticorrosion_coating: "防腐蚀涂料",
    ProductDomain.degreaser: "脱脂剂",
    ProductDomain.surface_treatment: "表面处理剂",
    ProductDomain.autodeposition_coating: "自沉积涂料",
}

_LEGACY_CONSTRAINT_LABELS: dict[str, str] = {
    "voc_limit_gpl": "VOC 上限",
    "cure_temperature_c": "固化温度上限",
    "ph_target": "pH 目标",
    "salt_spray_hours": "耐盐雾目标",
    "film_weight_gsm": "干膜重目标",
    "cleaning_efficiency": "清洗率目标",
}


def normalize_constraints(req: Requirement) -> dict[str, float]:
    """Merge legacy scalar constraint fields with ``constraint_values`` (SSOT)."""
    out: dict[str, float] = {}
    if req.voc_limit_gpl is not None:
        out[_LEGACY_CONSTRAINT_LABELS["voc_limit_gpl"]] = float(req.voc_limit_gpl)
    if req.cure_temperature_c is not None:
        out[_LEGACY_CONSTRAINT_LABELS["cure_temperature_c"]] = float(req.cure_temperature_c)
    if req.ph_target is not None:
        out[_LEGACY_CONSTRAINT_LABELS["ph_target"]] = float(req.ph_target)
    if req.salt_spray_hours:
        out[_LEGACY_CONSTRAINT_LABELS["salt_spray_hours"]] = float(req.salt_spray_hours)
    if req.film_weight_gsm:
        out[_LEGACY_CONSTRAINT_LABELS["film_weight_gsm"]] = float(req.film_weight_gsm)
    if req.cleaning_efficiency:
        out[_LEGACY_CONSTRAINT_LABELS["cleaning_efficiency"]] = float(req.cleaning_efficiency)
    for key, raw in (req.constraint_values or {}).items():
        if raw is not None:
            out[key] = float(raw)
    return out


_LEGACY_LEVERS: dict[ProductDomain, list[tuple[str, float, float]]] = {
    ProductDomain.anticorrosion_coating: [
        ("Zinc phosphate", 2.0, 14.0),
        ("Bisphenol-A epoxy (DGEBA)", 28.0, 48.0),
        ("Polyamide hardener", 8.0, 22.0),
    ],
    ProductDomain.degreaser: [
        ("Nonionic surfactant (C12-14 EO7)", 2.0, 12.0),
        ("Sodium metasilicate", 2.0, 14.0),
    ],
    # surface_treatment: use substrate_default_levers() in levers.py (SSOT)
    ProductDomain.autodeposition_coating: [
        ("Ferric fluoride (FeF3)", 0.2, 2.5),
        ("Acidic-stable epoxy-acrylic emulsion", 5.0, 16.0),
        ("Hydrofluoric acid (HF)", 0.05, 0.6),
    ],
}


def effective_project_id(req: Requirement) -> str:
    if req.project_id:
        return req.project_id
    if req.product_type:
        return req.product_type.strip().lower().replace(" ", "_")[:64]
    return req.domain.value


def normalize_requirement(req: Requirement) -> Requirement:
    """Fill product_type/application from legacy fields when empty."""
    data = req.model_dump()
    if not data.get("product_type"):
        data["product_type"] = _DOMAIN_LABELS.get(req.domain, req.domain.value)
    if not data.get("application"):
        data["application"] = substrate_value(req.substrate)
    if not data.get("project_id"):
        data["project_id"] = effective_project_id(req)
    return Requirement(**data)


def primary_objective(req: Requirement) -> str:
    objectives = getattr(req, "objectives", None) or []
    if objectives:
        return objectives[0].metric
    from ..pipeline.workflow import OBJECTIVE

    try:
        return OBJECTIVE.get(req.domain, "salt_spray_hours")
    except TypeError:
        # Test stubs may use an unhashable SimpleNamespace domain.
        return "salt_spray_hours"


def default_objectives_for(req: Requirement) -> list[ObjectiveSpec]:
    from ..pipeline.workflow import default_objectives

    return default_objectives(req.domain)


def _bounds_from_pct(
    current: float, *, margin: float = 0.3, cap: float | None = 100.0
) -> tuple[float, float]:
    """Bounds around *current*, clamped to *cap*.

    The 100 ceiling is a percent ceiling, so it only applies to wt%/%. A g/L
    bath concentration legitimately exceeds 100, and clamping it there would
    silently narrow the search range.
    """
    lo = max(0.0, current * (1.0 - margin))
    hi = current * (1.0 + margin)
    if cap is not None:
        hi = min(cap, hi)
    if hi - lo < 1.0:
        hi = lo + 1.0
        if cap is not None:
            hi = min(cap, hi)
    return round(lo, 4), round(hi, 4)


def derive_levers_from_formulation(
    form: Formulation, *, defaults: list[LeverSpec] | None = None
) -> list[LeverSpec]:
    """Pick adjustable ingredients from a formulation (exclude fixed roles).

    *defaults* is the substrate SSOT (``levers.py``). An ingredient it names
    keeps that lever's unit, and its amount is expressed in that unit.

    That is not a labelling detail. Surface-treatment baths are specified in
    g/L — 5–30 g/L for hexafluorozirconic acid — while a Formulation stores
    wt%. Emitting the wt% number under a g/L label (or vice versa) is a **10×
    error** in a real formulation, which is why ``test_g_per_litre_slot_does_
    not_shift_by_ten`` exists. Deriving levers from the formulation without
    consulting the SSOT reintroduced exactly that.
    """
    from .genome import Slot  # local: genome imports knowledge, project_spec does not need it eagerly

    by_name = {d.name: d for d in (defaults or [])}
    levers: list[LeverSpec] = []
    for ing in form.ingredients:
        if ing.role in _FIXED_ROLES:
            continue
        if ing.weight_pct <= 0:
            continue
        spec = by_name.get(ing.name)
        unit = spec.unit if spec else "wt%"
        amount = Slot.from_wt_pct(ing.weight_pct, unit)
        lo, hi = _bounds_from_pct(amount, cap=100.0 if unit in ("wt%", "%") else None)
        levers.append(LeverSpec(name=ing.name, low=lo, high=hi, unit=unit))
    return levers[:6]


def _numeric_window(value: Any) -> tuple[float, float] | None:
    """Normalise one process-window entry to a (lo, hi) range, or None.

    ``[lo, hi]`` interval → itself; a single number → a ±10% exploration
    range around it (mirrors DoeDesigner's ``fillFromProcessWindow`` for
    single-number windows); anything unparseable / zero-width / inverted →
    None (dropped, never a zero-width factor).
    """
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            lo, hi = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    elif value is not None:
        try:
            point = float(value)
        except (TypeError, ValueError):
            return None
        lo, hi = max(0.0, point * 0.9), point * 1.1
    else:
        return None
    if lo >= hi:
        return None
    return round(lo, 4), round(hi, 4)


def derive_process_levers(req: Requirement) -> list[LeverSpec]:
    """Process levers from the requirement's contract process window.

    补齐计划 2-10: 契约工艺窗口（process_window）优先于单值
    cure_temperature_c —— 规格契约是 DOE 的共同依据。
    窗口形状：前端存为 [lo, hi] 区间（Slider range）→ 杠杆即该区间；
    若为单个数字（API/旧数据）→ 固化温度按单值逻辑展开 [cure-30, cure]
    （既有行为），其余数值项按 ±10% 展开（与 DoeDesigner 的
    fillFromProcessWindow 一致）。

    P1-8: 窗口驱动的杠杆不再限领域、不限固化温度一项。SpecStudio
    的工艺窗口 Tab 对全部领域开放，窗口里的四项全部进入杠杆：
    固化温度 cure_temp_c（杠杆名沿用 cure_temperature_c）、固化时间
    cure_time_min、干膜厚度 film_thickness_um、施用方式
    application_method（允许多个取值的列表 → 离散因子，low/high 为
    水平序号占位，与快照重建的离散约定一致；单个定值不是可调因子，
    不成杠杆）。无窗口时保留既有行为：仅防腐蚀领域的单值固化温度
    成杠杆——Requirement.cure_temperature_c 默认 80，若对全部领域都
    展开默认值，会凭空给无关领域加一个从未设过的固化温度因子。
    """
    levers: list[LeverSpec] = []
    pw = req.process_window or {}
    window_cure = pw.get("cure_temp_c")
    cure_single: float | None = None  # legacy single-value cure temp to expand, if any
    if isinstance(window_cure, (list, tuple)) and len(window_cure) == 2:
        try:
            lo, hi = float(window_cure[0]), float(window_cure[1])
        except (TypeError, ValueError):
            lo = hi = None
        if lo is not None and lo < hi:
            # Window interval wins; other window items below still apply.
            levers.append(LeverSpec(name="cure_temperature_c", low=lo, high=hi, unit="C"))
        else:
            # Invalid interval falls back to the single-value path (old behaviour).
            cure_single = req.cure_temperature_c
    elif window_cure is not None:
        try:
            cure_single = float(window_cure)
        except (TypeError, ValueError):
            cure_single = req.cure_temperature_c
    else:
        cure_single = req.cure_temperature_c
    if cure_single is not None and (
            window_cure is not None or req.domain == ProductDomain.anticorrosion_coating):
        # Window-driven values are domain-free (the window tab is open to all
        # domains); the no-window field fallback stays anticorrosion-only so
        # the Requirement default of 80 C never invents a lever for other domains.
        high = float(cure_single)
        low = max(20.0, cure_single - 30)
        # A cure target at/below the 20 C floor leaves no range to explore;
        # emitting low>=high produced a zero-width or inverted factor that
        # decode() silently mapped backwards.
        if low < high:
            levers.append(LeverSpec(name="cure_temperature_c", low=low, high=high, unit="C"))
    for key, name, unit in (("cure_time_min", "cure_time_min", "min"),
                            ("film_thickness_um", "film_thickness_um", "μm")):
        bounds = _numeric_window(pw.get(key))
        if bounds is not None:
            lo, hi = bounds
            levers.append(LeverSpec(name=name, low=lo, high=hi, unit=unit))
    methods = pw.get("application_method")
    if isinstance(methods, (list, tuple)):
        levels = [str(m) for m in methods if str(m).strip()]
        seen: set[str] = set()
        levels = [m for m in levels if not (m in seen or seen.add(m))]
        if len(levels) >= 2:
            levers.append(LeverSpec(name="application_method", low=0.0, high=float(len(levels) - 1),
                                    unit="", kind="discrete", levels=levels))
    # 自定义工艺窗口（与 process_window 平行，不动既有 4 键）：数值区间 → 连续
    # 杠杆（杠杆名取英文 key，与窗口校验的键一致）；离散列表 ≥2 → 离散杠杆。
    # 非法项（空键、与既有键冲突、区间倒置、离散不足 2 个水平）直接丢弃，
    # 绝不产生零宽或倒置因子（与 _numeric_window 的丢弃约定一致）。
    for param in _custom_window_params(req):
        key = str(param.get("key") or "").strip()
        if not key or key in ("cure_temp_c", "cure_time_min", "film_thickness_um", "application_method"):
            continue
        unit = str(param.get("unit") or "")
        if _is_discrete_param(param):
            levels = _discrete_levels(param)
            if len(levels) >= 2:
                levers.append(LeverSpec(name=key, low=0.0, high=float(len(levels) - 1),
                                        unit=unit, kind="discrete", levels=levels))
            continue
        bounds = _custom_range(param)
        if bounds is not None:
            lo, hi = bounds
            levers.append(LeverSpec(name=key, low=lo, high=hi, unit=unit))
    return levers


def _custom_window_params(req: Requirement) -> list[dict]:
    """Custom process-window params from the requirement (defensive parse)."""
    raw = getattr(req, "process_window_custom", None) or []
    return [p for p in raw if isinstance(p, dict)]


def _is_discrete_param(param: dict) -> bool:
    if param.get("kind") == "discrete":
        return True
    if param.get("kind") == "range":
        return False
    values = param.get("values")
    return isinstance(values, (list, tuple)) and any(str(v).strip() for v in values)


def _discrete_levels(param: dict) -> list[str]:
    values = param.get("values") or []
    levels = [str(v) for v in values if str(v).strip()]
    seen: set[str] = set()
    return [v for v in levels if not (v in seen or seen.add(v))]


def _custom_range(param: dict) -> tuple[float, float] | None:
    try:
        lo, hi = float(param.get("low")), float(param.get("high"))
    except (TypeError, ValueError):
        return None
    if lo >= hi:
        return None
    return round(lo, 4), round(hi, 4)


# 补齐计划 2-10: 工艺参数名 → 工艺窗口键的别名。DOE/任务因子名可能是英文
# 杠杆名（cure_temperature_c）或中文名（固化温度），统一映射到窗口键。
_PROCESS_WINDOW_ALIASES: dict[str, str] = {
    "cure_temperature_c": "cure_temp_c",
    "cure_temp_c": "cure_temp_c",
    "cure_temp": "cure_temp_c",
    "固化温度": "cure_temp_c",
    "cure_time_min": "cure_time_min",
    "cure_time": "cure_time_min",
    "固化时间": "cure_time_min",
    "film_thickness_um": "film_thickness_um",
    "film_thickness": "film_thickness_um",
    "干膜厚度": "film_thickness_um",
    "application_method": "application_method",
    "施工方式": "application_method",
}


def check_process_window(
    process_params: dict[str, Any] | None,
    process_window: dict[str, Any] | None,
    custom_params: list[dict] | None = None,
) -> list[str]:
    """Warn (never block) when process params deviate from the contract window.

    窗口形状（与前端 SpecStudioPage 一致）：数值窗口为 [lo, hi] 区间或单个定点；
    application_method 为允许列表。参数落入区间 / 在列表中 → 无警告；否则警告。
    窗口为空 / 参数为空 → 无警告。数值容差 1e-6。

    自定义窗口参数（custom_params，与 process_window 平行）：按英文 key 或中文
    label 匹配参数名；数值区间与离散列表的校验与既有键同一规则。既有键与
    自定义键重名时既有键优先（前端保存时已拦截重名，后端再防一层）。
    """
    if not process_params or (not process_window and not custom_params):
        return []
    labels = {"cure_temp_c": "固化温度", "cure_time_min": "固化时间", "film_thickness_um": "干膜厚度"}
    units = {"cure_temp_c": "°C", "cure_time_min": "min", "film_thickness_um": "μm"}
    warnings: list[str] = []
    handled: set[str] = set()
    for name, value in process_params.items():
        key = _PROCESS_WINDOW_ALIASES.get(str(name))
        if key is None or process_window is None or key not in process_window:
            continue
        handled.add(str(name))
        window_value = process_window.get(key)
        if window_value is None:
            continue
        if key == "application_method":
            allowed = window_value if isinstance(window_value, (list, tuple)) else [window_value]
            if str(value) not in [str(a) for a in allowed]:
                warnings.append(f"施工方式超出工艺窗口：窗口允许 {', '.join(str(a) for a in allowed)}，实际 {value}")
            continue
        try:
            actual = float(value)
        except (TypeError, ValueError):
            continue
        if isinstance(window_value, (list, tuple)) and len(window_value) == 2:
            try:
                lo, hi = float(window_value[0]), float(window_value[1])
            except (TypeError, ValueError):
                continue
            if actual < lo - 1e-6 or actual > hi + 1e-6:
                warnings.append(
                    f"{labels.get(key, key)}超出工艺窗口：窗口允许 {lo:g}~{hi:g}{units.get(key, '')}，实际 {actual:g}{units.get(key, '')}"
                )
        else:
            try:
                expected = float(window_value)
            except (TypeError, ValueError):
                continue
            if abs(actual - expected) > 1e-6:
                warnings.append(
                    f"{labels.get(key, key)}超出工艺窗口：窗口要求 {expected:g}{units.get(key, '')}，实际 {actual:g}{units.get(key, '')}"
                )
    for param in (custom_params or []):
        if not isinstance(param, dict):
            continue
        key = str(param.get("key") or "").strip()
        if not key or key in ("cure_temp_c", "cure_time_min", "film_thickness_um", "application_method"):
            continue
        label = str(param.get("label") or key)
        unit = str(param.get("unit") or "")
        for name, value in process_params.items():
            if str(name) in handled or (str(name) != key and str(name) != label):
                continue
            handled.add(str(name))
            if _is_discrete_param(param):
                allowed = _discrete_levels(param)
                if str(value) not in allowed:
                    warnings.append(f"{label}超出工艺窗口：窗口允许 {', '.join(allowed)}，实际 {value}")
                continue
            bounds = _custom_range(param)
            if bounds is None:
                continue
            try:
                actual = float(value)
            except (TypeError, ValueError):
                continue
            lo, hi = bounds
            if actual < lo - 1e-6 or actual > hi + 1e-6:
                warnings.append(f"{label}超出工艺窗口：窗口允许 {lo:g}~{hi:g}{unit}，实际 {actual:g}{unit}")
    return warnings


def default_levers_for(
    domain: ProductDomain,
    substrate: Substrate | str = Substrate.carbon_steel,
    *,
    cure_temperature_c: float | None = None,
) -> list[LeverSpec]:
    """UI / API default DOE levers — same resolution as ``resolve_levers`` without explicit levers."""
    req = Requirement(
        domain=domain,
        substrate=substrate,
        cure_temperature_c=cure_temperature_c,
        levers=[],
    )
    return resolve_levers(req)


def _merge_explicit_levers(
    derived: list[LeverSpec], explicit: list[LeverSpec] | None
) -> list[LeverSpec]:
    """Merge user-explicit levers over formulation-derived ones (F-2).

    Explicit levers win on name match (replace) and are appended when new.
    Without this, ``resolve_levers`` priority 1 (formulation-derived) silently
    discards everything the user configured in LeversEditor — including
    Up-4A discrete factors — on the real ``generateDoe`` path.
    """
    if not explicit:
        return derived
    explicit_by_name = {e.name: e for e in explicit}
    merged: list[LeverSpec] = []
    seen: set[str] = set()
    for lever in derived:
        merged.append(explicit_by_name.get(lever.name, lever))
        seen.add(lever.name)
    for lever in explicit:
        if lever.name not in seen:
            merged.append(lever)
    return merged


def resolve_levers(req: Requirement, form: Formulation | None = None) -> list[LeverSpec]:
    """Resolve DOE levers: formulation-derived, then explicit user levers merged
    over them (explicit wins on name match), then substrate defaults, legacy.
    """
    # Substrate defaults are the SSOT for units and natural ranges. They are
    # resolved first so the formulation-derived path below can honour them:
    # deriving levers from ingredient weights alone loses the g/L unit and
    # lands every bath formulation 10x off.
    substrate_levers = substrate_default_levers(req)
    # Priority 1: derive from active formulation ingredients (most context-aware)
    source = form or req.active_formulation
    if source and source.ingredients:
        derived = derive_levers_from_formulation(source, defaults=substrate_levers)
        if derived:
            # F-2: explicit user levers (e.g. discrete factors from LeversEditor)
            # are merged over the derived set instead of being silently dropped.
            merged = _merge_explicit_levers(derived, req.levers)
            names = {l.name for l in merged}
            return merged + [l for l in derive_process_levers(req) if l.name not in names]
    # Priority 2: explicit user-specified levers
    if req.levers:
        return list(req.levers)
    # Priority 3: substrate-aware defaults
    if substrate_levers:
        return substrate_levers
    # Priority 4: legacy domain table
    legacy = _LEGACY_LEVERS.get(req.domain, [])
    levers = [LeverSpec(name=n, low=lo, high=hi, unit="wt%") for n, lo, hi in legacy]
    return levers + derive_process_levers(req)


def levers_to_doe_factors(levers: list[LeverSpec]) -> list[DOEFactor]:
    # Up-4A: 透传 kind/levels，离散因子进入生产链。
    # v8: material_map 已删（LeverSpec 是 SSOT，reconstruct 直接读 lever）。
    return [
        DOEFactor(
            name=l.name,
            low=l.low,
            high=l.high,
            unit=l.unit,
            kind=l.kind,
            levels=list(l.levels) if l.levels else None,
        )
        for l in levers
    ]


def lever_snapshot_from_plan(plan, req: Requirement | None = None) -> list[dict]:
    """Persist DOE levers on Campaign — derive from plan.runs when factors list is empty."""
    from .schemas import ProductDomain

    if req and req.levers:
        return [lev.model_dump() for lev in req.levers]
    if plan.factors:
        # Up-4A: 快照带 kind/levels，离散因子 round-trip 不丢失。
        return [
            {
                "name": f.name,
                "low": f.low,
                "high": f.high,
                "unit": f.unit,
                "kind": getattr(f, "kind", "continuous"),
                "levels": list(f.levels) if getattr(f, "levels", None) else None,
            }
            for f in plan.factors
        ]

    domain = plan.domain or (req.domain if req else ProductDomain.anticorrosion_coating)
    if req:
        resolved = {lev.name: (lev.low, lev.high, lev.unit) for lev in resolve_levers(req)}
    else:
        resolved = {}
    legacy_map = {name: (lo, hi) for name, lo, hi in _LEGACY_LEVERS.get(domain, [])}
    keys: list[str] = []
    seen: set[str] = set()
    values: dict[str, list[float]] = {}
    str_values: dict[str, list[str]] = {}  # v9: 离散 str 水平单独收集
    for run in plan.runs:
        for key, raw in (run.natural or {}).items():
            if key not in seen:
                seen.add(key)
                keys.append(key)
            if isinstance(raw, str) and raw.strip():
                # v9: 字符串水平保留原值（float() 会跳过导致信息丢失）。
                if raw not in str_values.setdefault(key, []):
                    str_values[key].append(raw)
                continue
            try:
                values.setdefault(key, []).append(float(raw))
            except (TypeError, ValueError):
                continue

    snapshot: list[dict] = []
    for name in keys:
        if name in resolved:
            lo, hi, unit = resolved[name]
        elif name in legacy_map:
            lo, hi = legacy_map[name]
            unit = "C" if name in ("cure_temperature_c", "bath_temperature_c") else "wt%"
        elif str_values.get(name):
            # v9: runs 里是字符串水平 → 重建离散因子，而非伪造连续范围。
            # v10: 补 low/high/unit（DOEFactor/LeverSpec 的 low/high 必填，
            # 缺失会导致 ValidationError；离散水平用序号索引作占位范围）。
            _levels = str_values[name]
            snapshot.append({
                "name": name,
                "low": 0.0,
                "high": float(len(_levels) - 1),
                "unit": "",
                "kind": "discrete",
                "levels": _levels,
            })
            continue
        else:
            samples = values.get(name) or [50.0]
            lo, hi = _bounds_from_pct(sum(samples) / len(samples))
            unit = "C" if name in ("cure_temperature_c", "bath_temperature_c") else (
                "min" if name == "immersion_time_min" else "wt%"
            )
        snapshot.append({"name": name, "low": lo, "high": hi, "unit": unit})
    return snapshot
