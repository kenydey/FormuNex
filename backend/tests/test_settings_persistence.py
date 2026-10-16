"""Settings persistence across restarts (LLM runtime / secrets / env flags).

The bug class under test: values saved through the Settings UI landed either
nowhere (LLM provider/model/base URL lived only in the in-process overlay) or
in a ``.env`` file that pydantic-settings never read back (writer used the
repo-root path, reader used the CWD-relative path — always different in
Docker). A "restart" here is simulated by clearing the runtime overlay and the
settings cache, then running the same bootstrap the app lifespan runs.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings, resolve_env_path
from app.main import app
from app.services.runtime_secrets import reset_runtime_secrets
from app.services.secrets_store import (
    apply_persisted_ui_settings,
    persist_llm_runtime,
    read_env_file,
    reload_settings,
    update_secrets,
    write_env_updates,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Fresh env file per test + full FORMUNEX_* environ snapshot/restore."""
    snapshot = {k: v for k, v in os.environ.items() if k.startswith("FORMUNEX_")}
    monkeypatch.setenv("FORMUNEX_ENV_FILE", str(tmp_path / ".env"))
    reset_runtime_secrets()
    get_settings.cache_clear()
    reload_settings()
    yield
    for key in [k for k in os.environ if k.startswith("FORMUNEX_")]:
        if key not in snapshot:
            del os.environ[key]
    os.environ.update(snapshot)
    reset_runtime_secrets()
    get_settings.cache_clear()
    reload_settings()


def _simulate_restart() -> None:
    """New process: empty overlay, cold settings cache, lifespan bootstrap."""
    reset_runtime_secrets()
    get_settings.cache_clear()
    reload_settings()


# ── LLM runtime settings ─────────────────────────────────────────────────────


def test_llm_settings_survive_restart():
    r = client.post(
        "/api/settings",
        json={"provider": "deepseek", "model": "deepseek-chat", "baseUrl": "https://api.deepseek.com"},
    )
    assert r.status_code == 200

    saved = read_env_file()
    assert saved["FORMUNEX_LLM_PROVIDER"] == "deepseek"
    assert saved["FORMUNEX_LLM_MODEL"] == "deepseek-chat"
    assert saved["FORMUNEX_LLM_BASE_URL"] == "https://api.deepseek.com"

    _simulate_restart()
    r = client.get("/api/settings")
    data = r.json()
    assert data["provider"] == "deepseek"
    assert data["model"] == "deepseek-chat"
    assert data["base_url"] == "https://api.deepseek.com"


def test_llm_partial_update_persists_effective_state():
    client.post("/api/settings", json={"provider": "deepseek", "model": "deepseek-chat"})
    # A later model-only update must not erase the persisted provider.
    client.post("/api/settings", json={"model": "deepseek-reasoner"})
    saved = read_env_file()
    assert saved["FORMUNEX_LLM_PROVIDER"] == "deepseek"
    assert saved["FORMUNEX_LLM_MODEL"] == "deepseek-reasoner"

    _simulate_restart()
    data = client.get("/api/settings").json()
    assert data["provider"] == "deepseek"
    assert data["model"] == "deepseek-reasoner"


def test_persist_llm_runtime_drops_empty_base_url():
    client.post("/api/settings", json={"provider": "anthropic", "model": "claude-sonnet-4-6"})
    persist_llm_runtime()
    saved = read_env_file()
    # anthropic has no custom base URL -> key removed, not written empty.
    assert "FORMUNEX_LLM_BASE_URL" not in saved or saved["FORMUNEX_LLM_BASE_URL"]


# ── API secrets ──────────────────────────────────────────────────────────────


def test_secret_survives_restart():
    update_secrets({"tavily_api_key": "tvly-test-123"})
    assert read_env_file()["FORMUNEX_TAVILY_API_KEY"] == "tvly-test-123"

    _simulate_restart()
    s = get_settings()
    from app.services.runtime_secrets import effective_setting

    assert effective_setting(s, "tavily_api_key") == "tvly-test-123"


# ── env flags ────────────────────────────────────────────────────────────────


def test_env_flag_survives_restart_even_with_stale_env_var(monkeypatch):
    from app.services.env_flags import update_env_flags

    update_env_flags({"content_filter_enabled": False})
    assert read_env_file()["FORMUNEX_CONTENT_FILTER_ENABLED"] == "false"

    # Simulate a stale container-level env var (docker compose env_file
    # injection) that would normally outrank the dotenv value.
    monkeypatch.setenv("FORMUNEX_CONTENT_FILTER_ENABLED", "true")
    _simulate_restart()
    assert get_settings().content_filter_enabled is False


# ── startup re-application scope ─────────────────────────────────────────────


def test_apply_persisted_only_touches_managed_keys(monkeypatch):
    write_env_updates(
        {
            "FORMUNEX_LLM_PROVIDER": "deepseek",
            "FORMUNEX_TAVILY_API_KEY": "tvly-abc",
            "FORMUNEX_CONTENT_FILTER_ENABLED": "false",
            "FORMUNEX_DB_URL": "sqlite:///./data/evil-override.db",
        }
    )
    monkeypatch.delenv("FORMUNEX_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("FORMUNEX_DB_URL", raising=False)

    apply_persisted_ui_settings()
    assert os.environ["FORMUNEX_LLM_PROVIDER"] == "deepseek"
    assert os.environ["FORMUNEX_TAVILY_API_KEY"] == "tvly-abc"
    assert os.environ["FORMUNEX_CONTENT_FILTER_ENABLED"] == "false"
    # Operator-level keys are not promoted into the process environment.
    assert "FORMUNEX_DB_URL" not in os.environ


# ── canonical env path resolution ────────────────────────────────────────────


def test_resolve_env_path_honours_override(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUNEX_ENV_FILE", str(tmp_path / "custom.env"))
    assert resolve_env_path() == tmp_path / "custom.env"


def test_resolve_env_path_prefers_existing_candidates(monkeypatch, tmp_path):
    monkeypatch.delenv("FORMUNEX_ENV_FILE", raising=False)
    backend = tmp_path / "repo" / "backend"
    backend.mkdir(parents=True)
    # No .env anywhere yet -> creation default is the (writable) repo root.
    assert resolve_env_path(backend_dir=backend) == tmp_path / "repo" / ".env"
    # backend/.env exists -> picked up.
    (backend / ".env").write_text("X=1\n", encoding="utf-8")
    assert resolve_env_path(backend_dir=backend) == backend / ".env"
    # repo-root .env exists -> outranks backend/.env.
    (tmp_path / "repo" / ".env").write_text("X=1\n", encoding="utf-8")
    assert resolve_env_path(backend_dir=backend) == tmp_path / "repo" / ".env"


def test_resolve_env_path_docker_layout_falls_back_to_data_dir(monkeypatch, tmp_path):
    """Package at a filesystem root (Docker /app) -> persistent data/.env."""
    monkeypatch.delenv("FORMUNEX_ENV_FILE", raising=False)
    import app.config as config_mod

    monkeypatch.setattr(
        config_mod, "_data_env_path", lambda: tmp_path / "data" / ".env"
    )
    root_backend = tmp_path / "rootfs"
    root_backend.mkdir()
    monkeypatch.setattr(config_mod.os, "access", lambda p, m: False)
    resolved = config_mod.resolve_env_path(backend_dir=root_backend)
    assert resolved == tmp_path / "data" / ".env"
