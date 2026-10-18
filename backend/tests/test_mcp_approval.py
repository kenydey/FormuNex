"""W2-7 (P1-3) MCP 逐工具审批三态测试。

Phase 2D (2-24): approval state lives in the main database (``mcp_tool_policies`` /
``mcp_approval_requests``), reached through the ``session_factory`` contract —
no more standalone SQLite file / ``path=`` / ``ensure_store``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db.database import Base, make_engine, make_session_factory
from app.db.models import McpToolPolicyRow
from app.services import mcp_approval
from app.services import mcp_client


@pytest.fixture()
def sf(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/approval.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


SID, TOOL = "srv1", "read_file"


def test_default_policy_is_ask_deny(sf):
    v = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    assert v["verdict"] == "deny"
    assert v["reason"] == "approval_required"
    assert v["request_id"] is not None


def test_allow_policy_permits(sf):
    mcp_approval.set_policy(SID, TOOL, "allow", "global", session_factory=sf)
    assert mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)["verdict"] == "allow"


def test_block_policy_denies(sf):
    mcp_approval.set_policy(SID, TOOL, "block", "global", session_factory=sf)
    v = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    assert v["verdict"] == "deny" and v["reason"] == "policy_block"


def test_block_overrides_allow(sf):
    mcp_approval.set_policy(SID, TOOL, "allow", "global", session_factory=sf)
    mcp_approval.set_policy(SID, TOOL, "block", "session", session_factory=sf)
    v = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    assert v["verdict"] == "deny" and v["reason"] == "policy_block"


def test_ask_overrides_allow(sf):
    mcp_approval.set_policy(SID, TOOL, "allow", "global", session_factory=sf)
    mcp_approval.set_policy(SID, TOOL, "ask", "project", session_factory=sf)
    v = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    assert v["verdict"] == "deny" and v["reason"] == "approval_required"


def test_effective_decision_priority():
    assert mcp_approval.effective_decision({}) == "ask"
    assert mcp_approval.effective_decision({"global": "allow"}) == "allow"
    assert mcp_approval.effective_decision({"global": "allow", "session": "ask"}) == "ask"
    assert mcp_approval.effective_decision({"global": "allow", "project": "block"}) == "block"


def test_pending_within_timeout_reuses_request(sf):
    v1 = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    v2 = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1100.0)
    assert v2["reason"] == "approval_pending"
    assert v2["request_id"] == v1["request_id"]


def test_ask_timeout_auto_deny(sf):
    v1 = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    v2 = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0 + 301.0)
    assert v2["verdict"] == "deny" and v2["reason"] == "approval_timeout"
    # 已过期，再次求值应产生新的 pending 请求
    v3 = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1400.0)
    assert v3["reason"] == "approval_required" and v3["request_id"] != v1["request_id"]


def test_decide_allow_then_call_permitted(sf):
    v = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    r = mcp_approval.decide_approval(v["request_id"], "allow", scope="session", session_factory=sf, now=1100.0)
    assert r["ok"] is True
    assert mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1200.0)["verdict"] == "allow"


def test_decide_deny_keeps_denying(sf):
    v = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    r = mcp_approval.decide_approval(v["request_id"], "deny", session_factory=sf, now=1100.0)
    assert r["ok"] is True
    v2 = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1200.0)
    assert v2["verdict"] == "deny"


def test_invalid_inputs_raise(sf):
    with pytest.raises(ValueError):
        mcp_approval.set_policy(SID, TOOL, "maybe", "global", session_factory=sf)
    with pytest.raises(ValueError):
        mcp_approval.set_policy(SID, TOOL, "allow", "everywhere", session_factory=sf)
    with pytest.raises(ValueError):
        mcp_approval.decide_approval("x", "maybe", session_factory=sf)


def test_policy_persistence_roundtrip(sf):
    mcp_approval.set_policy(SID, TOOL, "block", "project", session_factory=sf)
    with sf() as session:
        row = session.get(McpToolPolicyRow, (SID, TOOL, "project"))
    assert row is not None and row.decision == "block"
    assert mcp_approval.get_policies(SID, TOOL, session_factory=sf) == {"project": "block"}


def test_list_pending_excludes_expired(sf):
    v = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    assert mcp_approval.list_pending(session_factory=sf, now=1100.0)["total"] == 1
    assert mcp_approval.list_pending(session_factory=sf, now=1000.0 + 301.0)["total"] == 0
    assert v["request_id"] is not None


# ---- mcp_client 门禁集成 ----


def _enable():
    return SimpleNamespace(mcp_approval_enabled=True)


def test_gate_blocks_when_flag_off(monkeypatch, tmp_path):
    # flag 关闭 → 门禁不拦截（走到 server 查找，server 不存在返回 not found）
    out = mcp_client._call_tool("no-such", "t", settings=SimpleNamespace(mcp_approval_enabled=False))
    assert out == {"ok": False, "error": "server not found or disabled"}


def test_gate_denies_by_default_when_enabled(monkeypatch, sf):
    monkeypatch.setattr(mcp_approval, "_session_factory", lambda _sf=None: sf)
    out = mcp_client._call_tool("srv1", "some_tool", settings=_enable())
    assert out["ok"] is False
    assert out["error"] == "approval_required"
    assert out["mcp_approval_required"]["request_id"] is not None


def test_gate_allows_with_allow_policy(monkeypatch, sf):
    monkeypatch.setattr(mcp_approval, "_session_factory", lambda _sf=None: sf)
    mcp_approval.set_policy("srv1", "some_tool", "allow", "global")
    out = mcp_client._call_tool("srv1", "some_tool", settings=_enable())
    # 放行后走到 server 查找
    assert out == {"ok": False, "error": "server not found or disabled"}


def test_gate_fail_closed_on_approval_crash(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(mcp_approval, "evaluate_call", boom)
    out = mcp_client._call_tool("srv1", "some_tool", settings=_enable())
    assert out["ok"] is False
    assert out["error"] == "mcp_approval_unavailable"
    assert "mcp_approval_required" in out
