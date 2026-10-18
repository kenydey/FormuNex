"""Tests for substrate-aware DOE levers and research query injection."""
import pytest

from app.domain.knowledge import baseline_formulation, offline_recommend_fallback
from app.domain.levers import substrate_default_levers
from app.domain.project_spec import levers_to_doe_factors, resolve_levers
from app.domain.research_query import build_research_query
from app.domain.schemas import ProductDomain, Requirement, Substrate
from app.pipeline import reconstruct


def _mg_req() -> Requirement:
    return Requirement(
        domain=ProductDomain.surface_treatment,
        substrate=Substrate.magnesium_alloy,
        product_type="镁合金表面钝化剂",
        salt_spray_hours=128,
    )


def test_mg_alloy_doe_factors_use_gl_and_process():
    req = _mg_req()
    levers = resolve_levers(req)
    names = [lev.name for lev in levers]
    assert "Phosphoric acid" not in names
    assert "Hexafluorozirconic acid" in names
    assert "immersion_time_min" in names
    assert "bath_temperature_c" in names
    h2zrf = next(l for l in levers if l.name == "Hexafluorozirconic acid")
    assert h2zrf.unit == "g/L"
    factors = levers_to_doe_factors(levers)
    assert len(factors) >= 5


def test_steel_phosphate_unchanged():
    req = Requirement(domain=ProductDomain.surface_treatment, substrate=Substrate.carbon_steel)
    levers = resolve_levers(req)
    names = [lev.name for lev in levers]
    assert "Phosphoric acid" in names
    assert "immersion_time_min" not in names


def test_formulation_from_factors_respects_substrate():
    req = _mg_req()
    form = reconstruct.formulation_from_factors(
        req,
        {
            "Hexafluorozirconic acid": 2.0,  # 2 g/L → 0.2 wt%
            "(3-Aminopropyl)triethoxysilane (APTES)": 1.0,
            "Cerium nitrate": 0.5,
            "immersion_time_min": 120.0,
        },
    )
    zr = next(i for i in form.ingredients if i.name == "Hexafluorozirconic acid")
    assert zr.weight_pct == pytest.approx(0.2, abs=0.01)
    assert "Phosphoric acid" not in [i.name for i in form.ingredients]


def test_offline_recommend_fallback_perturbs_mg_active_not_phosphoric():
    req = _mg_req()
    variants = offline_recommend_fallback(req, n=3)
    assert len(variants) >= 2
    base = baseline_formulation(req)
    assert all("Phosphoric acid" not in [i.name for i in v.ingredients] for v in variants)
    assert variants[0].ingredients[0].name == base.ingredients[0].name


def test_build_research_query_injects_magnesium_terms():
    req = _mg_req()
    q = build_research_query("passivation coating", req)
    assert "passivation coating" in q
    assert "magnesium" in q.lower()
    assert "镁合金" in q or "AZ91" in q


def test_substrate_default_levers_only_surface_treatment():
    req = Requirement(domain=ProductDomain.degreaser, substrate=Substrate.magnesium_alloy)
    assert substrate_default_levers(req) is None


def test_2_11_new_builtin_substrates_use_family_levers():
    """补齐计划 2-11：A356/ADC12 走轻金属杠杆，冷轧钢走钢组杠杆。"""
    for sub in (Substrate.a356, Substrate.adc12):
        req = Requirement(domain=ProductDomain.surface_treatment, substrate=sub)
        names = [lev.name for lev in resolve_levers(req)]
        assert "Hexafluorozirconic acid" in names, sub
        assert "Phosphoric acid" not in names, sub
    req = Requirement(domain=ProductDomain.surface_treatment, substrate=Substrate.cold_rolled_steel)
    names = [lev.name for lev in resolve_levers(req)]
    assert "Phosphoric acid" in names
    assert "Hexafluorozirconic acid" not in names


def test_2_11_requirement_accepts_configured_substrate(monkeypatch):
    """补齐计划 2-11：FORMUNEX_EXTRA_SUBSTRATES 配置的基材可进 Requirement，原字符串流通。"""
    import json

    monkeypatch.setenv(
        "FORMUNEX_EXTRA_SUBSTRATES",
        json.dumps([{"value": "titanium", "label_zh": "钛合金", "label_en": "Titanium alloy"}]),
    )
    req = Requirement(domain=ProductDomain.anticorrosion_coating, substrate="titanium")
    assert req.substrate == "titanium"
    assert req.headline().endswith("on titanium")
    # 配置基材无已知工艺家族 → 无基材专属杠杆（不报错、不编造）。
    st_req = Requirement(domain=ProductDomain.surface_treatment, substrate="titanium")
    assert substrate_default_levers(st_req) is None


def test_2_11_unknown_substrate_rejected():
    """补齐计划 2-11：拼错的基材 fail-closed，不静默成别的基材。"""
    with pytest.raises(Exception, match="未知基材"):
        Requirement(domain=ProductDomain.anticorrosion_coating, substrate="titanuim")


def test_2_11_meta_substrates_endpoint(monkeypatch):
    """补齐计划 2-11：GET /api/meta/substrates 返回内置 + 配置扩展。"""
    import json

    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv(
        "FORMUNEX_EXTRA_SUBSTRATES",
        json.dumps([{"value": "titanium", "label_zh": "钛合金", "label_en": "Titanium alloy"}]),
    )
    client = TestClient(app)
    resp = client.get("/api/meta/substrates")
    assert resp.status_code == 200
    values = [o["value"] for o in resp.json()]
    assert {"a356", "adc12", "cold_rolled_steel", "titanium"} <= set(values)
    # default-levers 接受配置基材（无专属杠杆 → 通用杠杆，不 422/500）。
    resp2 = client.get("/api/meta/default-levers", params={"domain": "surface_treatment", "substrate": "titanium"})
    assert resp2.status_code == 200
    # 未知基材 → 400。
    resp3 = client.get("/api/meta/default-levers", params={"domain": "surface_treatment", "substrate": "titanuim"})
    assert resp3.status_code == 400
