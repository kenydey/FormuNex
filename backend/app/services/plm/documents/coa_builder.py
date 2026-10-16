"""Certificate of Analysis: batch results judged against release specifications.

Limit judgement follows GB/T 8170-2008 §4.3: unless a specification says
otherwise the *full-value* comparison method applies (the measured value as
is); a specification may ask for the *rounded-value* method, in which the
result is first rounded to the specification's precision with the standard's
rule (round half to even — 四舍六入五成双). Limits are inclusive.

A COA with any FAIL or missing result is not issuable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from .common import DocumentContent, t


def round_gbt8170(value: float, decimals: int) -> Decimal:
    """Round per GB/T 8170-2008 进舍规则 (half to even on the decimal value as written)."""
    q = Decimal(1).scaleb(-decimals)
    return Decimal(str(value)).quantize(q, rounding=ROUND_HALF_EVEN)


def _decimals(*limits: float | None) -> int:
    best = 0
    for x in limits:
        if x is None:
            continue
        try:
            exp = Decimal(str(x)).as_tuple().exponent
        except InvalidOperation:
            continue
        best = max(best, -exp if isinstance(exp, int) and exp < 0 else 0)
    return best


@dataclass
class SpecItem:
    metric: str
    label: str = ""
    unit: str = ""
    method: str = ""  # test method standard, e.g. GB/T 1725
    min: float | None = None
    max: float | None = None
    text: str | None = None  # qualitative requirement, e.g. "均匀无结块"
    comparison: str = "full"  # full | rounded
    decimals: int | None = None  # rounded comparison precision (default: precision of the limits)


@dataclass
class ResultItem:
    metric: str
    value: float | None = None
    value_text: str | None = None
    conforms: bool | None = None  # for qualitative requirements, judged by the analyst
    measurement_id: str | None = None


def judge(spec: SpecItem, result: ResultItem | None) -> tuple[str, str]:
    """Return (verdict, shown value). Verdicts: PASS | FAIL | NO_DATA."""
    if result is None or (result.value is None and result.value_text in (None, "") and result.conforms is None):
        return "NO_DATA", "—"
    if spec.text is not None and spec.min is None and spec.max is None:
        if result.conforms is None:
            return "NO_DATA", result.value_text or "—"
        return ("PASS" if result.conforms else "FAIL"), (result.value_text or ("符合" if result.conforms else "不符合"))
    if result.value is None:
        return "NO_DATA", result.value_text or "—"
    if spec.comparison == "rounded":
        d = spec.decimals if spec.decimals is not None else _decimals(spec.min, spec.max)
        v = round_gbt8170(result.value, d)
        shown = str(v)
        ok = (spec.min is None or v >= Decimal(str(spec.min))) and (spec.max is None or v <= Decimal(str(spec.max)))
    else:
        v = Decimal(str(result.value))
        shown = f"{result.value:g}"
        ok = (spec.min is None or v >= Decimal(str(spec.min))) and (spec.max is None or v <= Decimal(str(spec.max)))
    return ("PASS" if ok else "FAIL"), shown


def _requirement(spec: SpecItem, lang: str) -> str:
    if spec.text is not None and spec.min is None and spec.max is None:
        return spec.text
    if spec.min is not None and spec.max is not None:
        return f"{spec.min:g} ~ {spec.max:g}"
    if spec.min is not None:
        return f"≥ {spec.min:g}"
    if spec.max is not None:
        return f"≤ {spec.max:g}"
    return "—"


@dataclass
class CoaContext:
    product: dict[str, Any]
    company: dict[str, Any]
    batch: dict[str, Any]  # batch_no, produced_at, quantity_kg, expiry
    specs: list[SpecItem]
    results: list[ResultItem] = field(default_factory=list)
    coa_no: str | None = None
    lang: str = "zh-CN"


def build_coa(ctx: CoaContext) -> dict:
    lang = ctx.lang
    doc = DocumentContent(
        doc_type="coa", lang=lang, title=t(lang, "产品质量检验报告（COA）", "Certificate of Analysis"),
        header={"product_name": ctx.product.get("name_en" if lang == "en-US" else "name_zh") or ctx.product.get("name_zh"),
                "product_code": ctx.product.get("code"), "batch_no": ctx.batch.get("batch_no"), "coa_no": ctx.coa_no},
    )
    s = doc.section(1, "batch", t(lang, "批次信息", "Batch information"))
    s.add("product_name", t(lang, "产品名称", "Product"), doc.header["product_name"], "product")
    s.add("product_code", t(lang, "产品代码", "Product code"), ctx.product.get("code"), "product")
    s.add("batch_no", t(lang, "批号", "Batch No."), doc.require(ctx.batch.get("batch_no"), "批号"), "batch")
    s.add("produced_at", t(lang, "生产日期", "Date of manufacture"), ctx.batch.get("produced_at"), "batch")
    s.add("expiry", t(lang, "有效期至", "Expiry date"), ctx.batch.get("expiry"), "batch")
    s.add("quantity", t(lang, "批量", "Quantity"), ctx.batch.get("quantity_kg") and f"{ctx.batch['quantity_kg']} kg", "batch")

    by_metric = {r.metric: r for r in ctx.results}
    rows, verdicts = [], []
    for spec in ctx.specs:
        verdict, shown = judge(spec, by_metric.get(spec.metric))
        verdicts.append(verdict)
        rows.append([spec.label or spec.metric, spec.unit, _requirement(spec, lang), shown,
                     {"PASS": t(lang, "合格", "PASS"), "FAIL": t(lang, "不合格", "FAIL"),
                      "NO_DATA": t(lang, "未检", "NOT TESTED")}[verdict], spec.method])
    r = doc.section(2, "results", t(lang, "检验结果", "Test results"))
    r.table("results", [t(lang, "检验项目", "Item"), t(lang, "单位", "Unit"), t(lang, "技术要求", "Specification"),
                        t(lang, "检验结果", "Result"), t(lang, "单项判定", "Verdict"), t(lang, "检验方法", "Method")],
            rows, "measured")
    unknown_metrics = sorted(set(by_metric) - {s.metric for s in ctx.specs})
    if unknown_metrics:
        doc.warnings.append(f"结果中有规格未定义的项目：{', '.join(unknown_metrics)}")

    overall = "PASS" if verdicts and all(v == "PASS" for v in verdicts) else (
        "FAIL" if "FAIL" in verdicts else "INCOMPLETE")
    c = doc.section(3, "conclusion", t(lang, "检验结论", "Conclusion"))
    c.add("overall", t(lang, "结论", "Conclusion"), {
        "PASS": t(lang, "该批产品检验合格，准予出厂。", "The batch conforms to specification."),
        "FAIL": t(lang, "该批产品检验不合格，不得出厂。", "The batch does not conform to specification."),
        "INCOMPLETE": t(lang, "检验项目不完整，不能出具结论。", "Testing incomplete; no conclusion."),
    }[overall], "computed")
    c.add("rule", t(lang, "判定规则", "Decision rule"),
          t(lang, "按 GB/T 8170-2008 进行数值修约与极限数值判定。", "Rounding and limit judgement per GB/T 8170-2008."), "template")
    c.add("company", t(lang, "签发单位", "Issued by"), doc.require(ctx.company.get("name"), "签发单位"), "company")
    doc.meta.update({"overall": overall, "issuable": overall == "PASS",
                     "verdicts": dict(zip([s.metric for s in ctx.specs], verdicts, strict=True)),
                     "facts": {"properties": {r.metric: r.value for r in ctx.results
                                              if isinstance(r.value, (int, float))}}})
    return doc.to_dict()
