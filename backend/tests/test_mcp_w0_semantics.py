
"""W0 (2026-10-27): MCP approval semantics — once consumption, scope attribution,
pending isolation, decide-time expiry, wall clock, hash-chain audit."""
from __future__ import annotations

import pytest

from app.db.database import Base, make_engine, make_session_factory
from app.services import mcp_approval


@pytest.fixture()
def sf(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/approval_w0.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


SID, TOOL = "srv1", "read_file"


# ---------------------------------------------------------------------------
# W0 (2026-10-27): approval semantics — once consumption, scope attribution,
# pending isolation, decide-time expiry, wall clock, hash-chain audit.
# ---------------------------------------------------------------------------


def test_once_allow_consumed_on_first_grant(sf):
    v = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    r = mcp_approval.decide_approval(v["request_id"], "allow", scope="once", session_factory=sf, now=1100.0)
    assert r["ok"] is True
    # First granting call consumes the once policy...
    assert mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1200.0)["verdict"] == "allow"
    # ...the second call re-enters approval instead of riding it forever.
    v2 = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1300.0)
    assert v2["verdict"] == "deny" and v2["reason"] == "approval_required"


def test_session_allow_ignored_without_session(sf):
    mcp_approval.set_policy(SID, TOOL, "allow", "session", session_factory=sf)
    v = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    assert v["verdict"] == "deny" and v["reason"] == "approval_required"
    v2 = mcp_approval.evaluate_call(SID, TOOL, session_id="s1", session_factory=sf, now=1100.0)
    assert v2["verdict"] == "allow"


def test_project_allow_ignored_without_project(sf):
    mcp_approval.set_policy(SID, TOOL, "allow", "project", session_factory=sf)
    v = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    assert v["verdict"] == "deny" and v["reason"] == "approval_required"
    v2 = mcp_approval.evaluate_call(SID, TOOL, project_id="p1", session_factory=sf, now=1100.0)
    assert v2["verdict"] == "allow"


def test_pending_isolated_by_session_and_args_not_overwritten(sf):
    v1 = mcp_approval.evaluate_call(SID, TOOL, session_id="s1", session_factory=sf, now=1000.0,
                                    arguments={"q": "first"})
    v2 = mcp_approval.evaluate_call(SID, TOOL, session_id="s2", session_factory=sf, now=1100.0,
                                    arguments={"q": "other-session"})
    assert v2["request_id"] != v1["request_id"]
    # A later call in the SAME session does not overwrite the pending args.
    v3 = mcp_approval.evaluate_call(SID, TOOL, session_id="s1", session_factory=sf, now=1200.0,
                                    arguments={"q": "second"})
    assert v3["request_id"] == v1["request_id"] and v3["reason"] == "approval_pending"
    items = {i["request_id"]: i for i in mcp_approval.list_pending(session_factory=sf, now=1250.0)["items"]}
    assert items[v1["request_id"]]["arguments"] == {"q": "first"}
    assert items[v2["request_id"]]["arguments"] == {"q": "other-session"}


def test_decide_expired_and_legacy_monotonic_row_expired(sf):
    v = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    r = mcp_approval.decide_approval(v["request_id"], "allow", session_factory=sf, now=1000.0 + 301.0)
    assert r["ok"] is False and r["error"] == "request_expired"
    # Deciding again reports not-pending (expired), never a resurrection.
    r2 = mcp_approval.decide_approval(v["request_id"], "allow", session_factory=sf, now=1000.0 + 302.0)
    assert r2["ok"] is False and "request_not_pending" in r2["error"]

    # Legacy row written with a monotonic timestamp (pre-W0): wall-clock age is
    # huge, so decide treats it as expired even though it looks "fresh" in
    # monotonic terms.
    from app.db.models import McpApprovalRequestRow

    with sf() as session:
        session.add(McpApprovalRequestRow(
            id="legacy-row", server_id=SID, tool_name=TOOL, status="pending",
            requested_at=123456.0,  # monotonic seconds, not wall time
        ))
        session.commit()
    r3 = mcp_approval.decide_approval("legacy-row", "allow", session_factory=sf)
    assert r3["ok"] is False and r3["error"] == "request_expired"


def test_decide_scope_requires_attribution(sf):
    admin = mcp_approval.Actor(user_id="admin", is_admin=True)
    v = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    r = mcp_approval.decide_approval(v["request_id"], "allow", scope="session",
                                     session_factory=sf, now=1100.0, actor=admin)
    assert r["ok"] is False and r["error"] == "scope_requires_session"
    r = mcp_approval.decide_approval(v["request_id"], "allow", scope="project",
                                     session_factory=sf, now=1100.0, actor=admin)
    assert r["ok"] is False and r["error"] == "scope_requires_project"
    # Global scope is admin-only even when attribution is absent.
    stranger = mcp_approval.Actor(user_id="mallory")
    r = mcp_approval.decide_approval(v["request_id"], "allow", scope="global",
                                     session_factory=sf, now=1100.0, actor=stranger)
    assert r["ok"] is False and r["error"] == "not_authorized"


