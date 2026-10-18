"""平台 bearer token（FORMUNEX_API_TOKEN）设置面板后端：GET/POST /api/settings/api-token。

约定：状态只返回掩码，绝不回显完整 token；设置写 .env + 运行时 overlay，立即生效；
清除传 null（空字符串是 422，避免表单误触清空）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import secrets_store as ss


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("FORMUNEX_ENVIRONMENT=test\n", encoding="utf-8")
    monkeypatch.setenv("FORMUNEX_ENV_FILE", str(path))
    monkeypatch.delenv("FORMUNEX_API_TOKEN", raising=False)
    get_settings.cache_clear()
    ss.reload_settings()
    yield path
    get_settings.cache_clear()
    ss.reload_settings()


@pytest.fixture()
def client(env_file):
    from app.main import app

    return TestClient(app)


def test_status_unset_when_no_token(client):
    r = client.get("/api/settings/api-token")
    assert r.status_code == 200
    body = r.json()
    assert body["set"] is False
    assert body["masked"] == ""


def test_set_token_persists_to_env_and_masks(client, env_file):
    token = "script-token-0123456789abcdef"
    r = client.post("/api/settings/api-token", json={"token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["set"] is True
    # 掩码只含后四位，完整 token 不出现在任何响应里。
    assert body["masked"] == "••••cdef"
    assert token not in r.text
    assert ss.read_env_file(env_file)["FORMUNEX_API_TOKEN"] == token
    # 运行时 overlay 立即生效：中间件解析到 UI 设置的 token。
    # （测试环境 api_auth_enabled 为空，resolve 会直接返回 None；用开启鉴权的
    # settings 副本验证 overlay 确实被 resolve 采纳。）
    from app.middleware.api_auth import resolve_api_token
    from app.services.runtime_secrets import effective_setting

    assert effective_setting(get_settings(), "api_token") == token
    enabled = get_settings().model_copy(update={"api_auth_enabled": True})
    assert resolve_api_token(enabled) == token


def test_status_never_leaks_full_token(client):
    token = "script-token-0123456789abcdef"
    client.post("/api/settings/api-token", json={"token": token})
    r = client.get("/api/settings/api-token")
    assert r.status_code == 200
    assert token not in r.text
    assert r.json()["masked"] == "••••cdef"


def test_clear_token_removes_env_line(client, env_file):
    client.post("/api/settings/api-token", json={"token": "script-token-0123456789abcdef"})
    assert "FORMUNEX_API_TOKEN" in ss.read_env_file(env_file)
    r = client.post("/api/settings/api-token", json={"token": None})
    assert r.status_code == 200
    assert r.json()["set"] is False
    assert "FORMUNEX_API_TOKEN" not in ss.read_env_file(env_file)


def test_empty_string_token_is_rejected(client):
    r = client.post("/api/settings/api-token", json={"token": "   "})
    assert r.status_code == 422


def test_whitespace_inside_token_is_rejected(client):
    r = client.post("/api/settings/api-token", json={"token": "abc def"})
    assert r.status_code == 422


def test_newline_in_token_is_rejected(client):
    r = client.post("/api/settings/api-token", json={"token": "abc\nFORMUNEX_API_AUTH_ENABLED=false"})
    assert r.status_code == 422


def test_short_token_masks_fully(client):
    # ≤16 字符的掩码不透露任何字符（与 secrets_store._mask 口径一致）。
    client.post("/api/settings/api-token", json={"token": "short-token-123"})
    r = client.get("/api/settings/api-token")
    assert r.json()["masked"] == "••••"
    assert "short-token-123" not in r.text
