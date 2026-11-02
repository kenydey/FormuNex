"""Stage 5 / SR-084: high-sensitivity MCP servers need an admin allowlist on the readonly path."""

from __future__ import annotations

from app.services import mcp_client


def _server(allowlist=None):
    server = {"id": "s1", "command": "true", "args": [], "enabled": True, "env": {}, "transport": "stdio",
              "admin_preset": True}
    if allowlist is not None:
        server["readonly_tool_allowlist"] = allowlist
    return server


def _probe(tools):
    return lambda s: {"ok": True, "tools": tools, "names": [t["name"] for t in tools]}


def _settings():
    class S:
        mcp_client_enabled = True
        mcp_approval_enabled = False

    return S()


def _stub(monkeypatch, server, tools):
    monkeypatch.setattr(mcp_client, "_prefs_servers", lambda: [server])
    monkeypatch.setattr(mcp_client, "probe_server", _probe(tools))
    monkeypatch.setattr(mcp_client, "_call_tool", lambda *a, **k: {"ok": True})


def test_sensitivity_default_portrait():
    read_only = [{"name": "search", "annotations": {"readOnlyHint": True}}]
    assert mcp_client.is_high_sensitivity_server(read_only) is False
    assert mcp_client.is_high_sensitivity_server(None) is False
    assert mcp_client.is_high_sensitivity_server([]) is False
    # Declared write, undeclared, or a file/database name — each marks the server.
    assert mcp_client.is_high_sensitivity_server(
        [{"name": "search", "annotations": {"readOnlyHint": True}},
         {"name": "write_file", "annotations": {"destructiveHint": True}}]) is True
    assert mcp_client.is_high_sensitivity_server(
        [{"name": "read_file", "annotations": {"readOnlyHint": True}}]) is True
    assert mcp_client.is_high_sensitivity_server(
        [{"name": "query_db", "annotations": {"readOnlyHint": True}}]) is True
    assert mcp_client.is_high_sensitivity_server(
        [{"name": "mystery", "annotations": {}}]) is True


def test_readonly_on_sensitive_server_needs_allowlist(monkeypatch):
    tools = [{"name": "read_file", "annotations": {"readOnlyHint": True}},
             {"name": "write_file", "annotations": {"destructiveHint": True}}]
    _stub(monkeypatch, _server(), tools)
    out = mcp_client.call_tool_readonly("s1", "read_file", {}, settings=_settings())
    assert out["ok"] is False and "allowlist" in out["error"]
    _stub(monkeypatch, _server(["read_file"]), tools)
    out = mcp_client.call_tool_readonly("s1", "read_file", {}, settings=_settings())
    assert out["ok"] is True


def test_readonly_on_pure_read_server_unchanged(monkeypatch):
    tools = [{"name": "search", "annotations": {"readOnlyHint": True}}]
    _stub(monkeypatch, _server(), tools)
    out = mcp_client.call_tool_readonly("s1", "search", {}, settings=_settings())
    assert out["ok"] is True


def test_save_preserves_allowlist_when_key_absent(monkeypatch):
    import app.services.mcp_client as mc
    import app.services.skills_store as skills_store

    # save_mcp_servers imports save_prefs from skills_store at call time.
    monkeypatch.setattr(skills_store, "save_prefs", lambda prefs: None)
    monkeypatch.setattr(mc, "_prefs_servers", lambda: [_server(["read_file"])])
    # Toggle / delete re-saves pass rows without the key: the stored
    # list must survive.
    out = mc.save_mcp_servers([{"id": "s1", "command": "true"}])
    assert out[0]["readonly_tool_allowlist"] == ["read_file"]
    # An explicit key replaces, including an explicit empty list.
    out = mc.save_mcp_servers([{"id": "s1", "command": "true", "readonly_tool_allowlist": []}])
    assert out[0]["readonly_tool_allowlist"] == []
