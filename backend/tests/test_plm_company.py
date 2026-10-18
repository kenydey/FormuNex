"""整合方案 §5: 公司配置 — CRUD、默认公司、Logo、文档公司来源。"""
from __future__ import annotations

import pytest

pytest.importorskip("reportlab")
pytest.importorskip("docx")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.plm.company import validate_logo  # noqa: E402

PASSWORD = "Test-Pass-1!"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'company.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    monkeypatch.setenv("FORMUNEX_PLM_COMPANY_NAME", "环境变量公司")
    monkeypatch.setenv("FORMUNEX_PLM_EMERGENCY_PHONE", "010-0000")
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
    for name, roles in {"admin": ["sys_admin"], "rd": ["rd_engineer"]}.items():
        with default_session_factory()() as s:
            accounts.create_user(s, username=name, password=PASSWORD, roles=roles)
            s.commit()
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield {"client": client, "h": tokens}
    get_settings.cache_clear()


COMPANY = {"name": "方枢示例化工有限公司", "address": "上海市示例路 1 号", "phone": "021-0000",
           "emergency_phone": "021-0001", "unified_credit_code": "91310000XXXX00001"}


def test_validate_logo_sniffs_bytes_not_extension():
    assert validate_logo(PNG) == ("png", "image/png")
    assert validate_logo(b"<?xml version=\"1.0\"?><svg xmlns=\"http://www.w3.org/2000/svg\"/>") == ("svg", "image/svg+xml")
    with pytest.raises(ValueError):
        validate_logo(b"not an image")
    with pytest.raises(ValueError):
        validate_logo(b"")


def test_first_company_becomes_default(env):
    c, h = env["client"], env["h"]
    row = c.post("/api/plm/companies", headers=h["admin"], json=COMPANY).json()
    assert row["is_default"] is True


def test_emergency_phone_required(env):
    c, h = env["client"], env["h"]
    r = c.post("/api/plm/companies", headers=h["admin"], json={**COMPANY, "emergency_phone": ""})
    assert r.status_code == 422


def test_company_write_requires_admin(env):
    c, h = env["client"], env["h"]
    r = c.post("/api/plm/companies", headers=h["rd"], json=COMPANY)
    assert r.status_code == 403


def test_set_default_switches(env):
    c, h = env["client"], env["h"]
    a = c.post("/api/plm/companies", headers=h["admin"], json=COMPANY).json()
    b = c.post("/api/plm/companies", headers=h["admin"],
               json={**COMPANY, "name": "第二家公司", "is_default": False}).json()
    assert b["is_default"] is False
    c.post(f"/api/plm/companies/{b['id']}/set-default", headers=h["admin"])
    assert c.get(f"/api/plm/companies/{a['id']}").json()["is_default"] is False
    assert c.get(f"/api/plm/companies/{b['id']}").json()["is_default"] is True


def test_logo_upload_and_download(env):
    c, h = env["client"], env["h"]
    row = c.post("/api/plm/companies", headers=h["admin"], json=COMPANY).json()
    r = c.post(f"/api/plm/companies/{row['id']}/logo", headers=h["admin"],
               files={"file": ("logo.txt", PNG, "text/plain")})  # 扩展名说谎，字节为准
    assert r.status_code == 200, r.text
    assert r.json()["has_logo"] is True
    dl = c.get(f"/api/plm/companies/{row['id']}/logo")
    assert dl.status_code == 200 and dl.content == PNG and dl.headers["content-type"] == "image/png"
    bad = c.post(f"/api/plm/companies/{row['id']}/logo", headers=h["admin"],
                 files={"file": ("x.png", b"not an image", "image/png")})
    assert bad.status_code == 422


def test_documents_use_default_company_over_env(env):
    c, h = env["client"], env["h"]
    c.post("/api/plm/companies", headers=h["admin"], json=COMPANY)
    pid = c.post("/api/plm/products", json={"code": "CO-1", "name_zh": "CO-1"}).json()["id"]
    rid = c.post(f"/api/plm/products/{pid}/revisions", json={}).json()["id"]
    doc = c.post(f"/api/plm/revisions/{rid}/documents", json={
        "doc_type": "tds", "tds": {"product": {"description": "示例"}, "properties": []}}).json()
    fields = {f["key"]: f["value"] for s in doc["content"]["sections"] for f in s["fields"]}
    assert fields["supplier"] == "方枢示例化工有限公司"  # 默认公司行优先于环境变量公司


def test_documents_fall_back_to_env_company_when_none_configured(env):
    c = env["client"]
    pid = c.post("/api/plm/products", json={"code": "CO-2", "name_zh": "CO-2"}).json()["id"]
    rid = c.post(f"/api/plm/products/{pid}/revisions", json={}).json()["id"]
    doc = c.post(f"/api/plm/revisions/{rid}/documents", json={
        "doc_type": "tds", "tds": {"product": {"description": "示例"}, "properties": []}}).json()
    fields = {f["key"]: f["value"] for s in doc["content"]["sections"] for f in s["fields"]}
    assert fields["supplier"] == "环境变量公司"
