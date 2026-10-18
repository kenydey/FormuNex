"""整合方案 §3: TS 技术标准 — 构建器、生成、COA 引用、模板。"""
from __future__ import annotations

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("docx")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.plm.documents.ts_builder import TsContext, TsIndicator, build_ts, clean_indicators  # noqa: E402

PASSWORD = "Test-Pass-1!"
USERS = {"rd": ["rd_engineer"], "ehs": ["ehs_regulatory"], "admin": ["sys_admin"]}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'ts.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    monkeypatch.setenv("FORMUNEX_PLM_COMPANY_NAME", "方枢示例化工有限公司")
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.commit()
    from app.main import app

    client = TestClient(app)
    tokens = {}
    for name, roles in USERS.items():
        with default_session_factory()() as s:
            accounts.create_user(s, username=name, password=PASSWORD, roles=roles)
            s.commit()
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    pid = client.post("/api/plm/products", json={"code": "TS-100", "name_zh": "TS-100 底漆"}).json()["id"]
    rid = client.post(f"/api/plm/products/{pid}/revisions", json={}).json()["id"]
    yield {"client": client, "pid": pid, "rid": rid, "h": tokens}
    get_settings.cache_clear()


INDICATORS = [
    {"key": "solids", "label": "不挥发物含量", "unit": "%", "spec_min": 55, "spec_max": 60,
     "test_method": "GB/T 1725-2007", "is_factory_inspection": True},
    {"key": "fineness", "label": "细度", "unit": "μm", "spec_max": 40, "test_method": "GB/T 1724-2019",
     "is_factory_inspection": True},
    {"key": "adhesion", "label": "附着力", "unit": "级", "spec_max": 1, "test_method": "GB/T 9286-2021",
     "is_factory_inspection": False},
]


# --------------------------------------------------------------------------- builder


def test_ts_has_seven_sections_and_indicator_table():
    doc = build_ts(TsContext(product={"name_zh": "TS-100", "code": "TS-100"}, company={"name": "方枢"},
                              indicators=[TsIndicator(**i) for i in INDICATORS],
                              inspection={"sampling": "每批抽检 1 件"}, revision={"version": "A.1"}))
    assert [s["key"] for s in doc["sections"]] == ["identification", "properties", "indicators",
                                                  "factory_inspection", "methods", "packaging_storage", "revisions"]
    table = doc["sections"][2]["tables"][0]
    assert table["rows"][0][0] == "不挥发物含量" and table["rows"][0][2] == "55 ~ 60"
    assert doc["meta"]["indicators"][0]["key"] == "solids"
    # TS 规格目标不进 facts.properties（门禁跨文档一致性不拿规格值与实测值比较）。
    assert "properties" not in (doc["meta"]["facts"] or {})


def test_ts_indicator_without_limits_is_missing():
    doc = build_ts(TsContext(product={"name_zh": "X"}, company={"name": "方枢"},
                              indicators=[TsIndicator(key="x", label="未定指标")], revision={}))
    assert any("未定指标" in m and "未设定规格限值" in m for m in doc["validation"]["missing"])


def test_clean_indicators_drops_invalid_rows_with_warning():
    out, warnings = clean_indicators([
        {"key": "ok", "label": "好", "spec_min": 1, "spec_max": 2},
        {"key": "bad", "label": "倒置", "spec_min": 5, "spec_max": 2},
        {"label": "无 key"},
        {"key": "nan", "label": "非数值", "spec_min": "abc"},
    ])
    assert [i.key for i in out] == ["ok"]
    assert len(warnings) == 3


def test_ts_template_with_limits_is_rejected(env):
    c, h = env["client"], env["h"]
    r = c.post("/api/plm/ts-templates", headers=h["rd"], json={
        "category": "涂料", "name": "底漆模板",
        "indicators": [{"key": "solids", "label": "固含量", "spec_min": 55, "spec_max": 60}]})
    assert r.status_code == 422
    assert "不得带规格限值" in r.text


def test_ts_template_seeds_structure_only(env):
    c, h = env["client"], env["h"]
    t = c.post("/api/plm/ts-templates", headers=h["rd"], json={
        "category": "涂料", "name": "底漆模板",
        "indicators": [{"key": "solids", "label": "固含量", "unit": "%", "test_method": "GB/T 1725-2007",
                        "is_factory_inspection": True}]}).json()
    assert t["version"] == 1
    doc = c.post(f"/api/plm/revisions/{env['rid']}/documents", json={
        "doc_type": "ts", "ts": {"template_id": t["id"], "inspection": {"sampling": "每批抽检"}}}).json()
    assert "id" in doc, doc
    # 模板只给结构不给限值 → 指标缺限值，文档报告缺项（质量填写限值后重新生成）。
    assert any("未设定规格限值" in m for m in doc["content"]["validation"]["missing"])
    assert doc["content"]["meta"]["ts_template_id"] == t["id"]


# --------------------------------------------------------------------------- generation + COA fallback


def test_ts_generate_with_explicit_indicators_and_versioning(env):
    c, rid = env["client"], env["rid"]
    body = {"doc_type": "ts", "ts": {"indicators": INDICATORS, "inspection": {"sampling": "每批抽检 1 件"},
                                   "properties": [{"key": "density_g_cm3", "label": "密度", "value": 1.35, "unit": "g/cm³"}]}}
    d1 = c.post(f"/api/plm/revisions/{rid}/documents", json=body).json()
    assert d1["doc_type"] == "ts" and d1["version_no"] == 1, d1
    assert d1["content"]["validation"]["missing"] == []
    d2 = c.post(f"/api/plm/revisions/{rid}/documents", json=body).json()
    assert d2["version_no"] == 2 and d2["supersedes_id"] == d1["id"]
    diff = c.get(f"/api/plm/documents/{d2['id']}/diff").json()
    assert diff["from"] == d1["id"]


def test_coa_falls_back_to_ts_factory_indicators(env):
    c, rid, pid = env["client"], env["rid"], env["pid"]
    c.post(f"/api/plm/revisions/{rid}/documents", json={
        "doc_type": "ts", "ts": {"indicators": INDICATORS, "inspection": {"sampling": "每批抽检"}}}).json()
    bid = c.post(f"/api/plm/products/{pid}/batches", json={"batch_no": "B-TS-1", "revision_id": rid}).json()["id"]
    c.post(f"/api/plm/batches/{bid}/qc-results", json={"results": [{"metric": "solids", "value": 57.1},
                                                                     {"metric": "fineness", "value": 35}]})
    coa = c.post(f"/api/plm/revisions/{rid}/documents",
                 json={"doc_type": "coa", "coa": {"batch_id": bid}}).json()
    assert "id" in coa, coa
    rows = coa["content"]["sections"][1]["tables"][0]["rows"]
    assert {r[0] for r in rows} == {"不挥发物含量", "细度"}  # 只取 TS 出厂检验指标，不含附着力
    assert coa["content"]["meta"]["overall"] == "PASS"
