"""W0-1: the two missing permissions, fail-closed unknown permissions, and the
five previously-500 endpoints under the three caller shapes.

Caller shapes (same pattern as test_phase1d_rbac.py, auth disabled at the
middleware so the guard itself is what is exercised):
- JWT with the permission -> the endpoint runs (200 where a trivial body
  suffices, otherwise the endpoint's own validation status — never 500/403).
- JWT without any role -> 403 from the guard.
- Opaque bearer (service-key shape, not a JWT) -> the guard skips the check,
  exactly like the operator credential path.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

PASSWORD = "W0test!123"
OPAQUE = {"Authorization": "Bearer operator-credential-not-a-jwt"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'w0_rbac.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_KG_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        for name, roles in (("rd", ["rd_engineer"]), ("nobody", [])):
            accounts.create_user(s, username=name, password=PASSWORD, roles=roles)
        s.commit()
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    tokens = {}
    for name in ("rd", "nobody"):
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield {"client": client, "h": tokens}
    get_settings.cache_clear()


def _minimal_docx() -> bytes:
    """A valid .docx shell (no template tags) for the template-preview 200."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   "</Relationships>")
        z.writestr("word/document.xml",
                   '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   "<w:body><w:p><w:r><w:t>hello</w:t></w:r></w:p></w:body></w:document>")
    return buf.getvalue()


# (label, method-call) — each returns a TestClient response.
def _calls(client):
    png_1x1 = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
               b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x05\x00\x01"
               b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
    return [
        ("enrich-materials",
         lambda h: client.post("/api/chemical/enrich-materials", json={"materials": []}, headers=h)),
        ("structure",
         lambda h: client.post("/api/chemical/structure",
                               files={"image": ("s.png", png_1x1, "image/png")}, headers=h)),
        ("templates/preview",
         lambda h: client.post("/api/plm/templates/preview",
                               files={"file": ("t.docx", _minimal_docx(),
                                               "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                               data={"doc_type": "sds", "lang": "zh-CN"}, headers=h)),
        ("label-templates/preview",
         lambda h: client.post("/api/plm/label-templates/preview",
                               json={"layout": {}, "variables": {}}, headers=h)),
        ("labels/batch",
         lambda h: client.post("/api/plm/labels/batch",
                               json={"labels": [{"content": {}}]}, headers=h)),
    ]


def test_five_endpoints_jwt_with_permission_200(env):
    c, h = env["client"], env["h"]
    for label, call in _calls(c):
        r = call(h["rd"])
        assert r.status_code == 200, (label, r.status_code, r.text[:300])


def test_five_endpoints_jwt_without_permission_403(env):
    c, h = env["client"], env["h"]
    for label, call in _calls(c):
        r = call(h["nobody"])
        assert r.status_code == 403, (label, r.status_code, r.text[:300])


def test_five_endpoints_service_key_shape_200(env):
    c = env["client"]
    for label, call in _calls(c):
        r = call(OPAQUE)
        assert r.status_code == 200, (label, r.status_code, r.text[:300])


def test_unknown_permission_is_fail_closed_not_keyerror():
    from app.services.identity.rbac import Principal

    p = Principal("u", frozenset({"knowledge.read"}), {})
    assert p.can("does.not.exist") is False
    assert p.can("does.not.exist", "p1") is False


def test_typo_role_permissions_dropped_at_load(env):
    """A custom role row with a typo'd permission string must degrade to 403,
    never 500, on the next guarded call."""
    from app.db.database import default_session_factory
    from app.db.models_identity import RoleRow, UserRoleRow
    from app.services.identity import accounts
    from app.services.identity.rbac import load_principal, validate_role_permissions

    assert validate_role_permissions({"weird": ["knowledge.read", "typo.perm"]}) == ["weird:typo.perm"]
    with default_session_factory()() as s:
        s.add(RoleRow(code="weird", name_zh="怪", name_en="weird",
                      permissions=["knowledge.read", "typo.perm"], builtin=False))
        s.flush()
        user = accounts.create_user(s, username="weird-user", password=PASSWORD, roles=["weird"])
        s.commit()
        principal = load_principal(s, user.id)
    assert principal.global_permissions == frozenset({"knowledge.read"})
    # And through HTTP: a gated read the typo'd role cannot grant -> 403, not 500.
    c = env["client"]
    r = c.post("/api/identity/login", json={"username": "weird-user", "password": PASSWORD})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = c.get("/api/identity/users", headers=h)
    assert r.status_code == 403
