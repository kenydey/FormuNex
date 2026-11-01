"""Stage 2 / SR-041: allow policies expire (session 24h / project 30d /
global 90d); once is consumed, block/ask never expire; legacy rows
(NULL expires_at) expire from created_at + TTL at evaluation time."""
from __future__ import annotations

import time

import pytest

from app.db.database import Base, make_engine, make_session_factory
from app.db.models import McpToolPolicyRow
from app.services import mcp_approval


@pytest.fixture()
def sf(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/allow_ttl.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


SID, TOOL = "srv1", "read_file"
NOW = 1_800_000_000.0


def _seed(sf, scope, decision="allow", created_at=NOW, expires_at=None):
    with sf() as session:
        session.add(McpToolPolicyRow(
            server_id=SID, tool_name=TOOL, scope=scope, decision=decision,
            created_at=created_at, updated_at=created_at, expires_at=expires_at,
        ))
        session.commit()


def test_new_allow_gets_scope_ttl(sf):
    mcp_approval.set_policy(SID, TOOL, "allow", "session", session_factory=sf)
    policies = mcp_approval.list_policies(session_factory=sf)
    assert len(policies) == 1
    row = policies[0]
    assert row["expires_at"] - row["created_at"] == pytest.approx(24 * 3600, abs=2)
    assert row["expired"] is False


def test_block_and_once_get_no_expiry(sf):
    mcp_approval.set_policy(SID, TOOL, "block", "global", session_factory=sf)
    mcp_approval.set_policy(SID, TOOL, "allow", "once", session_factory=sf)
    rows = {r["scope"]: r for r in mcp_approval.list_policies(session_factory=sf)}
    assert rows["global"]["expires_at"] is None
    assert rows["once"]["expires_at"] is None


def test_expired_allow_denies(sf):
    _seed(sf, "global", expires_at=NOW - 1)
    v = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=NOW)
    assert v["verdict"] == "deny" and v["reason"] == "approval_required"


def test_legacy_row_expires_from_created_at(sf):
    # No expires_at: session allow created 25h ago is past its 24h TTL.
    _seed(sf, "session", created_at=NOW - 25 * 3600, expires_at=None)
    v = mcp_approval.evaluate_call(
        SID, TOOL, session_factory=sf, now=NOW, session_id="s1")
    assert v["verdict"] == "deny"
    # A project allow created 1h ago (30d TTL) still grants.
    _seed(sf, "project", created_at=NOW - 3600, expires_at=None)
    v = mcp_approval.evaluate_call(
        SID, TOOL, session_factory=sf, now=NOW, project_id="p1")
    assert v["verdict"] == "allow"


def test_backfill_materializes_and_drops_expired(sf):
    _seed(sf, "session", created_at=NOW - 3600, expires_at=None)  # fresh
    _seed(sf, "global", created_at=NOW - 100 * 86400, expires_at=None)  # stale
    _seed(sf, "project", decision="block", created_at=NOW - 3600)
    out = mcp_approval.backfill_allow_expiry(session_factory=sf, now=NOW)
    assert out == {"materialized": 1, "expired_deleted": 1}
    rows = {r["scope"]: r for r in mcp_approval.list_policies(session_factory=sf)}
    assert set(rows) == {"session", "project"}
    assert rows["session"]["expires_at"] == NOW - 3600 + 24 * 3600
    assert rows["project"]["decision"] == "block"
    # Second run is a no-op.
    assert mcp_approval.backfill_allow_expiry(session_factory=sf, now=NOW) == {
        "materialized": 0, "expired_deleted": 0}


def test_revoke_policy(sf):
    mcp_approval.set_policy(SID, TOOL, "allow", "global", session_factory=sf)
    assert mcp_approval.revoke_policy(SID, TOOL, "global", session_factory=sf) is True
    assert mcp_approval.revoke_policy(SID, TOOL, "global", session_factory=sf) is False
    assert mcp_approval.list_policies(session_factory=sf) == []
