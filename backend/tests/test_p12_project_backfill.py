"""P1-2: project-attribution backfill + cross-project invisibility gaps.

Backfill rules mirror migration 0033 (non-empty parent only, exactly-one
project for wiki sources); unattributable rows keep NULL and are listed,
never silently defaulted.
"""
from __future__ import annotations

import pytest

from app.db.database import Base, make_engine, make_session_factory
from app.db.models import (
    Campaign,
    ChatMessageRow,
    ChatSessionRow,
    DOEPlanRow,
    ExperimentRow,
    McpApprovalRequestRow,
    SourceDocument,
    WikiPage,
)
from app.db.models_lab import LabTaskRow
from app.db.project_store import _chat_mirror
from app.services import project_backfill


@pytest.fixture()
def engine(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/p12.db")
    Base.metadata.create_all(engine)
    return engine


def _seed_legacy(session):
    """Legacy NULL rows: some attributable, some not."""
    camp_a = Campaign(name="camp-a", project_id="proj-A")
    camp_empty = Campaign(name="camp-empty", project_id="")
    session.add_all([camp_a, camp_empty])
    session.flush()
    exp_a = ExperimentRow(domain="surface_treatment", project_id="proj-A", measured={})
    session.add(exp_a)
    session.flush()
    # doe_plans: via campaign / via experiment / orphan / empty-parent
    plans = {
        "via-camp": DOEPlanRow(id="plan-camp", campaign_id=camp_a.id, project_id=None,
                               design_type="lhs", parameters={}),
        "via-exp": DOEPlanRow(id="plan-exp", experiment_id=exp_a.id, project_id=None,
                              design_type="lhs", parameters={}),
        "orphan": DOEPlanRow(id="plan-orphan", project_id=None,
                             design_type="lhs", parameters={}),
        "empty-parent": DOEPlanRow(id="plan-empty", campaign_id=camp_empty.id, project_id=None,
                                   design_type="lhs", parameters={}),
    }
    session.add_all(plans.values())
    # chat: sessions in A and B; messages NULL (mirror the session)
    session.add_all([
        ChatSessionRow(id="sess-A", project_id="proj-A"),
        ChatSessionRow(id="sess-B", project_id="proj-B"),
        ChatMessageRow(id="msg-A", session_id="sess-A", project_id=None, content="hello A"),
        ChatMessageRow(id="msg-B", session_id="sess-B", project_id=None, content="hello B"),
        ChatMessageRow(id="msg-orphan", session_id="sess-gone", project_id=None),
    ])
    # lab task via the campaign-attributed plan (filled after doe_plans)
    session.add(LabTaskRow(id="task-1", task_no="T-1", doe_plan_id="plan-camp", project_id=None))
    # mcp approval via session A; one with no session
    session.add_all([
        McpApprovalRequestRow(id="mcp-A", server_id="s", tool_name="t",
                              session_id="sess-A", project_id=None, requested_at=0.0),
        McpApprovalRequestRow(id="mcp-none", server_id="s", tool_name="t",
                              session_id=None, project_id=None, requested_at=0.0),
    ])
    # wiki: single-project sources / multi-project / unknown source
    session.add_all([
        SourceDocument(id="doc-A", project_id="proj-A", content_hash="h-A"),
        SourceDocument(id="doc-B", project_id="proj-B", content_hash="h-B"),
        WikiPage(id="wiki-A", path="/a", source_ids=["doc-A"], project_id=None),
        WikiPage(id="wiki-mixed", path="/mixed",
                 source_ids=["doc-A", "doc-B"], project_id=None),
        WikiPage(id="wiki-unknown", path="/unknown",
                 source_ids=["doc-gone"], project_id=None),
    ])
    session.commit()
    return plans


def test_audit_lists_attributable_and_unattributable(engine):
    factory = make_session_factory(engine)
    with factory() as session:
        _seed_legacy(session)
    with engine.begin() as conn:
        report = project_backfill.audit(conn)
    assert report["doe_plans"]["null_rows"] == 4
    assert report["doe_plans"]["attributable"] == 2
    assert set(report["doe_plans"]["unattributable_ids"]) == {"plan-orphan", "plan-empty"}
    assert report["chat_messages"]["attributable"] == 2
    assert report["chat_messages"]["unattributable_ids"] == ["msg-orphan"]
    assert report["lab_tasks"]["attributable"] == 1
    assert report["mcp_approval_requests"]["attributable"] == 1
    assert report["wiki_pages"]["attributable"] == 1
    assert set(report["wiki_pages"]["unattributable_ids"]) == {"wiki-mixed", "wiki-unknown"}


def test_apply_fills_attributable_only_and_is_idempotent(engine):
    factory = make_session_factory(engine)
    with factory() as session:
        _seed_legacy(session)
    with engine.begin() as conn:
        first = project_backfill.apply_backfill(conn)
    assert first["doe_plans"]["filled"] == 2
    assert first["lab_tasks"]["filled"] == 1
    assert first["wiki_pages"]["filled"] == 1
    with factory() as session:
        assert session.get(DOEPlanRow, "plan-camp").project_id == "proj-A"
        assert session.get(DOEPlanRow, "plan-exp").project_id == "proj-A"
        assert session.get(DOEPlanRow, "plan-orphan").project_id is None
        assert session.get(DOEPlanRow, "plan-empty").project_id is None
        assert session.get(LabTaskRow, "task-1").project_id == "proj-A"
        assert session.get(ChatMessageRow, "msg-A").project_id == "proj-A"
        assert session.get(ChatMessageRow, "msg-orphan").project_id is None
        assert session.get(WikiPage, "wiki-A").project_id == "proj-A"
        assert session.get(WikiPage, "wiki-mixed").project_id is None
    with engine.begin() as conn:
        second = project_backfill.apply_backfill(conn)
    assert all(v["filled"] == 0 for v in second.values())


def test_audit_table_filter(engine):
    factory = make_session_factory(engine)
    with factory() as session:
        _seed_legacy(session)
    with engine.begin() as conn:
        report = project_backfill.audit(conn, tables=["doe_plans"])
    assert set(report) == {"doe_plans"}


def test_chat_mirror_excludes_other_project_and_null(engine):
    """Gap coverage: project detail mirrors only that project's messages."""
    factory = make_session_factory(engine)
    with factory() as session:
        _seed_legacy(session)
    # Mirror reads the stored project_id, so attribute first.
    with engine.begin() as conn:
        project_backfill.apply_backfill(conn)
    with factory() as session:
        mirror_a = _chat_mirror(session, "proj-A")
        mirror_b = _chat_mirror(session, "proj-B")
        assert mirror_a == [{"role": "user", "content": "hello A"}]
        assert mirror_b == [{"role": "user", "content": "hello B"}]
        # The orphan (NULL project) appears in neither mirror.
        assert session.query(ChatMessageRow).filter(
            ChatMessageRow.project_id.is_(None)).count() == 1
