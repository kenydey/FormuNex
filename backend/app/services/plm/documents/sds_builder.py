"""Safety Data Sheet content per GB/T 16483-2008 (16 sections) and GB/T 17519-2013.

The hazard-dependent parts (section 2 classification and label elements,
section 3 hazardous ingredients, the acute-toxicity estimate in section 11,
aquatic classification in 12, transport in 14) come from the GB 30000 engine
result. Measured physical properties fill section 9. Standard wording for
sections 4-7, 10 and 13 is chosen by hazard class and marked ``template`` —
a starting point for the EHS reviewer, never final text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..regulatory.hazard_classes import BY_CODE
from .common import NO_DATA, DocumentContent, t
from .confidential_ranges import DEFAULT_EDGES, disclose

SECTION_TITLES = [
    ("identification", "化学品及企业标识", "Identification"),
    ("hazards", "危险性概述", "Hazards identification"),
    ("composition", "成分/组成信息", "Composition/information on ingredients"),
    ("first_aid", "急救措施", "First-aid measures"),
    ("fire_fighting", "消防措施", "Fire-fighting measures"),
    ("accidental_release", "泄漏应急处理", "Accidental release measures"),
    ("handling_storage", "操作处置与储存", "Handling and storage"),
    ("exposure_controls", "接触控制和个体防护", "Exposure controls/personal protection"),
    ("physical_chemical", "理化特性", "Physical and chemical properties"),
    ("stability", "稳定性和反应性", "Stability and reactivity"),
    ("toxicology", "毒理学信息", "Toxicological information"),
    ("ecology", "生态学信息", "Ecological information"),
    ("disposal", "废弃处置", "Disposal considerations"),
    ("transport", "运输信息", "Transport information"),
    ("regulatory", "法规信息", "Regulatory information"),
    ("other", "其他信息", "Other information"),
]

HAZARD_CLASS_EN = {
    "explosive": "Explosives", "flammable_gas": "Flammable gases", "chemically_unstable_gas": "Chemically unstable gases",
    "aerosol": "Aerosols", "oxidizing_gas": "Oxidizing gases", "gas_under_pressure": "Gases under pressure",
    "flammable_liquid": "Flammable liquids", "flammable_solid": "Flammable solids",
    "self_reactive": "Self-reactive substances and mixtures", "pyrophoric_liquid": "Pyrophoric liquids",
    "pyrophoric_solid": "Pyrophoric solids", "self_heating": "Self-heating substances and mixtures",
    "water_reactive": "Substances and mixtures which, in contact with water, emit flammable gases",
    "oxidizing_liquid": "Oxidizing liquids", "oxidizing_solid": "Oxidizing solids",
    "organic_peroxide": "Organic peroxides", "metal_corrosive": "Corrosive to metals",
    "acute_tox_oral": "Acute toxicity - oral", "acute_tox_dermal": "Acute toxicity - dermal",
    "acute_tox_inhal": "Acute toxicity - inhalation", "skin_corrosion": "Skin corrosion/irritation",
    "eye_damage": "Serious eye damage/eye irritation", "resp_sensitizer": "Respiratory sensitization",
    "skin_sensitizer": "Skin sensitization", "mutagenicity": "Germ cell mutagenicity",
    "carcinogenicity": "Carcinogenicity", "reproductive": "Reproductive toxicity",
    "stot_se": "Specific target organ toxicity - single exposure",
    "stot_re": "Specific target organ toxicity - repeated exposure", "aspiration": "Aspiration hazard",
    "aquatic_acute": "Hazardous to the aquatic environment - acute",
    "aquatic_chronic": "Hazardous to the aquatic environment - long-term", "ozone": "Hazardous to the ozone layer",
}

# Section 9 property keys (GB/T 16483 section 9 list), with labels and units.
PROPERTIES: list[tuple[str, str, str, str]] = [
    ("appearance", "外观与性状", "Appearance", ""),
    ("odor", "气味", "Odour", ""),
    ("ph", "pH 值", "pH", ""),
    ("melting_point_c", "熔点/凝固点", "Melting point/freezing point", "℃"),
    ("initial_boiling_point_c", "初沸点和沸程", "Initial boiling point and boiling range", "℃"),
    ("flash_point_c", "闪点（闭杯）", "Flash point (closed cup)", "℃"),
    ("evaporation_rate", "蒸发速率", "Evaporation rate", ""),
    ("explosive_limits", "爆炸上限/下限", "Upper/lower explosive limits", "%(V/V)"),
    ("vapour_pressure_kpa", "蒸气压", "Vapour pressure", "kPa"),
    ("relative_vapour_density", "蒸气密度", "Vapour density", ""),
    ("density_g_cm3", "相对密度/密度", "Relative density/density", "g/cm³"),
    ("solubility", "溶解性", "Solubility", ""),
    ("log_kow", "n-辛醇/水分配系数", "Partition coefficient n-octanol/water", ""),
    ("autoignition_c", "自燃温度", "Auto-ignition temperature", "℃"),
    ("decomposition_c", "分解温度", "Decomposition temperature", "℃"),
    ("kinematic_viscosity_40c", "运动黏度（40 ℃）", "Kinematic viscosity (40 °C)", "mm²/s"),
    ("solids_content_pct", "固体含量", "Solids content", "%"),
    ("voc_g_l", "挥发性有机物（VOC）含量", "VOC content", "g/L"),
]


@dataclass
class SdsContext:
    product: dict[str, Any]
    company: dict[str, Any]
    classification: dict[str, Any]  # ClassificationResult.to_dict()
    statements: dict[str, dict[str, str]] = field(default_factory=dict)  # code → {"zh", "en"}
    properties: dict[str, dict[str, Any]] = field(default_factory=dict)  # key → {"value", "source", "method"}
    components: list[dict[str, Any]] = field(default_factory=list)  # disclosure rows (+ generic_name)
    oel: dict[str, dict[str, Any]] = field(default_factory=dict)  # CAS → {"mac", "pc_twa", "pc_stel"}
    regulations: list[str] = field(default_factory=list)  # ingredient-specific section 15 entries
    revision: dict[str, Any] = field(default_factory=dict)  # version, date, change_summary
    band_edges: tuple[float, ...] = DEFAULT_EDGES
    lang: str = "zh-CN"


def _cls_name(hazard_class: str, lang: str) -> str:
    return HAZARD_CLASS_EN.get(hazard_class, hazard_class) if lang == "en-US" else BY_CODE[hazard_class].names[0]


def _cat(category: str, lang: str) -> str:
    if category == "lact":
        return t(lang, "附加类别（哺乳效应）", "additional category (effects on or via lactation)")
    if category in "ABCDEFG" and len(category) == 1:
        return t(lang, f"{category} 型", f"Type {category}")
    if category in ("gas", "refrigerated"):
        return t(lang, "压缩/液化/溶解气体" if category == "gas" else "冷冻液化气体",
                 "compressed/liquefied/dissolved gas" if category == "gas" else "refrigerated liquefied gas")
    return t(lang, f"类别 {category}", f"Category {category}")


def _stmt(ctx: SdsContext, code: str, content: DocumentContent) -> str:
    texts = ctx.statements.get(code) or {}
    text = texts.get("en") if ctx.lang == "en-US" else texts.get("zh")
    if not text and ctx.lang == "en-US" and texts.get("zh"):
        content.warnings.append(f"{code}: English statement text missing, Chinese text used")
        text = texts.get("zh")
    if not text:
        content.warnings.append(f"{code}: statement text missing")
    elif "…" in text:
        content.warnings.append(f"{code}: 防范说明含待补充内容（{text}），须按产品具体情况填写")
    return f"{code} {text or ''}".strip()


def _has(cls: dict, hazard_class: str, categories: tuple[str, ...] | None = None) -> bool:
    return any(h["hazard_class"] == hazard_class and (categories is None or h["category"] in categories)
               for h in cls.get("hazards", []))


def build_sds(ctx: SdsContext) -> dict:
    lang = ctx.lang
    cls = ctx.classification
    nd = NO_DATA[lang]
    product_name = ctx.product.get("name_en" if lang == "en-US" else "name_zh") or ctx.product.get("name_zh")
    doc = DocumentContent(
        doc_type="sds", lang=lang,
        title=t(lang, "化学品安全技术说明书", "Safety Data Sheet"),
        header={"product_name": product_name, "product_code": ctx.product.get("code"),
                "version": ctx.revision.get("version"), "revision_date": ctx.revision.get("date"),
                "standard": "GB/T 16483-2008, GB/T 17519-2013"},
        meta={"rule_set_version": cls.get("rule_set_version"), "inputs_hash": cls.get("inputs_hash"),
              "template_sections": [4, 5, 6, 7, 10, 13]},
    )
    S = {key: doc.section(i + 1, key, t(lang, zh, en)) for i, (key, zh, en) in enumerate(SECTION_TITLES)}
    flammable = _has(cls, "flammable_liquid", ("1", "2", "3"))
    aquatic = _has(cls, "aquatic_acute") or _has(cls, "aquatic_chronic")

    # 1 ------------------------------------------------------------------------------------------------
    s = S["identification"]
    s.add("product_name", t(lang, "化学品中文名称", "Product name"), doc.require(product_name, "1.产品名称"), "product")
    if lang == "zh-CN" and ctx.product.get("name_en"):
        s.add("product_name_en", "化学品英文名称", ctx.product["name_en"], "product")
    s.add("product_code", t(lang, "产品代码", "Product code"), ctx.product.get("code"), "product")
    s.add("recommended_use", t(lang, "推荐用途", "Recommended use"), ctx.product.get("recommended_use") or nd, "product")
    s.add("restrictions", t(lang, "限制用途", "Uses advised against"), ctx.product.get("restricted_use") or nd, "product")
    s.add("supplier", t(lang, "企业名称", "Supplier"), doc.require(ctx.company.get("name"), "1.企业名称"), "company")
    s.add("address", t(lang, "地址", "Address"), doc.require(ctx.company.get("address"), "1.企业地址"), "company")
    s.add("phone", t(lang, "电话", "Telephone"), ctx.company.get("phone"), "company")
    s.add("email", t(lang, "电子邮件", "E-mail"), ctx.company.get("email"), "company")
    s.add("emergency_phone", t(lang, "企业应急电话（24 小时）", "Emergency telephone (24 h)"),
          doc.require(ctx.company.get("emergency_phone"), "1.24小时应急电话"), "company")

    # 2 ------------------------------------------------------------------------------------------------
    s = S["hazards"]
    hazards = cls.get("hazards", [])
    if hazards:
        s.add("classification", t(lang, "GHS 危险性类别", "GHS classification"),
              [f"{_cls_name(h['hazard_class'], lang)}，{_cat(h['category'], lang)}"
               + (f"（{h['effect']}）" if h.get("effect") else "") for h in hazards], "computed")
    else:
        s.add("classification", t(lang, "GHS 危险性类别", "GHS classification"),
              t(lang, "根据 GB 30000 系列标准，不分类", "Not classified according to GB 30000"), "computed")
    s.add("pictograms", t(lang, "象形图", "Pictograms"), [p["code"] for p in cls.get("pictograms", [])], "computed")
    s.add("signal_word", t(lang, "信号词", "Signal word"),
          {"危险": t(lang, "危险", "Danger"), "警告": t(lang, "警告", "Warning")}.get(cls.get("signal_word") or "", "—"),
          "computed")
    s.add("hazard_statements", t(lang, "危险性说明", "Hazard statements"),
          [_stmt(ctx, c, doc) for c in cls.get("h_codes", [])], "computed")
    groups = {"2": ("prevention", "预防措施", "Prevention"), "3": ("response", "事故响应", "Response"),
              "4": ("storage", "安全储存", "Storage"), "5": ("disposal", "废弃处置", "Disposal")}
    for g, (key, zh, en) in groups.items():
        codes = [c for c in cls.get("p_codes", []) if c[1] == g]
        if codes:
            s.add(f"p_{key}", t(lang, f"防范说明 — {zh}", f"Precautionary statements — {en}"),
                  [_stmt(ctx, c, doc) for c in codes], "computed")
    unknown = cls.get("unknown_toxicity_pct") or {}
    if any(v > 10 for k, v in unknown.items() if k != "aquatic"):
        worst = max(v for k, v in unknown.items() if k != "aquatic")
        s.paragraphs.append(t(lang, f"混合物中 {worst:g}% 的组分急性毒性未知。",
                              f"{worst:g}% of the mixture consists of ingredient(s) of unknown acute toxicity."))

    # 3 ------------------------------------------------------------------------------------------------
    s = S["composition"]
    s.add("type", t(lang, "物质/混合物", "Substance/mixture"), t(lang, "混合物", "Mixture"), "product")
    rows = []
    for c in ctx.components:
        secret = bool(c.get("trade_secret"))
        name = (c.get("generic_name") or c["name"]) if secret else c["name"]
        if secret and not c.get("generic_name"):
            doc.warnings.append(f"商业机密组分 {c['name']} 未提供通用化学名，SDS 中暂用原名")
        rows.append([name, "—" if secret and c.get("withhold_cas") else (c.get("cas") or "—"),
                     disclose(c["pct"], trade_secret=secret, edges=ctx.band_edges),
                     "；".join(f"{_cls_name(h['hazard_class'], lang)} {_cat(h['category'], lang)}" for h in c["hazards"])])
    s.table("ingredients", [t(lang, "组分", "Component"), "CAS No.", t(lang, "含量", "Concentration"),
                            t(lang, "GHS 分类", "GHS classification")], rows)
    if any(c.get("trade_secret") for c in ctx.components):
        s.paragraphs.append(t(lang, "部分组分的确切含量为商业机密，按 GB/T 17519 以浓度范围表示。",
                              "Exact concentrations of some ingredients are withheld as trade secrets and given as ranges."))

    # 4 ------------------------------------------------------------------------------------------------
    s = S["first_aid"]
    corrosive = _has(cls, "skin_corrosion", ("1", "1A", "1B", "1C"))
    eye1 = _has(cls, "eye_damage", ("1",)) or corrosive
    s.add("inhalation", t(lang, "吸入", "Inhalation"), t(
        lang,
        "迅速脱离现场至空气新鲜处，保持呼吸道通畅。如呼吸困难，给输氧；如呼吸停止，立即进行人工呼吸。就医。"
        if (_has(cls, "acute_tox_inhal") or _has(cls, "resp_sensitizer") or _has(cls, "stot_se"))
        else "脱离现场至空气新鲜处。如有不适，就医。",
        "Remove to fresh air and keep at rest. Give oxygen if breathing is difficult; artificial respiration if "
        "breathing has stopped. Get medical attention." if (_has(cls, "acute_tox_inhal") or _has(cls, "stot_se"))
        else "Remove to fresh air. Get medical advice if you feel unwell."), "template")
    s.add("skin", t(lang, "皮肤接触", "Skin contact"), t(
        lang,
        "立即脱去污染的衣着，用大量流动清水冲洗至少 15 分钟。立即就医。" if corrosive else
        "脱去污染的衣着，用肥皂水和清水彻底冲洗皮肤。如出现刺激或皮疹，就医。",
        "Take off contaminated clothing immediately; rinse skin with plenty of water for at least 15 minutes. "
        "Get medical attention immediately." if corrosive else
        "Remove contaminated clothing; wash skin thoroughly with soap and water. Get medical advice if irritation "
        "or rash occurs."), "template")
    s.add("eyes", t(lang, "眼睛接触", "Eye contact"), t(
        lang,
        "立即提起眼睑，用大量流动清水或生理盐水彻底冲洗至少 15 分钟。立即就医。" if eye1 else
        "提起眼睑，用流动清水或生理盐水冲洗。如仍有刺激，就医。",
        "Rinse immediately with plenty of water for at least 15 minutes, holding eyelids open. Get medical attention "
        "immediately." if eye1 else "Rinse with water, holding eyelids open. Get medical advice if irritation persists."),
          "template")
    aspiration = _has(cls, "aspiration", ("1",))
    s.add("ingestion", t(lang, "食入", "Ingestion"), t(
        lang,
        "用水漱口，禁止催吐。立即就医。" if (aspiration or corrosive) else "用水漱口，不要催吐。如有不适，就医。",
        "Rinse mouth. Do NOT induce vomiting. Get medical attention immediately." if (aspiration or corrosive)
        else "Rinse mouth. Do not induce vomiting. Get medical advice if you feel unwell."), "template")
    s.add("notes_to_physician", t(lang, "对医生的特别提示", "Notes to physician"),
          t(lang, "对症处理。", "Treat symptomatically."), "template")

    # 5 ------------------------------------------------------------------------------------------------
    s = S["fire_fighting"]
    s.add("media", t(lang, "灭火剂", "Extinguishing media"), t(
        lang, "抗溶性泡沫、干粉、二氧化碳、砂土。" if flammable else "根据周围火情选用适当的灭火剂。",
        "Alcohol-resistant foam, dry chemical, carbon dioxide, sand." if flammable
        else "Use extinguishing media appropriate to the surrounding fire."), "template")
    if flammable:
        s.add("unsuitable_media", t(lang, "不宜使用的灭火剂", "Unsuitable media"),
              t(lang, "直流水。", "Direct water jet."), "template")
    s.add("special_hazards", t(lang, "特别危险性", "Special hazards"), t(
        lang,
        "其蒸气与空气可形成爆炸性混合物，遇明火、高热易燃烧爆炸。燃烧时产生一氧化碳、二氧化碳等有害气体。" if flammable
        else "燃烧时可能产生一氧化碳、二氧化碳等有害气体。",
        "Vapours may form explosive mixtures with air; flammable in the presence of heat or ignition sources. "
        "Combustion produces carbon monoxide and carbon dioxide." if flammable
        else "Combustion may produce carbon monoxide and carbon dioxide."), "template")
    s.add("advice", t(lang, "灭火注意事项及防护措施", "Advice for firefighters"), t(
        lang, "消防人员须佩戴自给式呼吸器，穿全身防火防毒服，在上风向灭火。尽可能将容器从火场移至空旷处，喷水保持火场容器冷却。",
        "Wear self-contained breathing apparatus and full protective clothing; fight fire from upwind. Move containers "
        "from the fire area if possible and cool them with water spray."), "template")

    # 6 ------------------------------------------------------------------------------------------------
    s = S["accidental_release"]
    s.add("personal", t(lang, "作业人员防护措施、防护装备和应急处置程序", "Personal precautions"), t(
        lang,
        "隔离泄漏污染区，限制出入。" + ("消除所有点火源。" if flammable else "") + "应急处理人员佩戴防护装备，避免接触皮肤和眼睛、吸入蒸气。",
        "Isolate the area and restrict access. " + ("Eliminate all ignition sources. " if flammable else "")
        + "Responders wear protective equipment; avoid contact with skin and eyes and inhalation of vapour."), "template")
    s.add("environmental", t(lang, "环境保护措施", "Environmental precautions"), t(
        lang, "防止泄漏物进入下水道、地表水和土壤。" + ("本品对水生生物有害，须防止释放到环境中。" if aquatic else ""),
        "Prevent entry into drains, surface water and soil." + (" Harmful to aquatic life: avoid release." if aquatic else "")),
          "template")
    s.add("cleanup", t(lang, "泄漏化学品的收容、清除方法及所使用的处置材料", "Methods for containment and clean-up"), t(
        lang,
        "小量泄漏：用砂土或其他不燃材料吸收，收集于密闭容器中。" + ("使用无火花工具。" if flammable else "")
        + "大量泄漏：构筑围堤收容，转移至专用收集器内，回收或运至废物处理场所处置。",
        "Small spills: absorb with sand or other non-combustible material and collect in closed containers. "
        + ("Use non-sparking tools. " if flammable else "")
        + "Large spills: dike, transfer to suitable containers for recovery or disposal."), "template")

    # 7 ------------------------------------------------------------------------------------------------
    s = S["handling_storage"]
    prevention = [_stmt(ctx, c, doc) for c in cls.get("p_codes", []) if c[1] == "2"]
    storage = [_stmt(ctx, c, doc) for c in cls.get("p_codes", []) if c[1] == "4"]
    s.add("handling", t(lang, "操作注意事项", "Precautions for safe handling"), (prevention or []) + [t(
        lang, "密闭操作，加强通风。操作人员须经专门培训，严格遵守操作规程。" + ("远离火种、热源，工作场所严禁吸烟；使用防爆型通风系统和设备，防止静电积聚。" if flammable else ""),
        "Use in a closed system with adequate ventilation; trained operators only. "
        + ("Keep away from ignition sources; no smoking; use explosion-proof equipment and prevent static discharge." if flammable else ""))],
          "template")
    s.add("storage", t(lang, "储存注意事项", "Conditions for safe storage"), (storage or []) + [t(
        lang, "储存于阴凉、通风的库房。保持容器密封。" + ("远离火种、热源，库温不宜超过 37 ℃。与氧化剂分开存放。" if flammable else "与氧化剂分开存放。"),
        "Store in a cool, well-ventilated place; keep container tightly closed. "
        + ("Keep away from heat and ignition sources; store below 37 °C; separate from oxidizers." if flammable
           else "Store separately from oxidizers."))], "template")

    # 8 ------------------------------------------------------------------------------------------------
    s = S["exposure_controls"]
    oel_rows = []
    for c in ctx.components:
        lim = ctx.oel.get(c.get("cas") or "")
        if lim:
            oel_rows.append([c.get("generic_name") if c.get("trade_secret") else c["name"], c.get("cas") or "—",
                             lim.get("mac") or "—", lim.get("pc_twa") or "—", lim.get("pc_stel") or "—"])
    if oel_rows:
        s.table("oel", [t(lang, "组分", "Component"), "CAS No.", "MAC (mg/m³)", "PC-TWA (mg/m³)", "PC-STEL (mg/m³)"],
                oel_rows, "computed")
        s.paragraphs.append(t(lang, "职业接触限值依据 GBZ 2.1-2019。", "Occupational exposure limits per GBZ 2.1-2019."))
    else:
        s.add("oel", t(lang, "职业接触限值", "Occupational exposure limits"),
              t(lang, "组分均未制定 GBZ 2.1 职业接触限值。", "No GBZ 2.1 limits for the ingredients."), "computed")
    inhal = _has(cls, "acute_tox_inhal") or _has(cls, "stot_se") or _has(cls, "resp_sensitizer") or flammable
    s.add("engineering", t(lang, "工程控制", "Engineering controls"),
          t(lang, "生产过程密闭，加强通风，提供安全淋浴和洗眼设备。", "Enclosed process, local exhaust ventilation; "
            "safety shower and eye-wash station."), "template")
    s.add("respiratory", t(lang, "呼吸系统防护", "Respiratory protection"), t(
        lang, "空气中浓度超标时，佩戴过滤式防毒面具（半面罩）。" if inhal else "一般不需要特殊防护。",
        "Wear a filter respirator (half mask) when airborne concentrations exceed limits." if inhal
        else "Not normally required."), "template")
    s.add("hands", t(lang, "手防护", "Hand protection"), t(
        lang, "戴防化学品手套（材质按溶剂体系选择，如丁腈橡胶）。", "Chemical-resistant gloves (e.g. nitrile; select for the solvent system)."),
          "template")
    s.add("eyes", t(lang, "眼睛防护", "Eye protection"), t(
        lang, "戴化学安全防护眼镜" + ("或面罩。" if eye1 else "。"), "Chemical safety goggles" + (" or face shield." if eye1 else ".")),
          "template")
    s.add("body", t(lang, "皮肤和身体防护", "Skin and body protection"),
          t(lang, "穿防静电工作服。" if flammable else "穿工作服。", "Antistatic work clothing." if flammable else "Work clothing."),
          "template")

    # 9 ------------------------------------------------------------------------------------------------
    s = S["physical_chemical"]
    rows = []
    for key, zh, en, unit in PROPERTIES:
        p = ctx.properties.get(key) or {}
        value = p.get("value")
        shown = nd if value in (None, "") else f"{value}{(' ' + unit) if unit and isinstance(value, (int, float)) else ''}"
        rows.append([t(lang, zh, en), shown, p.get("method") or "", p.get("source") or ("" if value is None else "manual")])
    s.table("properties", [t(lang, "项目", "Property"), t(lang, "数值", "Value"), t(lang, "方法", "Method"),
                           t(lang, "来源", "Source")], rows, "measured")
    if not (ctx.properties.get("flash_point_c") or {}).get("value") and flammable:
        doc.warnings.append("易燃液体分类结果存在，但第 9 节缺少闪点")

    # 10 -----------------------------------------------------------------------------------------------
    s = S["stability"]
    s.add("stability", t(lang, "稳定性", "Chemical stability"), t(lang, "正常储存和使用条件下稳定。",
                                                                  "Stable under normal storage and use conditions."), "template")
    s.add("conditions_to_avoid", t(lang, "应避免的条件", "Conditions to avoid"),
          t(lang, "明火、高热、静电。" if flammable else "高温。", "Heat, sparks, open flames, static." if flammable else "High temperature."),
          "template")
    s.add("incompatible", t(lang, "禁配物", "Incompatible materials"), t(lang, "强氧化剂、强酸、强碱。", "Strong oxidizers, strong acids and bases."),
          "template")
    s.add("decomposition", t(lang, "危险的分解产物", "Hazardous decomposition products"),
          t(lang, "一氧化碳、二氧化碳。", "Carbon monoxide, carbon dioxide."), "template")

    # 11 -----------------------------------------------------------------------------------------------
    s = S["toxicology"]
    health = [h for h in hazards if BY_CODE[h["hazard_class"]].kind == "health"]
    for tr in cls.get("trace", []):
        if tr["hazard_class"].startswith("acute_tox_") and "ate_mix" in (tr.get("numbers") or {}):
            n = tr["numbers"]
            s.add(f"ate_{tr['hazard_class']}", t(lang, f"{_cls_name(tr['hazard_class'], lang)} ATEmix",
                                                f"{_cls_name(tr['hazard_class'], lang)} ATEmix"),
                  f"{n['ate_mix']:.4g} {n['unit']}（{t(lang, '计算值', 'calculated')}）", "computed")
    s.add("classification", t(lang, "健康危害分类", "Health hazard classification"),
          [f"{_cls_name(h['hazard_class'], lang)}，{_cat(h['category'], lang)}" for h in health]
          or t(lang, "根据组分数据计算，健康危害不分类。", "Not classified for health hazards (calculation method)."),
          "computed")
    s.paragraphs.append(t(lang, "上述结果按 GB 30000 系列标准的混合物计算方法得出；未进行混合物整体毒理试验。",
                          "Results derived with the GB 30000 calculation method; the mixture as a whole was not tested."))

    # 12 -----------------------------------------------------------------------------------------------
    s = S["ecology"]
    env = [h for h in hazards if h["hazard_class"] in ("aquatic_acute", "aquatic_chronic", "ozone")]
    s.add("classification", t(lang, "环境危害分类", "Environmental classification"),
          [f"{_cls_name(h['hazard_class'], lang)}，{_cat(h['category'], lang)}" for h in env]
          or t(lang, "根据组分数据计算，不分类。", "Not classified (calculation method)."), "computed")
    if (unknown.get("aquatic") or 0) > 0:
        s.paragraphs.append(t(lang, f"混合物中 {unknown['aquatic']:g}% 的组分对水生环境的危害未知。",
                              f"{unknown['aquatic']:g}% of the mixture consists of ingredients of unknown hazards to "
                              "the aquatic environment."))
    for key, zh, en in (("persistence", "持久性和降解性", "Persistence and degradability"),
                        ("bioaccumulation", "潜在的生物累积性", "Bioaccumulative potential"),
                        ("mobility", "土壤中的迁移性", "Mobility in soil")):
        s.add(key, t(lang, zh, en), nd, "template")

    # 13 -----------------------------------------------------------------------------------------------
    s = S["disposal"]
    s.add("method", t(lang, "废弃化学品", "Waste treatment"), t(
        lang, "按照国家和地方有关法规处置，委托有资质的单位处理。" + ("禁止排入下水道和水体。" if aquatic else ""),
        "Dispose of according to national and local regulations through a licensed contractor."
        + (" Do not discharge to drains or water bodies." if aquatic else "")), "template")
    s.add("packaging", t(lang, "污染包装物", "Contaminated packaging"), t(lang, "将容器返还生产商或按国家和地方法规处置。",
                                                                        "Return to supplier or dispose of per regulations."), "template")

    # 14 -----------------------------------------------------------------------------------------------
    s = S["transport"]
    tr = cls.get("transport") or {}
    if tr.get("regulated"):
        name = tr.get("proper_shipping_name_en" if lang == "en-US" else "proper_shipping_name_zh") or ""
        if tr.get("technical_names"):
            name = f"{name}（{'，'.join(tr['technical_names'])}）"
        s.add("un_number", t(lang, "联合国危险货物编号（UN 号）", "UN number"), tr.get("un_number"), "computed")
        s.add("shipping_name", t(lang, "联合国运输名称", "UN proper shipping name"), name, "computed")
        s.add("class", t(lang, "联合国危险性类别", "Transport hazard class"),
              tr.get("transport_class") + (f"（{tr['subsidiary_risk']}）" if tr.get("subsidiary_risk") else ""), "computed")
        s.add("packing_group", t(lang, "包装类别", "Packing group"), tr.get("packing_group"), "computed")
        s.add("marine_pollutant", t(lang, "海洋污染物", "Marine pollutant"),
              t(lang, "是", "Yes") if tr.get("marine_pollutant") else t(lang, "否", "No"), "computed")
        doc.warnings.append("运输分类由系统推导，须由法规专员确认后发布（GB 6944 / GB 12268）")
    else:
        s.add("un_number", t(lang, "联合国危险货物编号（UN 号）", "UN number"),
              t(lang, "不受危险货物运输法规管制", "Not regulated for transport"), "computed")
    s.add("precautions", t(lang, "运输注意事项", "Special precautions"), t(
        lang, "运输时运输车辆应配备相应品种和数量的消防器材及泄漏应急处理设备，防止曝晒、雨淋、高温。",
        "Carry suitable fire-fighting and spill equipment; protect from sun, rain and heat."), "template")

    # 15 -----------------------------------------------------------------------------------------------
    s = S["regulatory"]
    base = [t(lang, z, e) for z, e in (
        ("《中华人民共和国安全生产法》", "Work Safety Law of the PRC"),
        ("《危险化学品安全管理条例》", "Regulations on the Safe Management of Hazardous Chemicals"),
        ("《危险化学品目录（2015 版）》", "Catalogue of Hazardous Chemicals (2015)"),
        ("GB 30000.2~29-2013《化学品分类和标签规范》", "GB 30000.2~29-2013 Rules for classification and labelling"),
        ("GB/T 16483-2008、GB/T 17519-2013（SDS）", "GB/T 16483-2008, GB/T 17519-2013 (SDS)"),
        ("GB 15258-2009《化学品安全标签编写规定》", "GB 15258-2009 Precautionary labelling"),
        ("GBZ 2.1-2019《工作场所有害因素职业接触限值》", "GBZ 2.1-2019 Occupational exposure limits"),
        ("GB 6944、GB 12268（危险货物分类与品名）", "GB 6944, GB 12268 (dangerous goods)"),
    )]
    s.add("regulations", t(lang, "适用法规", "Applicable regulations"), base, "template")
    if ctx.regulations:
        s.add("ingredient_listings", t(lang, "组分的法规管控信息", "Ingredient regulatory listings"), ctx.regulations,
              "computed")

    # 16 -----------------------------------------------------------------------------------------------
    s = S["other"]
    s.add("version", t(lang, "版本", "Version"), ctx.revision.get("version"), "product")
    s.add("revision_date", t(lang, "修订日期", "Revision date"), ctx.revision.get("date"), "product")
    s.add("changes", t(lang, "修订说明", "Revision notes"), ctx.revision.get("change_summary") or "—", "manual")
    s.add("basis", t(lang, "分类依据", "Classification basis"),
          f"GB 30000（{cls.get('rule_set_version')}）", "computed")
    s.paragraphs.append(t(lang, "本 SDS 的信息基于目前掌握的知识和数据，仅供安全处置本品参考，不构成任何质量保证。",
                          "The information in this SDS reflects current knowledge and is intended for safe handling only; "
                          "it does not constitute a warranty of properties."))
    for gap in cls.get("data_gaps", []):
        if gap.get("severity") == "blocking":
            doc.warnings.append(f"分类数据缺口（阻断发布）：{gap['message']}")
    # Language-neutral values the release gate compares across documents (方案 §5.5.10 checks 7-8).
    doc.meta["facts"] = {
        "pictograms": [p["code"] for p in cls.get("pictograms", [])], "signal_word": cls.get("signal_word"),
        "h_codes": list(cls.get("h_codes", [])), "p_codes": list(cls.get("p_codes", [])),
        "un_number": tr.get("un_number") if tr.get("regulated") else None,
        "properties": {k: v.get("value") for k, v in ctx.properties.items()
                       if isinstance(v, dict) and isinstance(v.get("value"), (int, float))},
    }
    return doc.to_dict()
