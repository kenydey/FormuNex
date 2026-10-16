"""Technical Data Sheet content.

A TDS states what the product is, how to apply it and what it achieves. Its
typical-properties table must agree with the SDS: when the same property
(density, solids, flash point, VOC, pH) appears in both, both documents read
the same data point — ``properties`` entries carry ``measurement_id`` so the
release gate can prove it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .common import DocumentContent, t


@dataclass
class TdsContext:
    product: dict[str, Any]  # name_zh, name_en, code, description, features[], applications[]
    company: dict[str, Any]
    properties: list[dict[str, Any]] = field(default_factory=list)  # key, label, value, unit, method, source, measurement_id
    performance: list[dict[str, Any]] = field(default_factory=list)  # label, value, unit, method, substrate, condition
    application: dict[str, Any] = field(default_factory=dict)  # substrates, surface_prep, method, film_thickness, cure, recoat, dilution
    storage: dict[str, Any] = field(default_factory=dict)  # shelf_life, conditions, packaging
    revision: dict[str, Any] = field(default_factory=dict)
    lang: str = "zh-CN"


_APPLICATION_LABELS = [
    ("substrates", "适用基材", "Substrates"),
    ("surface_prep", "表面处理", "Surface preparation"),
    ("method", "施工方式", "Application method"),
    ("dilution", "稀释", "Thinning"),
    ("film_thickness", "推荐膜厚", "Recommended film thickness"),
    ("cure", "固化条件", "Curing schedule"),
    ("recoat", "重涂间隔", "Recoat interval"),
    ("coverage", "理论涂布率", "Theoretical coverage"),
]


def build_tds(ctx: TdsContext) -> dict:
    lang = ctx.lang
    name = ctx.product.get("name_en" if lang == "en-US" else "name_zh") or ctx.product.get("name_zh")
    doc = DocumentContent(
        doc_type="tds", lang=lang, title=t(lang, "产品技术数据表", "Technical Data Sheet"),
        header={"product_name": name, "product_code": ctx.product.get("code"),
                "version": ctx.revision.get("version"), "revision_date": ctx.revision.get("date")},
    )
    s = doc.section(1, "description", t(lang, "产品描述", "Product description"))
    s.add("product_name", t(lang, "产品名称", "Product"), doc.require(name, "产品名称"), "product")
    s.add("description", t(lang, "产品说明", "Description"),
          doc.require(ctx.product.get("description"), "产品说明"), "product")
    if ctx.product.get("features"):
        s.add("features", t(lang, "产品特点", "Features"), list(ctx.product["features"]), "product")
    if ctx.product.get("applications"):
        s.add("applications", t(lang, "应用领域", "Applications"), list(ctx.product["applications"]), "product")

    p = doc.section(2, "typical_properties", t(lang, "典型物性", "Typical properties"))
    if not ctx.properties:
        doc.missing.append("典型物性")
    p.table("properties", [t(lang, "项目", "Property"), t(lang, "典型值", "Typical value"), t(lang, "单位", "Unit"),
                           t(lang, "测试方法", "Test method")],
            [[x.get("label") or x["key"], x.get("value"), x.get("unit", ""), x.get("method", "")] for x in ctx.properties],
            "measured")
    p.paragraphs.append(t(lang, "以上为典型值，不作为产品规格。", "Typical values; not a specification."))

    if ctx.performance:
        q = doc.section(3, "performance", t(lang, "涂层性能", "Coating performance"))
        q.table("performance", [t(lang, "项目", "Property"), t(lang, "结果", "Result"), t(lang, "单位", "Unit"),
                                t(lang, "测试方法", "Test method"), t(lang, "条件", "Conditions")],
                [[x.get("label"), x.get("value"), x.get("unit", ""), x.get("method", ""),
                  "；".join(v for v in (x.get("substrate"), x.get("condition")) if v)] for x in ctx.performance],
                "measured")

    a = doc.section(4, "application", t(lang, "施工指南", "Application guide"))
    for key, zh, en in _APPLICATION_LABELS:
        if ctx.application.get(key) not in (None, ""):
            a.add(key, t(lang, zh, en), ctx.application[key], "product")
    if not a.fields:
        doc.missing.append("施工指南")

    st = doc.section(5, "storage", t(lang, "储存与包装", "Storage and packaging"))
    for key, zh, en in (("shelf_life", "保质期", "Shelf life"), ("conditions", "储存条件", "Storage conditions"),
                        ("packaging", "包装规格", "Packaging")):
        if ctx.storage.get(key):
            st.add(key, t(lang, zh, en), ctx.storage[key], "product")
    st.paragraphs.append(t(lang, "安全信息请参阅本产品的化学品安全技术说明书（SDS）。",
                           "For safety information refer to the Safety Data Sheet."))

    o = doc.section(6, "other", t(lang, "其他", "Other"))
    o.add("supplier", t(lang, "供应商", "Supplier"), doc.require(ctx.company.get("name"), "供应商"), "company")
    o.add("version", t(lang, "版本", "Version"), ctx.revision.get("version"), "product")
    o.paragraphs.append(t(lang, "本资料基于实验室测试与实践经验，用户应根据具体条件进行试验确认。",
                          "Data are based on laboratory tests and experience; users should verify suitability under their own conditions."))
    doc.meta["measurement_ids"] = {x["key"]: x.get("measurement_id") for x in ctx.properties if x.get("key")}
    doc.meta["facts"] = {"properties": {x["key"]: x.get("value") for x in ctx.properties
                                        if x.get("key") and isinstance(x.get("value"), (int, float))}}
    return doc.to_dict()
