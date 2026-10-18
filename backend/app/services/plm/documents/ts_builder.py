"""Technical Standard (TS) content — the product's internal quality standard.

TS vs TDS (整合方案 §3.1): TS is the *internal* control document — product
physicochemical properties, quality indicators with specification limits,
factory-inspection items, test methods, decision rules. TDS is the *external*
user-facing data sheet (typical values only). The two are independent
documents; TS limits are never published in the TDS.

Limits are NEVER fabricated: indicators come from the caller (payload /
TS template structure / the revision's spec snapshot, all of which are
quality- or contract-defined). An indicator without any of min / max /
target is reported in ``validation.missing`` so the TS cannot be approved
until quality sets its limits.

``meta.indicators`` is the structured indicator list COA generation falls
back to (factory-inspection indicators become COA specs when no explicit
specs exist). Indicator values deliberately do NOT enter
``meta.facts.properties`` — a TS spec target is not a measured value, and the
release gate's cross-document consistency check must not compare it against
measured SDS / TDS values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .common import DocumentContent, t


@dataclass
class TsIndicator:
    key: str  # metric key, shared with COA specs / release_specs (e.g. "solids_content_pct")
    label: str = ""  # e.g. "不挥发物含量"
    unit: str = ""
    spec_min: float | None = None
    spec_max: float | None = None
    spec_target: float | None = None
    test_method: str = ""  # e.g. "GB/T 1725-2007"
    is_factory_inspection: bool = False
    source: str = "manual"  # manual | contract | template
    measurement_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "unit": self.unit,
                "spec_min": self.spec_min, "spec_max": self.spec_max, "spec_target": self.spec_target,
                "test_method": self.test_method, "is_factory_inspection": self.is_factory_inspection,
                "source": self.source, "measurement_id": self.measurement_id}


def clean_indicators(raw: list[dict[str, Any]] | None) -> tuple[list[TsIndicator], list[str]]:
    """Validate raw indicator dicts; invalid rows are dropped with a warning (never silently fixed)."""
    out: list[TsIndicator] = []
    warnings: list[str] = []
    for i, r in enumerate(raw or []):
        key = str(r.get("key") or "").strip()
        label = str(r.get("label") or key).strip()
        if not key:
            warnings.append(f"第 {i + 1} 个指标缺少 key，已跳过")
            continue
        try:
            mn = None if r.get("spec_min") in (None, "") else float(r["spec_min"])
            mx = None if r.get("spec_max") in (None, "") else float(r["spec_max"])
            tg = None if r.get("spec_target") in (None, "") else float(r["spec_target"])
        except (TypeError, ValueError):
            warnings.append(f"指标 {label} 的规格限值不是数值，已跳过")
            continue
        if mn is not None and mx is not None and mn > mx:
            warnings.append(f"指标 {label} 的下限 {mn:g} 大于上限 {mx:g}，已跳过")
            continue
        out.append(TsIndicator(key=key, label=label, unit=str(r.get("unit") or ""),
                               spec_min=mn, spec_max=mx, spec_target=tg,
                               test_method=str(r.get("test_method") or r.get("method") or ""),
                               is_factory_inspection=bool(r.get("is_factory_inspection")),
                               source=str(r.get("source") or "manual"),
                               measurement_id=r.get("measurement_id")))
    return out, warnings


def _limit(ind: TsIndicator) -> str:
    if ind.spec_min is not None and ind.spec_max is not None:
        return f"{ind.spec_min:g} ~ {ind.spec_max:g}"
    if ind.spec_min is not None:
        return f"≥ {ind.spec_min:g}"
    if ind.spec_max is not None:
        return f"≤ {ind.spec_max:g}"
    if ind.spec_target is not None:
        return f"{ind.spec_target:g}（目标值）"
    return "—"


@dataclass
class TsContext:
    product: dict[str, Any]  # name_zh, name_en, code, category
    company: dict[str, Any]
    indicators: list[TsIndicator] = field(default_factory=list)
    properties: list[dict[str, Any]] = field(default_factory=list)  # key, label, value, unit, method, measurement_id
    inspection: dict[str, Any] = field(default_factory=dict)  # sampling, decision, notes
    packaging: dict[str, Any] = field(default_factory=dict)  # spec, conditions, shelf_life
    revision: dict[str, Any] = field(default_factory=dict)  # version, date, change_summary
    lang: str = "zh-CN"


def build_ts(ctx: TsContext) -> dict:
    lang = ctx.lang
    name = ctx.product.get("name_en" if lang == "en-US" else "name_zh") or ctx.product.get("name_zh")
    doc = DocumentContent(
        doc_type="ts", lang=lang, title=t(lang, "产品技术标准（TS）", "Technical Standard (TS)"),
        header={"product_name": name, "product_code": ctx.product.get("code"),
                "version": ctx.revision.get("version"), "revision_date": ctx.revision.get("date")},
    )
    s = doc.section(1, "identification", t(lang, "产品标识", "Product identification"))
    s.add("product_name", t(lang, "产品名称", "Product"), doc.require(name, "产品名称"), "product")
    s.add("product_code", t(lang, "产品代码", "Product code"), ctx.product.get("code"), "product")
    s.add("category", t(lang, "产品类别", "Category"), ctx.product.get("category"), "product")
    s.add("company", t(lang, "制定单位", "Issued by"), doc.require(ctx.company.get("name"), "制定单位"), "company")
    s.add("version", t(lang, "标准版本", "Standard version"), ctx.revision.get("version"), "product")

    p = doc.section(2, "properties", t(lang, "理化特性", "Physicochemical properties"))
    if not ctx.properties:
        doc.missing.append("理化特性")
    p.table("properties", [t(lang, "项目", "Property"), t(lang, "典型值", "Typical value"),
                           t(lang, "单位", "Unit"), t(lang, "测试方法", "Test method")],
            [[x.get("label") or x["key"], x.get("value"), x.get("unit", ""), x.get("method", "")]
             for x in ctx.properties], "measured")

    q = doc.section(3, "indicators", t(lang, "质量指标", "Quality indicators"))
    if not ctx.indicators:
        doc.missing.append("质量指标")
    q.table("indicators", [t(lang, "指标", "Indicator"), t(lang, "单位", "Unit"), t(lang, "规格", "Specification"),
                           t(lang, "目标值", "Target"), t(lang, "检验方法", "Test method"),
                           t(lang, "出厂检验", "Factory inspection")],
            [[i.label or i.key, i.unit, _limit(i), i.spec_target if i.spec_target is not None else "—",
              i.test_method, t(lang, "是", "Yes") if i.is_factory_inspection else t(lang, "否", "No")]
             for i in ctx.indicators], "manual")
    for i in ctx.indicators:
        if i.spec_min is None and i.spec_max is None and i.spec_target is None:
            doc.missing.append(f"指标 {i.label or i.key} 未设定规格限值")

    f = doc.section(4, "factory_inspection", t(lang, "出厂检验", "Factory inspection"))
    factory = [i for i in ctx.indicators if i.is_factory_inspection]
    if factory:
        f.table("factory_items", [t(lang, "检验项目", "Item"), t(lang, "规格", "Specification"),
                                  t(lang, "检验方法", "Test method")],
                [[i.label or i.key, _limit(i), i.test_method] for i in factory], "manual")
    else:
        f.paragraphs.append(t(lang, "本产品未标记出厂检验项目。", "No factory inspection items marked."))
    sampling = ctx.inspection.get("sampling")
    decision = ctx.inspection.get("decision")
    f.add("sampling", t(lang, "抽样规则", "Sampling rule"),
          doc.require(sampling, "出厂检验抽样规则"), "manual")
    f.add("decision", t(lang, "判定规则", "Decision rule"),
          decision or t(lang, "按 GB/T 8170-2008 进行数值修约与极限数值判定。",
                        "Rounding and limit judgement per GB/T 8170-2008."), "template")

    m = doc.section(5, "methods", t(lang, "检验方法", "Test methods"))
    methods = sorted({i.test_method for i in ctx.indicators if i.test_method})
    if methods:
        m.table("methods", [t(lang, "标准号", "Standard"), t(lang, "适用指标", "Indicators")],
                [[md, "、".join(i.label or i.key for i in ctx.indicators if i.test_method == md)] for md in methods],
                "manual")
    else:
        doc.missing.append("检验方法")

    st = doc.section(6, "packaging_storage", t(lang, "包装与储存", "Packaging and storage"))
    for key, zh, en in (("packaging", "包装规格", "Packaging"), ("conditions", "储存条件", "Storage conditions"),
                        ("shelf_life", "保质期", "Shelf life")):
        if ctx.packaging.get(key):
            st.add(key, t(lang, zh, en), ctx.packaging[key], "product")

    r = doc.section(7, "revisions", t(lang, "修订记录", "Revision history"))
    r.add("version", t(lang, "版本", "Version"), ctx.revision.get("version"), "product")
    r.add("date", t(lang, "修订日期", "Date"), ctx.revision.get("date"), "product")
    if ctx.revision.get("change_summary"):
        r.add("change_summary", t(lang, "变更摘要", "Change summary"), ctx.revision["change_summary"], "manual")

    doc.meta["indicators"] = [i.to_dict() for i in ctx.indicators]
    doc.meta["facts"] = {"ts_indicators": {i.key: {"min": i.spec_min, "max": i.spec_max,
                                                   "target": i.spec_target, "method": i.test_method}
                                           for i in ctx.indicators}}
    return doc.to_dict()
