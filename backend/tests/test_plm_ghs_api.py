"""PLM GHS API: database lookups, raw-material explosion, persistence."""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'plm.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion, MaterialRow
    from app.db.models_regulatory import (
        MaterialCompositionRow,
        SubstanceClassificationRow,
        SubstanceRow,
        SubstanceTransportRow,
    )

    def sub(session, cas, zh, en, catalog=None):
        s = SubstanceRow(id=str(uuid.uuid4()), cas_no=cas, name_zh=zh, name_en=en, aliases=[],
                         hazardous_catalog_no=catalog)
        session.add(s)
        session.flush()
        return s

    def cls(session, s, hc, cat, h, source="cn_catalog", **kw):
        session.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=s.id, hazard_class=hc,
                                               category=cat, h_code=h, source=source, **kw))

    with default_session_factory()() as session:
        xylene = sub(session, "1330-20-7", "二甲苯", "xylene", 2289)
        for hc, cat, h in [("flammable_liquid", "3", "H226"), ("acute_tox_dermal", "4", "H312"),
                           ("acute_tox_inhal", "4", "H332"), ("skin_corrosion", "2", "H315")]:
            cls(session, xylene, hc, cat, h)
        hcho = sub(session, "50-00-0", "甲醛", "formaldehyde", 1173)
        cls(session, hcho, "carcinogenicity", "1B", "H350")
        cls(session, hcho, "acute_tox_oral", "3", "H301")
        # Supplier SDS outranks the catalogue for the same hazard class.
        cls(session, hcho, "acute_tox_oral", "4", "H302", source="supplier_sds")
        water = sub(session, "7732-18-5", "水", "water")
        ethanol = sub(session, "64-17-5", "乙醇", "ethanol", 2568)
        cls(session, ethanol, "flammable_liquid", "2", "H225")
        session.add(SubstanceTransportRow(id=str(uuid.uuid4()), un_number="1170", substance_id=ethanol.id,
                                          proper_shipping_name_zh="乙醇", proper_shipping_name_en="ETHANOL",
                                          transport_class="3", packing_group="II"))
        resin = MaterialRow(id="mat-resin", norm_key="urea-resin", name="UF resin", zh_name="脲醛树脂",
                            role="binder", origin="manual", availability="in_stock")
        session.add(resin)
        session.flush()
        session.add(MaterialCompositionRow(id=str(uuid.uuid4()), material_id=resin.id, substance_id=hcho.id,
                                           pct_min=0.1, pct_max=0.5, pct_typical=0.2))
        session.add(MaterialCompositionRow(id=str(uuid.uuid4()), material_id=resin.id, substance_id=water.id,
                                           pct_min=40, pct_max=50, pct_typical=45))
        session.add(MaterialRow(id="mat-blank", norm_key="blank", name="Mystery additive", role="additive",
                                origin="manual", availability="in_stock"))
        session.add(FormulationVersion(
            id="fv-1", lineage_id="lin-1", version=1, domain="coating", name="底漆 v1", snapshot={
                "ingredients": [{"name": "xylene", "zh_name": "二甲苯", "cas_no": "1330-20-7", "weight_pct": 40},
                                {"name": "water", "cas_no": "7732-18-5", "weight_pct": 60}]},
            created_at=datetime(2026, 10, 1)))
        session.commit()
    from app.main import app

    yield TestClient(app)
    get_settings.cache_clear()


def _cats(body, hazard_class):
    return sorted(h["category"] for h in body["hazards"] if h["hazard_class"] == hazard_class)


