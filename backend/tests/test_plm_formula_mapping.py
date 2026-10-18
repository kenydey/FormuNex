"""整合方案 §6: 配方数据库映射 — 导入、冲突跳过、dry-run、创建修订版。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402

PASSWORD = "Test-Pass-1!"

RECORD = {"product_code": "IMP-100", "product_name_zh": "IMP-100 底漆", "version": 1,
          "company": {"name": "方枢示例化工有限公司", "emergency_phone": "021-0001"},
          "components": [{"name": "二甲苯", "pct": 25, "cas": "1330-20-7"},
                         {"name": "环氧树脂", "pct": 60, "cas": "25068-38-6"},
                         {"name": "水", "pct": 15, "cas": "7732-18-5"}],
          "source_ref": "msds-gen-001"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'formula.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.commit()
    from app.main import app

    client = TestClient(app)
    with default_session_factory()() as s:
        accounts.create_user(s, username="rd", password=PASSWORD, roles=["rd_engineer"])
        accounts.create_user(s, username="lead", password=PASSWORD, roles=["project_lead"])
        s.commit()
    r = client.post("/api/identity/login", json={"username": "rd", "password": PASSWORD})
    assert r.status_code == 200, r.text
    r2 = client.post("/api/identity/login", json={"username": "lead", "password": PASSWORD})
    assert r2.status_code == 200, r2.text
    yield {"client": client, "h": {"Authorization": f"Bearer {r.json()['access_token']}"},
           "h_lead": {"Authorization": f"Bearer {r2.json()['access_token']}"}}
    get_settings.cache_clear()


def _session():
    from app.db.database import default_session_factory

    return default_session_factory()()


def test_import_creates_company_product_formulation_mapping(env):
    from app.services.plm import formula_mapping

    with _session() as s:
        report = formula_mapping.import_formulas(s, [RECORD])
        s.commit()
        assert len(report["created"]) == 1 and report["errors"] == []
        created = report["created"][0]
        assert created["formulation_version_id"]
    with _session() as s:
        from app.db.models import FormulationVersion
        from app.db.models_plm import CompanyRow, FormulaMappingRow, ProductRow

        m = s.query(FormulaMappingRow).one()
        assert m.source == "msds_generator" and m.source_ref == "msds-gen-001"
        assert s.get(ProductRow, m.product_id).code == "IMP-100"
        assert s.get(CompanyRow, m.company_id).name == "方枢示例化工有限公司"
        fv = s.get(FormulationVersion, m.formulation_version_id)
        assert fv.snapshot["source"] == "msds_generator"
        assert fv.snapshot["ingredients"][0] == {"name": "二甲苯", "pct": 25.0, "cas": "1330-20-7"}
        assert fv.domain == "plm_import"


def test_reimport_same_ref_is_skipped_not_duplicated(env):
    from app.services.plm import formula_mapping

    with _session() as s:
        formula_mapping.import_formulas(s, [RECORD])
        s.commit()
        report = formula_mapping.import_formulas(s, [RECORD])
        s.commit()
        assert report["created"] == [] and len(report["skipped"]) == 1
        assert report["skipped"][0]["reason"].startswith("同公司同产品同 source_ref")


def test_import_rejects_bad_totals_and_missing_product_code(env):
    from app.services.plm import formula_mapping

    with _session() as s:
        report = formula_mapping.import_formulas(s, [
            {**RECORD, "source_ref": "bad-total",
             "components": [{"name": "水", "pct": 50}]},
            {**RECORD, "source_ref": "no-code", "product_code": ""},
        ])
        s.commit()
        assert len(report["errors"]) == 2
        assert "100%" in report["errors"][0]["error"]
        assert "产品代码" in report["errors"][1]["error"]


def test_dry_run_writes_nothing(env):
    from app.db.models_plm import FormulaMappingRow
    from app.services.plm import formula_mapping

    with _session() as s:
        report = formula_mapping.import_formulas(s, [RECORD], dry_run=True)
        assert report["dry_run"] is True and len(report["created"]) == 1
    with _session() as s:
        assert s.query(FormulaMappingRow).count() == 0


def test_company_without_emergency_phone_is_not_created(env):
    from app.db.models_plm import CompanyRow
    from app.services.plm import formula_mapping

    record = {**RECORD, "source_ref": "no-phone",
              "company": {"name": "无应急电话公司"}}
    with _session() as s:
        report = formula_mapping.import_formulas(s, [record])
        s.commit()
        assert len(report["created"]) == 1
        assert any("应急电话" in w for w in report["warnings"])
    with _session() as s:
        assert s.query(CompanyRow).filter(CompanyRow.name == "无应急电话公司").count() == 0


def test_api_import_and_create_revision(env):
    c, h = env["client"], env["h"]
    r = c.post("/api/plm/formula-mappings/import", headers=h, json={"records": [RECORD]})
    assert r.status_code == 200, r.text
    assert len(r.json()["created"]) == 1
    lst = c.get("/api/plm/formula-mappings").json()
    assert len(lst) == 1 and lst[0]["product_code"] == "IMP-100"
    # dry-run: 报告 created 但不落库
    r2 = c.post("/api/plm/formula-mappings/import", headers=h, json={"records": [RECORD], "dry_run": True})
    assert r2.json()["created"] == []  # dry-run 对已存在记录同样判跳过
    rev = c.post(f"/api/plm/formula-mappings/{lst[0]['id']}/create-revision", headers=env["h_lead"])
    assert rev.status_code == 201, rev.text
    assert rev.json()["state"] == "draft"
    assert rev.json()["formulation_version_id"] == lst[0]["formulation_version_id"]


def test_import_script_loads_json_csv_jsonl(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "import_script", "scripts/import_msds_generator_formulas.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    p = tmp_path / "f.json"
    p.write_text(json.dumps([RECORD]), encoding="utf-8")
    assert mod._load_records(p)[0]["product_code"] == "IMP-100"
    p2 = tmp_path / "f.jsonl"
    p2.write_text(json.dumps(RECORD) + "\n", encoding="utf-8")
    assert mod._load_records(p2)[0]["product_code"] == "IMP-100"
    p3 = tmp_path / "f.csv"
    p3.write_text("product_code,product_name_zh,version,company_name,company_emergency_phone,components,source_ref\n"
                  "IMP-200,底漆,1,方枢,021-0001,水:100:7732-18-5,ref-2\n", encoding="utf-8")
    rec = mod._load_records(p3)[0]
    assert rec["components"] == [{"name": "水", "pct": 100.0, "cas": "7732-18-5"}]