def test_decide_attribution_member_session_and_stranger(sf):
    from app.db.models import ChatSessionRow

    with sf() as session:
        session.add(ChatSessionRow(id="chat-1", project_id="p1"))
        session.commit()
    admin = mcp_approval.Actor(user_id="admin", is_admin=True)
    member = mcp_approval.Actor(user_id="u1", project_ids=frozenset({"p1"}))
    stranger = mcp_approval.Actor(user_id="mallory", project_ids=frozenset({"p2"}))

    # Direct project attribution: member decides, stranger cannot.
    v = mcp_approval.evaluate_call(SID, TOOL, project_id="p1", session_factory=sf, now=1000.0)
    r = mcp_approval.decide_approval(v["request_id"], "deny", session_factory=sf, now=1100.0, actor=stranger)
    assert r["ok"] is False and r["error"] == "not_authorized"
    r = mcp_approval.decide_approval(v["request_id"], "deny", session_factory=sf, now=1100.0, actor=member)
    assert r["ok"] is True

    # Session attribution via the chat session's project: same rule.
    v2 = mcp_approval.evaluate_call(SID, TOOL, session_id="chat-1", session_factory=sf, now=1000.0)
    r = mcp_approval.decide_approval(v2["request_id"], "deny", session_factory=sf, now=1100.0, actor=stranger)
    assert r["ok"] is False and r["error"] == "not_authorized"
    r = mcp_approval.decide_approval(v2["request_id"], "deny", session_factory=sf, now=1100.0, actor=member)
    assert r["ok"] is True

    # Unattributed requests are admin-only.
    v3 = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    r = mcp_approval.decide_approval(v3["request_id"], "deny", session_factory=sf, now=1100.0, actor=member)
    assert r["ok"] is False and r["error"] == "not_authorized"
    r = mcp_approval.decide_approval(v3["request_id"], "deny", session_factory=sf, now=1100.0, actor=admin)
    assert r["ok"] is True


def test_deny_ignores_scope_validation_but_keeps_attribution(sf):
    member = mcp_approval.Actor(user_id="u1", project_ids=frozenset({"p1"}))
    stranger = mcp_approval.Actor(user_id="mallory", project_ids=frozenset({"p2"}))
    # Project-only request, denied with scope=session: scope is meaningless for
    # deny, so the member's deny succeeds; the stranger's still fails.
    v = mcp_approval.evaluate_call(SID, TOOL, project_id="p1", session_factory=sf, now=1000.0)
    r = mcp_approval.decide_approval(v["request_id"], "deny", scope="session",
                                     session_factory=sf, now=1100.0, actor=stranger)
    assert r["ok"] is False and r["error"] == "not_authorized"
    r = mcp_approval.decide_approval(v["request_id"], "deny", scope="session",
                                     session_factory=sf, now=1100.0, actor=member)
    assert r["ok"] is True


def test_list_pending_filtered_by_actor(sf):
    member = mcp_approval.Actor(user_id="u1", project_ids=frozenset({"p1"}))
    stranger = mcp_approval.Actor(user_id="mallory", project_ids=frozenset({"p2"}))
    admin = mcp_approval.Actor(user_id="admin", is_admin=True)
    mcp_approval.evaluate_call(SID, TOOL, project_id="p1", session_factory=sf, now=1000.0)
    mcp_approval.evaluate_call(SID, TOOL, project_id="p2", session_factory=sf, now=1000.0)
    assert mcp_approval.list_pending(session_factory=sf, now=1100.0, actor=member)["total"] == 1
    assert mcp_approval.list_pending(session_factory=sf, now=1100.0, actor=stranger)["total"] == 1
    assert mcp_approval.list_pending(session_factory=sf, now=1100.0, actor=admin)["total"] == 2
    # Unfiltered (internal) listing still sees everything.
    assert mcp_approval.list_pending(session_factory=sf, now=1100.0)["total"] == 2


def test_decide_writes_hash_chain_audit(sf):
    from app.services.audit.hash_chain import verify_chain
    from app.db.models_plm import AuditLogRow

    admin = mcp_approval.Actor(user_id="admin", is_admin=True)
    v = mcp_approval.evaluate_call(SID, TOOL, session_factory=sf, now=1000.0)
    r = mcp_approval.decide_approval(v["request_id"], "allow", scope="global",
                                     session_factory=sf, now=1100.0, actor=admin)
    assert r["ok"] is True
    with sf() as session:
        rows = session.query(AuditLogRow).filter(AuditLogRow.action == "mcp.approval.decided").all()
        assert len(rows) == 1
        row = rows[0]
        assert row.actor == "admin" and row.entity_id == v["request_id"]
        assert row.detail["decision"] == "allow" and row.detail["scope"] == "global"
        assert row.detail["tool_name"] == TOOL
        assert verify_chain(session).ok is True