def test_classify_by_cas_uses_the_substance_database(api):
    r = api.post("/api/plm/ghs/classify", json={
        "components": [{"name": "二甲苯", "cas": "1330-20-7", "pct": 60}, {"name": "水", "cas": "7732-18-5", "pct": 40}],
        "properties": {"flash_point_c": 30, "initial_boiling_point_c": 140},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert _cats(body, "flammable_liquid") == ["3"] and _cats(body, "skin_corrosion") == ["2"]
    assert body["statements"]["H226"]["zh"] == ""  # statement texts come from the seeded table (empty here)
    assert body["rule_set_version"] == "gb30000-2013.v1" and body["rule_set_status"] == "draft"
    assert body["transport"]["un_number"] == "1993"
    assert body["unknown_toxicity_pct"] == {}  # water is an assessed, non-hazardous substance


def test_supplier_sds_outranks_catalogue(api):
    body = api.post("/api/plm/ghs/classify", json={
        "components": [{"name": "甲醛", "cas": "50-00-0", "pct": 100}], "include_transport": False}).json()
    assert _cats(body, "acute_tox_oral") == ["4"]  # supplier category 4 wins over catalogue category 3
    assert _cats(body, "carcinogenicity") == ["1B"]


def test_raw_material_explodes_to_substances_with_upper_bound(api):
    body = api.post("/api/plm/ghs/classify", json={
        "components": [{"name": "脲醛树脂", "material_id": "mat-resin", "pct": 40}, {"cas": "7732-18-5", "pct": 60}],
        "include_transport": False}).json()
    # Formaldehyde at the 0.5 % upper bound of 40 % resin = 0.2 % ≥ 0.1 % → carcinogen 1B.
    assert _cats(body, "carcinogenicity") == ["1B"]
    codes = {g["code"] for g in body["data_gaps"]}
    # Declared maxima cover 50.5 % of the resin; the rest is counted as unknown, not dropped.
    assert {"material_not_reviewed", "material_composition_incomplete"} <= codes
    assert "composition_not_100" not in codes
    assert body["unknown_toxicity_pct"]["oral"] == pytest.approx(40 * 0.495)
    typical = api.post("/api/plm/ghs/classify", json={
        "components": [{"name": "脲醛树脂", "material_id": "mat-resin", "pct": 40}, {"cas": "7732-18-5", "pct": 60}],
        "mode": "typical", "include_transport": False}).json()
    # Typical 0.2 % of 40 % = 0.08 % < 0.1 %; the undeclared 54.8 % of the resin counts as unknown.
    assert _cats(typical, "carcinogenicity") == []
    assert "material_composition_incomplete" in {g["code"] for g in typical["data_gaps"]}


def test_unknown_inputs_are_reported_not_ignored(api):
    body = api.post("/api/plm/ghs/classify", json={"components": [
        {"name": "神秘助剂", "material_id": "mat-blank", "pct": 20},
        {"name": "未入库物质", "cas": "123-45-6", "pct": 5},
        {"name": "水", "cas": "7732-18-5", "pct": 75}], "include_transport": False}).json()
    gaps = {g["code"]: g["severity"] for g in body["data_gaps"]}
    assert gaps["material_without_composition"] == "blocking"
    assert "substance_not_found" in gaps
    assert body["unknown_toxicity_pct"]["oral"] == 25


def test_inline_hazards_are_used_as_given(api):
    body = api.post("/api/plm/ghs/classify", json={"components": [
        {"name": "某酸", "pct": 2, "hazards": [{"hazard_class": "skin_corrosion", "category": "1A", "non_additive": True}]},
        {"cas": "7732-18-5", "pct": 98}], "include_transport": False}).json()
    assert _cats(body, "skin_corrosion") == ["1"] and body["signal_word"] == "危险"


def test_single_substance_product_uses_its_own_un_entry(api):
    body = api.post("/api/plm/ghs/classify", json={"components": [{"cas": "64-17-5", "pct": 100}],
                                                   "properties": {"flash_point_c": 13, "initial_boiling_point_c": 78}}).json()
    assert body["transport"]["un_number"] == "1170"


def test_formulation_version_is_classified_and_persisted(api):
    from app.db.database import default_session_factory
    from app.db.models_plm import ClassificationResultRow

    r = api.post("/api/plm/ghs/classify/formulation-versions/fv-1",
                 json={"properties": {"flash_point_c": 35, "initial_boiling_point_c": 140}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert _cats(body, "flammable_liquid") == ["3"]
    with default_session_factory()() as session:
        row = session.get(ClassificationResultRow, body["classification_id"])
        assert row.formulation_version_id == "fv-1" and row.inputs_hash == body["inputs_hash"]
        assert row.result["h_codes"] == body["h_codes"] and row.trace
    assert api.post("/api/plm/ghs/classify/formulation-versions/nope", json={}).status_code == 404


def test_substance_search_and_ruleset(api):
    by_cas = api.get("/api/plm/substances", params={"q": "50-00-0"}).json()
    assert by_cas[0]["name_zh"] == "甲醛"
    assert {(c["hazard_class"], c["source"]) for c in by_cas[0]["classifications"]} == {
        ("acute_tox_oral", "supplier_sds"), ("carcinogenicity", "cn_catalog")}
    by_name = api.get("/api/plm/substances", params={"q": "二甲"}).json()
    assert [s["cas_no"] for s in by_name] == ["1330-20-7"]
    rs = api.get("/api/plm/ghs/ruleset").json()
    assert rs["version"] == "gb30000-2013.v1" and rs["parameters"]["aquatic"]["limit_pct"] == 25.0


def test_validation(api):
    assert api.post("/api/plm/ghs/classify", json={"components": []}).status_code == 422
    assert api.post("/api/plm/ghs/classify", json={"components": [{"pct": 120}]}).status_code == 422
