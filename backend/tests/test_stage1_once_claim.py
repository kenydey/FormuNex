"""Stage 1 / SR-050: a ``once`` allow grant is consumed exactly once.

The old get-then-delete let two concurrent evaluations both see the
row and both allow. The claim is now one conditional DELETE; the
second evaluation finds the grant spent and falls through to approval
instead of allowing. True multi-process contention cannot be
reproduced on sandbox SQLite, so this pins the consume-once contract
sequentially: first call allows and deletes, second call denies.
"""
from __future__ import annotations

import pytest

from app.db.database import Base, make_engine, make_session_factory
from app.db.models import McpToolPolicyRow
from app.services import mcp_approval


@pytest.fixture()
def sf(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/once.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()


def test_once_allow_consumed_by_first_call_only(sf):
    mcp_approval.set_policy("srv1", "read_file", "allow", "once", session_factory=sf)
    first = mcp_approval.evaluate_call("srv1", "read_file", session_factory=sf, now=1000.0)
    assert first["verdict"] == "allow"
    with sf() as s:
        assert s.get(McpToolPolicyRow, ("srv1", "read_file", "once")) is None
    second = mcp_approval.evaluate_call("srv1", "read_file", session_factory=sf, now=1001.0)
    assert second["verdict"] == "deny"
    assert second["reason"] == "approval_required"


def test_once_allow_without_policy_still_asks(sf):
    v = mcp_approval.evaluate_call("srv1", "read_file", session_factory=sf, now=1000.0)
    assert v["verdict"] == "deny" and v["reason"] == "approval_required"
