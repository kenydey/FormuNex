"""The desktop UI calls the API from tauri://localhost: CORS must allow that origin and expose headers."""
from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def test_desktop_origin_is_allowed_and_headers_exposed(monkeypatch):
    monkeypatch.setenv("FORMUNEX_CORS_ORIGINS", '["tauri://localhost","http://tauri.localhost"]')
    from app import config

    config.get_settings.cache_clear()
    import app.main as main

    main = importlib.reload(main)
    try:
        client = TestClient(main.app)
        pre = client.options("/api/identity/login", headers={
            "Origin": "tauri://localhost", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"})
        assert pre.status_code == 200
        assert pre.headers["access-control-allow-origin"] == "tauri://localhost"
        r = client.get("/health", headers={"Origin": "http://tauri.localhost"})
        assert r.headers["access-control-allow-origin"] == "http://tauri.localhost"
        assert r.headers["access-control-expose-headers"] == "*"
        assert "access-control-allow-origin" not in client.get("/health", headers={"Origin": "https://evil.example"}).headers
    finally:
        monkeypatch.delenv("FORMUNEX_CORS_ORIGINS")
        config.get_settings.cache_clear()
        importlib.reload(main)
