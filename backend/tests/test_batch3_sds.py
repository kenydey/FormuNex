"""Batch 3: regressions for P1-C8/C9/C10, P2-C10, P2-C13, label regulation."""
from __future__ import annotations

from app.services.plm.documents.label_builder import LabelContext, build_label, check_label_size
from app.services.plm.documents.sds_builder import SdsContext, build_sds
from app.services.plm.documents.service import _components
from app.services.plm.documents.tds_builder import TdsContext, build_tds
from app.services.plm.ghs.engine import classify
from app.services.plm.ghs.model import Component, ComponentHazard, MixtureProperties
from app.services.plm.transport import derive_transport

COMPANY = {"name": "示例化工", "address": "上海市示例路 1 号", "phone": "021-0000", "emergency_phone": "021-0001"}
PRODUCT = {"name_zh": "FX-100", "name_en": "FX-100", "code": "FX-100"}


def _classification():
    comps = [Component("二甲苯", 25, "1330-20-7", hazards=[ComponentHazard("flammable_liquid", "3", "H226")])]
    r = classify(comps, MixtureProperties(flash_point_c=27, initial_boiling_point_c=138)).to_dict()
    r["statements"] = {c: {"zh": f"{c}中文", "en": ""} for c in r["h_codes"] + r["p_codes"]}
    return r


def _sds(**kw):
    cls = kw.pop("cls", None) or _classification()
    return build_sds(SdsContext(product=PRODUCT, company=COMPANY, classification=cls,
                                statements=cls["statements"],
                                components=kw.pop("components", cls["disclosure"]),
                                revision={"version": "A.1", "date": "2026-10-16"}, **kw))


def _section(doc, key):
    return next(s for s in doc["sections"] if s["key"] == key)


def _table(section, key):
    return next(t for t in section.get("tables", []) if t["key"] == key)["rows"]


def _warnings(doc):
    return doc["validation"]["warnings"]


def test_confidential_component_hides_cas_and_name():
    comps = _components([{"name": "密组分", "cas": "111-11-1", "pct": 5, "hazards": []}],
                        {"111-11-1": "通用名 A"})
    assert comps[0]["trade_secret"] and comps[0]["withhold_cas"] and comps[0]["generic_name"] == "通用名 A"
    doc = _sds(components=comps)
    row = _table(_section(doc, "composition"), "ingredients")[0]
    assert row[0] == "通用名 A" and row[1] == "—"
    # Builder-level degradation for a secret without a generic name: no leak.
    doc2 = _sds(components=[{"name": "密组分", "cas": "111-11-1", "pct": 5, "hazards": [],
                             "trade_secret": True, "generic_name": None, "withhold_cas": True}])
    row2 = _table(_section(doc2, "composition"), "ingredients")[0]
    assert row2[0] == "—" and row2[1] == "—"
    assert any("未提供通用化学名" in w for w in _warnings(doc2))


def test_eu_sds_has_no_gb_basis_text():
    cls = _classification()
    cls["hazards"] = []
    doc = _sds(cls=cls, regulation="EU", regulation_standard="EC 1272/2008 (CLP)",
               oel={"1330-20-7": {"mac": "100", "pc_twa": "50", "pc_stel": "100"}})
    assert doc["header"]["standard"] == "EC 1272/2008 (CLP)"
    text = str(doc)
    assert "GB 30000" not in text and "GBZ" not in text and "GB/T 16483" not in text
    assert "EC 1272/2008 (CLP)" in text
    oel = _section(doc, "exposure_controls")
    assert "无对应辖区职业接触限值数据" in str(oel)
    assert not any(t["key"] == "oel" for t in oel.get("tables", []))


def test_cn_sds_keeps_gb_text_and_oel_table():
    doc = _sds(oel={"1330-20-7": {"mac": "100", "pc_twa": "50", "pc_stel": "100"}})
    assert doc["header"]["standard"] == "GB/T 16483-2008, GB/T 17519-2013"
    assert any(t["key"] == "oel" for t in _section(doc, "exposure_controls").get("tables", []))


def _flammable_result(phys_state):
    # Unit-level: derive_transport's NOS fallback must not see a liquid
    # entry for gas/aerosol/paste, regardless of what the engine does.
    from types import SimpleNamespace

    from app.services.plm.ghs.model import HazardOutcome

    result = SimpleNamespace(hazards=[HazardOutcome("flammable_liquid", "2", ["H225"], "calculation")])
    props = MixtureProperties(phys_state=phys_state, flash_point_c=10)
    return result, props, [Component("溶剂", 50, "111-11-1")]


def test_gas_and_paste_get_no_liquid_nos_entry():
    for state in ("gas", "aerosol", "paste"):
        result, props, comps = _flammable_result(state)
        tr = derive_transport(result, props, comps)
        assert tr["un_number"] is None and "无法自动选择" in tr["basis"]
    result, props, comps = _flammable_result("liquid")
    assert derive_transport(result, props, comps)["un_number"] == "1993"


def test_transport_mismatch_note_becomes_sds_warning():
    cls = _classification()
    cls["transport"] = {"regulated": True, "un_number": "1993", "proper_shipping_name_zh": "易燃液体",
                        "proper_shipping_name_en": "FLAMMABLE LIQUID", "transport_class": "3",
                        "packing_group": "II", "marine_pollutant": False,
                        "notes": ["单一物质产品，使用该物质的 GB 12268 品名条目",
                                  "品名表条目类别 8 与分类推导的 3 不一致，需人工确认"]}
    doc = _sds(cls=cls)
    assert any("不一致，需人工确认" in w for w in _warnings(doc))
    assert not any("单一物质产品" in w for w in _warnings(doc))


def test_tds_skips_keyless_property_rows():
    doc = build_tds(TdsContext(product={**PRODUCT, "description": "底漆"}, company=COMPANY,
                               properties=[{"value": 1.2}, {"label": "黏度", "value": 3, "unit": "mPa·s"}]))
    rows = _table(next(s for s in doc["sections"] if s["key"] == "typical_properties"), "properties")
    assert len(rows) == 1 and rows[0][0] == "黏度"
    assert any("已跳过" in w for w in _warnings(doc))


def test_eu_label_gets_no_gb15258_size_verdict():
    check = check_label_size(5, 100, 150, regulation="EU")
    assert check["required_mm"] is None and "GB 15258" in check["note"]
    doc = build_label(LabelContext(product=PRODUCT, company=COMPANY, classification=_classification(),
                                   container_volume_l=5, size_mm=(100, 150), regulation="EU"))
    assert any("GB 15258" in w and "不适用" in w for w in _warnings(doc))
