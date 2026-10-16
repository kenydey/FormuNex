"""Precautionary label content per GB 15258-2009.

Label elements: product identifier (and hazardous ingredients), pictograms,
signal word, hazard statements, precautionary statements (the label subset the
engine selected), supplier identification, 24 h emergency telephone and the
reference to the SDS. ``check_label_size`` compares a label against the
minimum size GB 15258 sets for the container volume.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .common import DocumentContent, t

# GB 15258-2009 Table: container volume (L, upper bound inclusive) → minimum label size (mm).
LABEL_SIZES: list[tuple[float, tuple[int, int] | None]] = [
    (0.1, None),          # ≤ 0.1 L: simplified label allowed
    (3, (50, 75)),
    (50, (75, 100)),
    (500, (100, 150)),
    (1000, (150, 200)),
    (float("inf"), (200, 300)),
]


def required_label_size(volume_l: float) -> tuple[int, int] | None:
    for limit, size in LABEL_SIZES:
        if volume_l <= limit:
            return size
    return LABEL_SIZES[-1][1]


def check_label_size(volume_l: float, width_mm: float, height_mm: float) -> dict[str, Any]:
    need = required_label_size(volume_l)
    if need is None:
        return {"ok": True, "required_mm": None, "note": "容器 ≤ 0.1 L，可使用简化标签"}
    w, h = sorted((width_mm, height_mm))
    nw, nh = sorted(need)
    ok = w >= nw and h >= nh
    return {"ok": ok, "required_mm": list(need),
            "note": "" if ok else f"容器 {volume_l:g} L 的标签不应小于 {need[0]}×{need[1]} mm（GB 15258）"}


@dataclass
class LabelContext:
    product: dict[str, Any]
    company: dict[str, Any]
    classification: dict[str, Any]
    statements: dict[str, dict[str, str]] = field(default_factory=dict)
    hazardous_ingredients: list[str] = field(default_factory=list)  # names shown under the product identifier
    container_volume_l: float | None = None
    size_mm: tuple[float, float] = (100, 150)
    qr_payload: str | None = None
    lang: str = "zh-CN"


def build_label(ctx: LabelContext) -> dict:
    lang = ctx.lang
    cls = ctx.classification
    name = ctx.product.get("name_en" if lang == "en-US" else "name_zh") or ctx.product.get("name_zh")
    doc = DocumentContent(doc_type="label", lang=lang, title=t(lang, "化学品安全标签", "Precautionary label"),
                          header={"product_name": name, "product_code": ctx.product.get("code")})

    def text(code: str) -> str:
        s = ctx.statements.get(code) or {}
        v = (s.get("en") if lang == "en-US" else s.get("zh")) or s.get("zh") or ""
        if not v:
            doc.warnings.append(f"{code}: statement text missing")
        elif "…" in v:
            doc.warnings.append(f"{code}: 防范说明含待补充内容（{v}），须按产品具体情况填写")
        return v

    s = doc.section(1, "label", doc.title)
    s.add("product_name", t(lang, "化学品标识", "Product identifier"), doc.require(name, "化学品标识"), "product")
    if ctx.hazardous_ingredients:
        s.add("ingredients", t(lang, "危险组分", "Hazardous ingredients"), ctx.hazardous_ingredients, "computed")
    s.add("pictograms", t(lang, "象形图", "Pictograms"), [p["code"] for p in cls.get("pictograms", [])], "computed")
    s.add("signal_word", t(lang, "信号词", "Signal word"),
          {"危险": t(lang, "危险", "Danger"), "警告": t(lang, "警告", "Warning")}.get(cls.get("signal_word") or ""),
          "computed")
    s.add("hazard_statements", t(lang, "危险性说明", "Hazard statements"),
          [{"code": c, "text": text(c)} for c in cls.get("h_codes", [])], "computed")
    s.add("precautionary_statements", t(lang, "防范说明", "Precautionary statements"),
          [{"code": c, "text": text(c)} for c in cls.get("label_p_codes", [])], "computed")
    s.add("sds_reference", "", t(lang, "请参阅化学品安全技术说明书", "Refer to the Safety Data Sheet"), "template")
    s.add("supplier", t(lang, "供应商", "Supplier"),
          doc.require(" ".join(v for v in (ctx.company.get("name"), ctx.company.get("address"), ctx.company.get("phone")) if v),
                      "供应商标识"), "company")
    s.add("emergency_phone", t(lang, "化学事故应急咨询电话", "Emergency telephone"),
          doc.require(ctx.company.get("emergency_phone"), "应急咨询电话"), "company")
    if not cls.get("hazards"):
        doc.warnings.append("产品不分类：GB 15258 不要求危险化学品安全标签")
    size = {"width_mm": ctx.size_mm[0], "height_mm": ctx.size_mm[1]}
    if ctx.container_volume_l is not None:
        check = check_label_size(ctx.container_volume_l, *ctx.size_mm)
        size.update(check)
        if not check["ok"]:
            doc.warnings.append(check["note"])
    doc.meta.update({"size": size, "qr_payload": ctx.qr_payload, "facts": {
        "pictograms": [p["code"] for p in cls.get("pictograms", [])], "signal_word": cls.get("signal_word"),
        "h_codes": list(cls.get("h_codes", [])), "p_codes": list(cls.get("label_p_codes", []))}})
    return doc.to_dict()
