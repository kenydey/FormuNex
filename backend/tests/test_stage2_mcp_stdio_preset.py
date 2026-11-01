"""Stage 2 / SR-009: stdio spawns only for admin-preset MCP servers."""
from __future__ import annotations

import pytest

from app.services import mcp_client
from app.services import skills_store


@pytest.fixture(autouse=True)
def _reset_mcp_state():
    # test_mcp_client.py 的 fixture 同款隔离：全量套件中先行的
    # lifespan shutdown 会置位 _MCP_SHUTDOWN 并毒化后续测试；本文件
    # 的 probe/acquire 断言只测 preset 门，不测 shutdown 门，故先清零。
    mcp_client._reset_client_cache()


def test_probe_refuses_unblessed_server_without_spawning(monkeypatch):
    # A command that would fail loudly if ever spawned — the gate must
    # fire first, so the error names the preset rule, not the spawn.
    out = mcp_client.probe_server(
        {"id": "evil", "command": "/nonexistent-binary-xyz",
         "transport": "stdio"},
        settings=None,
    )
    assert out["ok"] is False
    assert "admin-preset" in out["error"]


def test_acquire_refuses_unblessed_server():
    with pytest.raises(mcp_client._MCPError, match="admin-preset"):
        mcp_client._acquire_session({"id": "evil", "command": "true"})


def test_probe_succeeds_for_blessed_server(monkeypatch):
    monkeypatch.setattr(mcp_client._StdioSession, "start", lambda self, timeout_s=8.0: None)
    monkeypatch.setattr(mcp_client._StdioSession, "close", lambda self: None)
    monkeypatch.setattr(
        mcp_client._StdioSession, "_request",
        lambda self, method, params, timeout_s=8.0: {"tools": []},
    )
    monkeypatch.setattr(mcp_client, "_sleep", lambda s: None)
    try:
        out = mcp_client.probe_server(
            {"id": "ok", "command": "true", "transport": "stdio", "admin_preset": True},
            settings=None,
        )
    finally:
        mcp_client._reset_client_cache()
    assert out["ok"] is True


def test_save_bless_semantics(monkeypatch):
    monkeypatch.setattr(
        mcp_client, "_prefs_servers",
        lambda: [{"id": "old", "command": "true", "admin_preset": False}],
    )
    saved_prefs = {}

    def fake_save_prefs(patch):
        saved_prefs.update(patch)
        return patch

    monkeypatch.setattr(skills_store, "save_prefs", fake_save_prefs)
    rows = [
        {"id": "old", "command": "true"},
        {"id": "new", "command": "true", "admin_preset": True},  # client claim: ignored
    ]
    # Bless only "new": old keeps False, new becomes True.
    out = mcp_client.save_mcp_servers(rows, bless_ids={"new"})
    by_id = {r["id"]: r for r in out}
    assert by_id["old"]["admin_preset"] is False
    assert by_id["new"]["admin_preset"] is True
    # No bless set: client claims are ignored, stored flags preserved.
    out = mcp_client.save_mcp_servers(rows)
    by_id = {r["id"]: r for r in out}
    assert by_id["old"]["admin_preset"] is False
    assert by_id["new"]["admin_preset"] is False
