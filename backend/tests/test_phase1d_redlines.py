"""补齐计划 Phase 1D (1-11~1-14, 1-16)：红线闭环 + 预置合规清单。

覆盖：共享红线检查（物质 / 清单引用 / VOC / 象形图 / 信号词）、
保存服务端复核（1-12/1-13，绕过前端直接调 API 也拦截）、推荐过滤（1-11）、
替换标注降权（1-11）、门禁 GHS 约束（1-14）、定型携带 GHS 字段（1-14）、
预置清单数据与端点（1-16）。
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import (
    ObjectiveSpec,
    ProductDomain,
    RecommendedFormula,
    RecommendedFormulaComponent,
    Requirement,
)

CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'phase1d.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import ProjectRow

    def _project(spec_contract: dict) -> str:
        pid = f"proj-{uuid.uuid4().hex[:8]}"
        with default_session_factory()() as s:
            s.add(ProjectRow(id=pid, title="T", domain="coating",
                             payload={"workspace": {"spec_contract": spec_contract}}))
            s.commit()
        return pid

    from app.main import app

    yield {"client": TestClient(app), "project": _project}
    get_settings.cache_clear()


def _formulation(ingredients: list[dict]) -> dict:
    return {"name": "FX-T", "domain": "anticorrosion_coating", "ingredients": ingredients}


_XYLENE = {"name": "二甲苯", "cas_no": "1330-20-7", "role": "solvent", "weight_pct": 30}
_WATER = {"name": "水", "cas_no": "7732-18-5", "role": "solvent", "weight_pct": 70}


# ── 共享红线检查（纯函数） ──────────────────────────────────────────────────

def test_voc_estimate_counts_solvents_excludes_water():
    from app.services.project_redlines import estimate_voc_g_l

    voc, contributors, _d = estimate_voc_g_l([_XYLENE, _WATER])
    assert voc == pytest.approx(300.0)
    assert contributors == ["二甲苯"]
    voc2, _, _ = estimate_voc_g_l([_XYLENE, _WATER], density_g_cm3=1.2)
    assert voc2 == pytest.approx(360.0)


def test_voc_estimate_no_solvents_is_zero():
    from app.services.project_redlines import estimate_voc_g_l

    voc, contributors, _ = estimate_voc_g_l(
        [{"name": "滑石粉", "role": "pigment", "weight_pct": 40}])
    assert voc == 0.0 and contributors == []


def test_check_redlines_direct_cas_block_and_under_cap():
    from app.services.project_redlines import GhsConstraints, check_redlines

    c = GhsConstraints(substances=[{"cas": "1330-20-7", "max_pct": 25, "reason": "客户禁用"}])
    items = [{"name": "二甲苯", "cas": "1330-20-7", "pct": 30}]
    hits = check_redlines(items, c)
    assert len(hits) == 1 and hits[0].code == "substance" and hits[0].severity == "block"
    assert "25" in hits[0].message
    assert check_redlines([{"name": "二甲苯", "cas": "1330-20-7", "pct": 20}], c) == []


def test_check_redlines_list_reference_class_sum():
    from app.services.project_redlines import GhsConstraints, check_redlines

    # PFOS 类（NP-01）：两盐合计 0.6% > 0.5% 上限 → 命中；单项均未超但合计超。
    c = GhsConstraints(substances=[{"list_id": "new_pollutants_2023", "list_entry_id": "NP-01", "max_pct": 0.5}])
    items = [{"name": "全氟辛烷磺酸", "cas": "1763-23-1", "pct": 0.3},
             {"name": "全氟辛烷磺酸钾", "cas": "2795-39-3", "pct": 0.3}]
    hits = check_redlines(items, c)
    assert len(hits) == 1 and "PFOS" in hits[0].message and "合计" in hits[0].message
    assert check_redlines(items, GhsConstraints(substances=[
        {"list_id": "new_pollutants_2023", "list_entry_id": "NP-01", "max_pct": 1.0}])) == []


def test_check_redlines_unknown_list_ref_fail_open_and_no_cas_skipped():
    from app.services.project_redlines import GhsConstraints, check_redlines

    c = GhsConstraints(substances=[{"list_id": "nope", "list_entry_id": "XX", "max_pct": 0}])
    assert check_redlines([{"name": "X", "cas": "1-2-3", "pct": 50}], c) == []
    # NP-13 抗生素类无 CAS：无法按 CAS 匹配，跳过（不误报）。
    c2 = GhsConstraints(substances=[{"list_id": "new_pollutants_2023", "list_entry_id": "NP-13", "max_pct": 0}])
    assert check_redlines([{"name": "青霉素", "cas": "61-33-6", "pct": 1}], c2) == []


def test_check_redlines_voc_authoritative_block_vs_estimate_warn():
    from app.services.project_redlines import GhsConstraints, check_redlines

    c = GhsConstraints(voc_limit_g_l=420)
    hits = check_redlines([], c, voc_gpl=500)
    assert len(hits) == 1 and hits[0].code == "voc" and hits[0].severity == "block"
    assert not hits[0].voc_estimated
    c2 = GhsConstraints(voc_limit_g_l=100)
    hits2 = check_redlines([_XYLENE, _WATER], c2)
    assert len(hits2) == 1 and hits2[0].code == "voc" and hits2[0].severity == "warn"
    assert hits2[0].voc_estimated


def test_check_redlines_pictogram_and_signal_word():
    from app.services.project_redlines import GhsConstraints, check_redlines

    c = GhsConstraints(forbidden_pictograms=["GHS02"], forbidden_signal_word="危险")
    cls = {"pictograms": [{"code": "GHS02"}], "signal_word": "危险"}
    hits = check_redlines([], c, classification=cls)
    assert {h.code for h in hits} == {"pictogram", "signal_word"}
    assert all(h.severity == "block" for h in hits)


def test_constraints_from_spec_contract():
    from app.services.project_redlines import constraints_from_spec_contract

    c = constraints_from_spec_contract({
        "red_lines": [{"cas": "1-2-3", "max_pct": 0}],
        "voc_limit_g_l": 420,
        "forbidden_pictograms": ["GHS02"],
        "forbidden_signal_word": "危险",
    })
    assert c.voc_limit_g_l == 420 and c.forbidden_pictograms == ["GHS02"]
    assert c.forbidden_signal_word == "危险" and len(c.substances) == 1


# ── 预置清单（1-16） ────────────────────────────────────────────────────────

def test_compliance_lists_counts_and_spot_checks():
    from app.services.compliance_lists import ALL_LISTS, get_entry

    assert {lst.list_id for lst in ALL_LISTS} == {"new_pollutants_2023", "priority_controlled"}
    np_lst = next(l for l in ALL_LISTS if l.list_id == "new_pollutants_2023")
    pc_lst = next(l for l in ALL_LISTS if l.list_id == "priority_controlled")
    assert len(np_lst.entries) == 14
    assert len(pc_lst.entries) == 40
    pfos = get_entry("new_pollutants_2023", "NP-01")
    assert pfos is not None and len(pfos.cas_numbers) == 8 and "1763-23-1" in pfos.cas_numbers
    assert get_entry("priority_controlled", "PC001").cas_numbers == ("120-82-1",)
    assert get_entry("priority_controlled", "PC040").cas_numbers == ("68937-41-7",)
    # 第二批标记为待 EHS 复核（转录来源，未与官方 PDF 逐条核对）。
    assert all(not e.verified for e in pc_lst.entries if e.entry_id >= "PC023")
    assert all(e.verified for e in pc_lst.entries if e.entry_id < "PC023")


def test_compliance_lists_cas_well_formed_and_ids_unique():
    from app.services.compliance_lists import ALL_LISTS

    for lst in ALL_LISTS:
        ids = [e.entry_id for e in lst.entries]
        assert len(ids) == len(set(ids)), lst.list_id
        for e in lst.entries:
            for cas in e.cas_numbers:
                assert CAS_RE.match(cas), (lst.list_id, e.entry_id, cas)


def test_compliance_lists_endpoints(env):
    c = env["client"]
    r = c.get("/api/compliance-lists")
    assert r.status_code == 200
    lists = r.json()["lists"]
    assert {x["list_id"] for x in lists} == {"new_pollutants_2023", "priority_controlled"}
    r = c.get("/api/compliance-lists/new_pollutants_2023")
    assert r.status_code == 200 and len(r.json()["entries"]) == 14
    assert c.get("/api/compliance-lists/nope").status_code == 404


# ── 保存服务端复核（1-12/1-13）：绕过前端直接调 API ────────────────────────

def test_save_blocked_on_substance_redline(env):
    pid = env["project"]({"red_lines": [{"cas": "1330-20-7", "max_pct": 25, "reason": "客户禁用"}]})
    r = env["client"].post("/api/formulations/versions", json={
        "formulation": _formulation([_XYLENE, _WATER]), "project_id": pid})
    assert r.status_code == 422, r.text
    assert "红线" in r.json()["detail"]


def test_save_blocked_on_list_reference(env):
    pid = env["project"]({"red_lines": [
        {"list_id": "new_pollutants_2023", "list_entry_id": "NP-01", "max_pct": 0, "reason": "重点管控新污染物，禁用"}]})
    form = _formulation([{"name": "全氟辛烷磺酸", "cas_no": "1763-23-1", "role": "additive", "weight_pct": 0.5},
                         {"name": "水", "cas_no": "7732-18-5", "role": "solvent", "weight_pct": 99.5}])
    r = env["client"].post("/api/formulations/versions", json={"formulation": form, "project_id": pid})
    assert r.status_code == 422, r.text
    assert "PFOS" in r.json()["detail"]


def test_save_warns_on_voc_estimate_and_allows(env):
    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion

    pid = env["project"]({"voc_limit_g_l": 100})
    r = env["client"].post("/api/formulations/versions", json={
        "formulation": _formulation([_XYLENE, _WATER]), "project_id": pid})
    assert r.status_code == 200, r.text
    lineage_id = r.json()["lineage_id"]
    with default_session_factory()() as s:
        rows = s.query(FormulationVersion).filter_by(lineage_id=lineage_id).all()
        assert len(rows) == 1
        warnings = (rows[0].snapshot or {}).get("warnings") or []
    assert any("合规红线警告" in w and "VOC" in w for w in warnings), warnings


def test_save_allows_without_constraints(env):
    r = env["client"].post("/api/formulations/versions", json={
        "formulation": _formulation([_XYLENE, _WATER])})
    assert r.status_code == 200, r.text
    pid = env["project"]({})
    r = env["client"].post("/api/formulations/versions", json={
        "formulation": _formulation([_XYLENE, _WATER]), "project_id": pid})
    assert r.status_code == 200, r.text


# ── 推荐过滤（1-11） ────────────────────────────────────────────────────────

def _rec(name: str, components: list[dict]) -> RecommendedFormula:
    return RecommendedFormula(
        name=name, domain=ProductDomain.anticorrosion_coating,
        components=[RecommendedFormulaComponent(name=c["name"], cas_no=c.get("cas_no", ""),
                                                weight_pct=c["weight_pct"]) for c in components])


def test_recommend_filters_redline_violators(env):
    from app.services.recommend_pipeline import finalize_recommendation_bundle

    pid = env["project"]({"red_lines": [{"cas": "1330-20-7", "max_pct": 25}]})
    req = Requirement(domain=ProductDomain.anticorrosion_coating,
                      objectives=[ObjectiveSpec(metric="salt_spray_hours", direction="maximize")],
                      project_id=pid)
    recs = [
        _rec("bad", [{"name": "二甲苯", "cas_no": "1330-20-7", "weight_pct": 30},
                    {"name": "水", "cas_no": "7732-18-5", "weight_pct": 70}]),
        _rec("good", [{"name": "滑石粉", "cas_no": "", "weight_pct": 40},
                     {"name": "水", "cas_no": "7732-18-5", "weight_pct": 60}]),
    ]
    aligned, scored, warnings, _n, _div, _tradeoff = finalize_recommendation_bundle(
        recs, req, [], requested_n=2, include_tradeoff=False)
    names = [f.name for f in scored]
    assert "bad" not in names and "good" in names
    assert any("合规红线过滤：1" in w for w in warnings), warnings
    assert [r.name for r in aligned] == names  # 双列表同步


# ── 替换标注降权（1-11） ────────────────────────────────────────────────────

def test_substitution_annotates_redline_violators(env, monkeypatch):
    from app.db.database import default_session_factory
    from app.pipeline import reconstruct
    from app.services.substitution import find_substitutes

    monkeypatch.setattr("app.services.external_alternatives.external_substitutes_enabled", lambda: False)
    monkeypatch.setattr("app.services.surechembl_client.surechembl_enabled", lambda: False)
    pid = env["project"]({"voc_limit_g_l": 100})
    req = Requirement(domain=ProductDomain.anticorrosion_coating, project_id=pid)
    genome = reconstruct.genome_from_requirement(req)
    slot = next(i for i, s in enumerate(genome.slots) if s.material == "Xylene")
    with default_session_factory()() as session:
        report = find_substitutes(genome, slot, req, include_external=False,
                                  include_literature=False, include_surechembl=False,
                                  session=session)
    cands = report["candidates"]
    assert cands, "xylene 应有替代候选"
    # 溶剂替换后 VOC 估算仍 ~300 > 100：全部标注违反，且不被删除（替换是建议性的）。
    assert all(c["redline_violated"] for c in cands)
    assert all(any(h["code"] == "voc" for h in c["redline_hits"]) for c in cands)
    # 无约束时不标注。
    req2 = Requirement(domain=ProductDomain.anticorrosion_coating)
    with default_session_factory()() as session:
        report2 = find_substitutes(genome, slot, req2, include_external=False,
                                   include_literature=False, include_surechembl=False,
                                   session=session)
    assert all(not c["redline_violated"] for c in report2["candidates"])


# ── 门禁 GHS 约束（1-14） ───────────────────────────────────────────────────

def _gate_env(tmp_path, monkeypatch, spec_snapshot: dict, project_spec: dict | None = None):
    """Service-level gate fixtures: xylene (H226 → GHS02) + water formulation version."""
    import os

    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'gate.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import FormulationVersion, ProjectRow
    from app.db.models_plm import ProductRevisionRow, ProductRow
    from app.db.models_regulatory import SubstanceClassificationRow, SubstanceRow

    with default_session_factory()() as s:
        xyl = SubstanceRow(id=str(uuid.uuid4()), cas_no="1330-20-7", name_zh="二甲苯",
                           name_en="xylene", aliases=[])
        water = SubstanceRow(id=str(uuid.uuid4()), cas_no="7732-18-5", name_zh="水",
                             name_en="water", aliases=[])
        s.add_all([xyl, water])
        s.flush()
        locked = datetime(2026, 10, 1)
        s.add(SubstanceClassificationRow(id=str(uuid.uuid4()), substance_id=xyl.id,
                                         hazard_class="flammable_liquid", category="3",
                                         h_code="H226", source="manual", locked_at=locked,
                                         locked_by="tester"))
        s.add(FormulationVersion(id="fv-g", lineage_id="l", version=1, domain="coating", name="v1",
                                 created_at=datetime(2026, 10, 1),
                                 snapshot={"ingredients": [
                                     {"name": "二甲苯", "cas_no": "1330-20-7", "weight_pct": 30, "role": "solvent"},
                                     {"name": "水", "cas_no": "7732-18-5", "weight_pct": 70, "role": "solvent"}]}))
        proj_id = "proj-gate"
        payload = {"workspace": {"spec_contract": project_spec}} if project_spec else {}
        s.add(ProjectRow(id=proj_id, title="G", domain="coating", payload=payload))
        s.add(ProductRow(id="prod-g", code="FX-G", name_zh="G", project_id=proj_id))
        s.flush()  # 跨模块 Base 的 flush 排序漏 FK 依赖：先落盘父行，再建修订版
        s.add(ProductRevisionRow(id="rev-g", product_id="prod-g", rev="A",
                                 formulation_version_id="fv-g", spec_snapshot=spec_snapshot,
                                 created_at=datetime(2026, 10, 1)))
        s.commit()
    return default_session_factory


def _red_lines_check(result: dict) -> dict:
    return next(c for c in result["checks"] if c["key"] == "red_lines")


def test_gate_fails_on_forbidden_pictogram_and_voc(tmp_path, monkeypatch):
    from app.services.plm.release import gate as gate_mod

    factory = _gate_env(tmp_path, monkeypatch,
                        {"forbidden_pictograms": ["GHS02"], "voc_limit_g_l": 100})
    with factory()() as s:
        result = gate_mod.evaluate(s, "rev-g")
    check = _red_lines_check(result)
    assert check["status"] == "fail"
    msgs = " ".join(i["message"] for i in check["items"])
    assert "GHS02" in msgs and "VOC" in msgs


def test_gate_passes_without_ghs_constraints(tmp_path, monkeypatch):
    from app.services.plm.release import gate as gate_mod

    factory = _gate_env(tmp_path, monkeypatch, {})
    with factory()() as s:
        result = gate_mod.evaluate(s, "rev-g")
    check = _red_lines_check(result)
    assert not any("GHS" in i["message"] or "VOC" in i["message"] for i in check["items"])


def test_gate_falls_back_to_project_spec_contract(tmp_path, monkeypatch):
    from app.services.plm.release import gate as gate_mod

    # 修订版快照为空（拷贝丢字段），项目规格契约有 GHS02 禁令 → 门禁仍拦截。
    factory = _gate_env(tmp_path, monkeypatch, {},
                        project_spec={"forbidden_pictograms": ["GHS02"]})
    with factory()() as s:
        result = gate_mod.evaluate(s, "rev-g")
    check = _red_lines_check(result)
    assert check["status"] == "fail"
    assert any("GHS02" in i["message"] for i in check["items"])


# ── 定型携带 GHS 字段（1-14） ───────────────────────────────────────────────

def test_winning_carries_ghs_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'winning.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.db.models import ExperimentRow, FormulationVersion, ProjectRow
    from app.db.models_plm import ProductRevisionRow, ProductRow
    from app.services.identity.rbac import build_principal
    from app.services.plm import winning as winning_mod

    with default_session_factory()() as s:
        s.add(ProjectRow(id="proj-w", title="W", domain="coating", payload={}))
        s.add(ProductRow(id="prod-w", code="FX-W", name_zh="W", project_id="proj-w"))
        s.add(FormulationVersion(id="fv-w", lineage_id="l", version=1, domain="coating", name="v1",
                                 created_at=datetime(2026, 10, 1),
                                 snapshot={"ingredients": [{"name": "水", "weight_pct": 100}]}))
        s.add(ExperimentRow(id=1, project_id="proj-w", domain="coating", measured={}))
        s.flush()  # 跨模块 Base 的 flush 排序漏 FK 依赖：先落盘父行，再建修订版
        s.add(ProductRevisionRow(id="rev-prev", product_id="prod-w", rev="A",
                                 formulation_version_id="fv-w",
                                 spec_snapshot={"release_specs": [], "acceptance": [],
                                                "red_lines": [], "voc_limit_g_l": 420,
                                                "forbidden_pictograms": ["GHS02"],
                                                "forbidden_signal_word": "危险",
                                                "unrelated": "drop-me"},
                                 created_at=datetime(2026, 10, 1)))
        s.commit()
    principal = build_principal("u1", ["project_lead"], {})
    with default_session_factory()() as s:
        rev, _warnings = winning_mod.select_winning(
            s, product_id="prod-w", formulation_version_id="fv-w", experiment_id=1,
            user_id="u1", principal=principal)
        spec = rev.spec_snapshot or {}
    assert spec["voc_limit_g_l"] == 420
    assert spec["forbidden_pictograms"] == ["GHS02"]
    assert spec["forbidden_signal_word"] == "危险"
    assert "unrelated" not in spec
