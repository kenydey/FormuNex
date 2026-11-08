"""补齐计划 Phase 1D (1-15)：RBAC 全覆盖。

覆盖：审计脚本零未覆盖写路由、新规则表抽查（材料 / Wiki / PLM / 工件）、
敏感读（用户 / 角色列表）需 admin.users、服务 key 豁免白名单有文档。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

PASSWORD = "Passw0rd!x"
USERS = {
    "rd": ["rd_engineer"],       # formulation.write / project.write
    "lab": ["lab_technician"],   # formulation.read only — no write
    "admin": ["sys_admin"],          # admin.* (user administration)
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'rbac15.db'}")
    monkeypatch.setenv("FORMUNEX_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUNEX_AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_settings.cache_clear()
    from app.db.database import default_session_factory
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    with default_session_factory()() as s:
        sync_builtin_roles(s)
        s.flush()
        for name, roles in USERS.items():
            accounts.create_user(s, username=name, password=PASSWORD, roles=roles)
        s.commit()
    from app.main import app

    client = TestClient(app)
    tokens = {}
    for name in USERS:
        r = client.post("/api/identity/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, r.text
        tokens[name] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    yield {"client": client, "h": tokens}
    get_settings.cache_clear()


# ── 规则表 ──────────────────────────────────────────────────────────────────

def test_guard_table_covers_new_prefixes():
    from app.api._rbac_guard import required_permission

    cases = [
        ("POST", "/api/materials", "knowledge.write"),
        ("POST", "/api/materials/substitutes", "knowledge.read"),
        ("POST", "/api/wiki/drafts/save", "knowledge.write"),
        ("POST", "/api/wiki/dossier/report", "knowledge.read"),
        ("POST", "/api/plm/products", "plm.document.write"),
        ("POST", "/api/plm/substances/sds-import", "knowledge.write"),
        ("POST", "/api/plm/ghs/classify", "plm.document.write"),
        ("POST", "/api/plm/label-templates/preview", "plm.document.read"),
        ("POST", "/api/plm/templates/preview", "plm.document.read"),  # Phase 3B：模板预览，纯渲染
        ("POST", "/api/plm/labels/batch", "plm.document.read"),  # Phase 3B：标签批量，纯渲染
        ("POST", "/api/artifacts/lineages", "project.write"),
        ("POST", "/api/collections", "knowledge.write"),
        ("POST", "/api/kg/rebuild", "knowledge.write"),
        ("POST", "/api/kg/retrieve", "knowledge.read"),
        ("POST", "/api/cockpit/winning", "formulation.freeze"),
        ("POST", "/api/cockpit/pareto", "formulation.read"),
        ("POST", "/api/design/inverse", "optimize.run"),
        ("POST", "/api/search", "knowledge.read"),
        ("POST", "/api/chat", None),  # 对话接口：策略豁免（触发的动作另行受检）
        ("GET", "/api/identity/users", "admin.users"),
        ("GET", "/api/identity/roles", "admin.users"),
        ("GET", "/api/projects/anything", None),  # 普通读不拦截
        # P1-A1/P1-A2 (2026-11-04 deep review): diff 与源文件下载补登记。
        ("GET", "/api/plm/documents/abc/diff", "plm.document.read"),
        ("GET", "/api/documents/abc/file", "knowledge.read"),
    ]
    for method, path, expected in cases:
        assert required_permission(method, path)[0] == expected, (method, path)


def test_endpoint_enforced_table_covers_lab_signoff_identity():
    from app.api._rbac_guard import ENDPOINT_ENFORCED

    paths = [p.pattern for p, _ in ENDPOINT_ENFORCED]
    joined = "\n".join(paths)
    for needle in ("lab/eln", "lab/tasks", "plm/documents", "plm/packages",
                   "plm/revisions", "identity/users", "label-templates", "tasks/"):
        assert needle in joined, needle


# ── 端到端：越权 403 / 有权通过 ─────────────────────────────────────────────

def test_new_write_routes_enforce_permission(env):
    c, h = env["client"], env["h"]
    # 无写权限的个人账号 → 403（绕过前端直接调 API 也拦截）。
    r = c.post("/api/materials", json={"name": "X"}, headers=h["lab"])
    assert r.status_code == 403, r.text
    r = c.post("/api/wiki/drafts/save", json={}, headers=h["lab"])
    assert r.status_code == 403, r.text
    r = c.post("/api/plm/products", json={"code": "P", "name_zh": "P"}, headers=h["lab"])
    assert r.status_code == 403, r.text


def test_sensitive_reads_require_admin_users(env):
    c, h = env["client"], env["h"]
    assert c.get("/api/identity/users", headers=h["lab"]).status_code == 403
    assert c.get("/api/identity/roles", headers=h["lab"]).status_code == 403
    assert c.get("/api/identity/users", headers=h["admin"]).status_code == 200
    assert c.get("/api/identity/roles", headers=h["admin"]).status_code == 200


def test_service_key_exemption_is_documented():
    from app.api._rbac_guard import SERVICE_KEY_EXEMPT_REASON

    assert SERVICE_KEY_EXEMPT_REASON
    assert "middleware" in SERVICE_KEY_EXEMPT_REASON.lower() or "中间件" in SERVICE_KEY_EXEMPT_REASON


def test_rbac_audit_script_passes():
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "audit_rbac.py")],
        cwd=root, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Stage 2 audit wording: exemptions are not coverage, and endpoint
    # claims are verified against endpoint source.
    assert "every non-exempt write route covered by exactly one verified layer" in proc.stdout


def test_sensitive_reads_diff_and_source_file(env):
    """P1-A1/P1-A2 live: diff needs plm.document.read (lab lacks it, rd
    passes the guard and then 404s on the missing document); source file
    needs knowledge.read (all fixture roles hold it, so rd passes the
    guard and 404s — the mapping test above pins the registration)."""
    c, h = env["client"], env["h"]
    assert c.get("/api/plm/documents/nope/diff", headers=h["lab"]).status_code == 403
    assert c.get("/api/plm/documents/nope/diff", headers=h["rd"]).status_code == 404
    assert c.get("/api/documents/nope/file", headers=h["rd"]).status_code == 404


def test_experiment_attachment_guard_helper(env):
    """P1-A3: the lab.execute/lab.review project-scoped rule itself —
    admin (no lab perms) is refused, lab (global lab.execute) passes."""
    import pytest
    from fastapi import HTTPException
    from starlette.requests import Request

    from app.api.experiments import _require_lab_attachment_access

    def _req(token: str) -> Request:
        return Request({"type": "http", "method": "GET", "path": "/x",
                        "headers": [(b"authorization", f"Bearer {token}".encode())]})

    tokens = _fixture_tokens(env)
    with pytest.raises(HTTPException) as exc:
        _require_lab_attachment_access(_req(tokens["admin"]), "p1")
    assert exc.value.status_code == 403
    _require_lab_attachment_access(_req(tokens["lab"]), "p1")
    _require_lab_attachment_access(_req(tokens["lab"]), None)


def _fixture_tokens(env):
    """Raw access tokens, split out of the fixture's Authorization headers
    (avoids duplicating the fixture password)."""
    return {name: headers["Authorization"].split(None, 1)[1]
            for name, headers in env["h"].items()}


def test_experiment_attachments_list_guarded(env):
    """P1-A3 live: native attachment list needs lab.execute/lab.review;
    unknown experiments 404 (no id enumeration through the list)."""
    from app.db.database import default_session_factory
    from app.db.models import ExperimentRow

    with default_session_factory()() as s:
        s.add(ExperimentRow(id=4242, domain="doe", project_id="p1", measured={}))
        s.commit()
    c, h = env["client"], env["h"]
    assert c.get("/api/experiments/4242/attachments", headers=h["admin"]).status_code == 403
    r = c.get("/api/experiments/4242/attachments", headers=h["lab"])
    assert r.status_code == 200, r.text
    assert r.json() == []
    assert c.get("/api/experiments/999999/attachments", headers=h["lab"]).status_code == 404


def test_project_export_download_guarded(env):
    """P1-A4 live: export download needs project.write (global or
    project-scoped) or project membership; admin has neither → 403,
    rd (global project.write) passes the guard and then 404s."""
    c, h = env["client"], env["h"]
    assert c.get("/api/projects/p1/exports/x.csv", headers=h["admin"]).status_code == 403
    assert c.get("/api/projects/p1/exports/x.csv", headers=h["rd"]).status_code == 404
