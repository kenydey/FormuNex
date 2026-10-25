"""G2 multi-regulation classification / documents / gate + G3 aqueous suppression."""
from __future__ import annotations

import uuid

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.models import FormulationVersion
from app.services.plm import company as company_svc
from app.services.plm.documents import service as docs
from app.services.plm.ghs import aqueous as aqueous_mod
from app.services.plm.ghs.engine import classify
from app.services.plm.ghs.model import Component, ComponentHazard, MixtureProperties
from app.services.plm.ghs.regulations import (
    DEFAULT_REGULATION,
    regulation_codes,
    ruleset_for,
    standard_for,
    validate_regulations,
)
from app.services.plm.ghs.ruleset import load_ruleset
from app.services.plm.ghs.service import formulation_version_items
from app.services.plm.release.gate import Check, _check_classification, check_ruleset_signoff


@pytest.fixture()
def sf(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/g2.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()
    get_settings.cache_clear()


def _comp(name, pct, hazards=(), cas=None):
    return Component(name=name, pct=pct, cas=cas,
                     hazards=[ComponentHazard(h, c) for h, c in hazards])


# --------------------------------------------------------------------------- registry


def test_registry_codes_rulesets_standards():
    assert regulation_codes() == ["CN", "EU", "US"]
    assert ruleset_for("CN") == "gb30000-2013.v1"
    assert ruleset_for("EU") == "clp-1272-2008.v1"
    assert ruleset_for("US") == "osha-hazcom-2012.v1"
    assert standard_for("CN") == "GB 30000"
    assert DEFAULT_REGULATION == "CN"
    for code in regulation_codes():
        rs = load_ruleset(ruleset_for(code))
        assert rs.version == ruleset_for(code)
        assert rs.status == "draft"  # EHS sign-off pending for all three


def test_validate_regulations_dedupes_and_rejects():
    assert validate_regulations(["EU", "CN", "EU"]) == ["EU", "CN"]
    assert validate_regulations([]) == []
    with pytest.raises(ValueError, match="未知适用法规"):
        validate_regulations(["JP"])
    with pytest.raises(ValueError, match="必须是列表"):
        validate_regulations("CN")


# --------------------------------------------------------------------------- engine differences


def test_osha_skips_environmental_classes():
    aq = _comp("AqTox", 30.0, [("aquatic_acute", "1")])
    oz = _comp("Ozone", 1.0, [("ozone", "1")])
    inert = _comp("Inert", 69.0)
    for reg, expect_aq, expect_oz in (("CN", True, True), ("EU", True, True), ("US", False, False)):
        r = classify([aq, oz, inert], MixtureProperties(), ruleset=ruleset_for(reg), include_transport=False)
        classes = {(h.hazard_class, h.category) for h in r.hazards}
        assert ("aquatic_acute", "1") in classes if expect_aq else ("aquatic_acute", "1") not in classes
        assert ("ozone", "1") in classes if expect_oz else ("ozone", "1") not in classes


def test_clp_has_no_flammable_cat4():
    flam = _comp("Solvent", 50.0, [("flammable_liquid", "3")])
    inert = _comp("Inert", 50.0)
    props = MixtureProperties(flash_point_c=70.0)
    for reg, expect in (("CN", "4"), ("EU", None), ("US", "4")):
        r = classify([flam, inert], props, ruleset=ruleset_for(reg), include_transport=False)
        got = [h.category for h in r.hazards if h.hazard_class == "flammable_liquid"]
        assert got == ([expect] if expect else [])


# --------------------------------------------------------------------------- G3 aqueous


def _water(pct):
    return _comp("Water", pct, cas="7732-18-5")


def _oxidizer(pct):
    return _comp("Oxidizer", pct, [("oxidizing_liquid", "1")])


def test_is_aqueous_requires_strictly_largest():
    assert aqueous_mod.is_aqueous([_water(60.0), _oxidizer(30.0), _comp("Inert", 10.0)])
    assert not aqueous_mod.is_aqueous([_water(50.0), _oxidizer(50.0)])  # tie: conservative
    assert not aqueous_mod.is_aqueous([_water(40.0), _oxidizer(60.0)])
    assert not aqueous_mod.is_aqueous([_oxidizer(100.0)])
    assert aqueous_mod.is_aqueous([_comp("去离子水", 60.0), _oxidizer(40.0)])  # name fallback


def _screening_gaps(result):
    return [g for g in result.data_gaps if g.code.startswith("physical_test_required:")]


def test_aqueous_suppresses_screening_gaps_only_when_rule_applies():
    # Water-dominant, no flash point: oxidizer screening gap suppressed, with trace.
    r = classify([_water(60.0), _oxidizer(30.0), _comp("Inert", 10.0)],
                 MixtureProperties(), include_transport=False)
    assert _screening_gaps(r) == []
    assert any(t.hazard_class == "aqueous_suppression" for t in r.trace)
    # Not water-dominant: gap stands.
    r = classify([_water(40.0), _oxidizer(60.0)], MixtureProperties(), include_transport=False)
    assert [g.hazard_class for g in _screening_gaps(r)] == ["oxidizing_liquid"]
    # Tie: gap stands.
    r = classify([_water(50.0), _oxidizer(50.0)], MixtureProperties(), include_transport=False)
    assert [g.hazard_class for g in _screening_gaps(r)] == ["oxidizing_liquid"]
    # Measured flash point below 60 °C: gap stands AND the measured
    # flammable outcome stands (measured outcomes are never suppressed).
    flam = _comp("Solvent", 10.0, [("flammable_liquid", "3")])
    r = classify([_water(60.0), _oxidizer(20.0), flam, _comp("Inert", 10.0)],
                 MixtureProperties(flash_point_c=30.0), include_transport=False)
    assert [g.hazard_class for g in _screening_gaps(r)] == ["oxidizing_liquid"]
    assert [h.category for h in r.hazards if h.hazard_class == "flammable_liquid"] == ["3"]
    # Flash point at/above 60 °C: gap suppressed again.
    r = classify([_water(60.0), _oxidizer(30.0), _comp("Inert", 10.0)],
                 MixtureProperties(flash_point_c=70.0), include_transport=False)
    assert _screening_gaps(r) == []


# --------------------------------------------------------------------------- company config


def test_company_regulations_round_trip_and_validation(sf):
    with sf() as s:
        row = company_svc.create_company(s, {"name": "测试公司", "emergency_phone": "010-123",
                                             "applicable_regulations": ["EU", "CN", "EU"]})
        assert company_svc.company_dict(row)["applicable_regulations"] == ["EU", "CN"]
        row = company_svc.update_company(s, row.id, {"applicable_regulations": ["US"]})
        assert company_svc.company_dict(row)["applicable_regulations"] == ["US"]
        with pytest.raises(ValueError, match="未知适用法规"):
            company_svc.create_company(s, {"name": "坏公司", "emergency_phone": "010-123",
                                           "applicable_regulations": ["XX"]})
        with pytest.raises(ValueError, match="未知适用法规"):
            company_svc.update_company(s, row.id, {"applicable_regulations": ["XX"]})
        plain = company_svc.create_company(s, {"name": "默认公司", "emergency_phone": "010-123"})
        assert company_svc.company_dict(plain)["applicable_regulations"] == []


# --------------------------------------------------------------------------- multi-regulation documents + gate


def _revision_with_formulation(s):
    prod = docs.create_product(s, code="P-G2", name_zh="G2测试产品")
    fv = FormulationVersion(id=str(uuid.uuid4()), lineage_id=str(uuid.uuid4()), version=1,
                            domain="anticorrosion_coating",
                            snapshot={"ingredients": [{"name": "Epoxy", "weight_pct": 60.0},
                                                      {"name": "Water", "weight_pct": 40.0,
                                                       "cas_no": "7732-18-5"}]})
    s.add(fv)
    s.flush()
    rev = docs.create_revision(s, prod.id, formulation_version_id=fv.id)
    s.commit()
    return rev.id, fv.id


def test_generate_documents_one_version_per_regulation(sf):
    with sf() as s:
        revision_id, _fv = _revision_with_formulation(s)
        rows = docs.generate_documents(s, revision_id, "sds", lang="zh-CN", payload={},
                                       created_by="u1", regulations=["CN", "EU"])
        s.commit()
        assert [(r.regulation, r.version_no) for r in rows] == [("CN", 1), ("EU", 1)]
        assert rows[0].content["meta"]["classification_id"] != rows[1].content["meta"]["classification_id"]
        assert rows[0].content["meta"]["regulation"] == "CN"
        assert rows[1].content["meta"]["regulation"] == "EU"
        # A second CN generation increments only the CN series.
        again = docs.generate_document(s, revision_id, "sds", lang="zh-CN", payload={},
                                       created_by="u1", regulation="CN")
        assert (again.regulation, again.version_no) == ("CN", 2)
        assert again.supersedes_id == rows[0].id
        # EU series untouched by the CN regeneration.
        eu_again = docs.generate_document(s, revision_id, "sds", lang="zh-CN", payload={},
                                          created_by="u1", regulation="EU")
        assert (eu_again.regulation, eu_again.version_no) == ("EU", 2)


def test_generate_documents_rejects_empty_unknown_and_non_sds(sf):
    with sf() as s:
        revision_id, _fv = _revision_with_formulation(s)
        with pytest.raises(docs.DocumentError, match="至少选择一个适用法规"):
            docs.generate_documents(s, revision_id, "sds", regulations=[])
        with pytest.raises(docs.DocumentError, match="未知适用法规"):
            docs.generate_documents(s, revision_id, "sds", regulations=["XX"])
        with pytest.raises(docs.DocumentError, match="仅适用于 SDS / 标签"):
            docs.generate_documents(s, revision_id, "tds", regulations=["CN"])


def test_gate_signoff_keys_are_per_regulation():
    c = Check("classification_current", "G5", "分类结果基于当前规则集与法规数据")
    check_ruleset_signoff(c, load_ruleset("gb30000-2013.v1"))
    assert any(i["key"] == "ruleset_status" and "待 EHS 签核" in i["message"] for i in c.items)
    c2 = Check("classification_current", "G5", "分类结果基于当前规则集与法规数据")
    check_ruleset_signoff(c2, load_ruleset("clp-1272-2008.v1"), key_suffix=":EU")
    assert any(i["key"] == "ruleset_status:EU" and "待 EHS 签核" in i["message"] for i in c2.items)


def test_gate_classification_checks_each_regulation_without_mixed_false_positive(sf):
    with sf() as s:
        revision_id, fv_id = _revision_with_formulation(s)
        rows = docs.generate_documents(s, revision_id, "sds", lang="zh-CN", payload={},
                                       created_by="u1", regulations=["CN", "EU"])
        s.commit()
        c = Check("classification_current", "G5", "分类结果基于当前规则集与法规数据")
        items = formulation_version_items(s, fv_id)
        _check_classification(s, c, rows, items)
        keys = [i["key"] for i in c.items]
        # Both draft rule sets block their own regulation's versions...
        assert "ruleset_status" in keys
        assert "ruleset_status:EU" in keys
        # ...but different inputs_hashes across regulations are NOT "mixed":
        # each regulation legitimately has its own classification.
        assert "mixed" not in keys
        assert c.evidence["rule_set_versions"] == {"CN": "gb30000-2013.v1", "EU": "clp-1272-2008.v1"}
