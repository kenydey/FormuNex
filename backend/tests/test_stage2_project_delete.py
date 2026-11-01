"""Stage 2 / SR-035 (D-10): project deletion is a true delete.

Every project-owned row goes; the project row itself is deleted (not
archived). Company-level assets (products, formulation versions) keep
existing with their project pointer cleared; global wiki rows stay.
"""
from __future__ import annotations

from app.db.database import make_engine, make_session_factory
from app.db.models import (
    ChatSessionRow,
    DOEPlanRow,
    ProjectPayloadHistoryRow,
    ProjectRow,
    SourceDocument,
    WikiPage,
)
from app.db.models_identity import ProjectMemberRow, RoleRow, UserRow
from app.db.models_lab import LabTaskRow
from app.db.models_plm import ProductRow
from app.db.project_store import ProjectStore


def _seed(tmp_path):
    db_url = f"sqlite:///{(tmp_path / 'del.db').as_posix()}"
    engine = make_engine(db_url)
    store = ProjectStore(make_session_factory(engine))
    detail = store.create(title="doomed")
    pid = detail.id
    with make_session_factory(engine)() as s:
        s.add(UserRow(id="u1", username="u1", is_active=True))
        s.add(RoleRow(code="project_lead", name_zh="x", name_en="x",
                      permissions=["project.write"], builtin=True))
        # Same-flush parent/child FK pitfall (see AGENTS.md): flush the
        # referenced rows before adding the member row.
        s.flush()
        s.add(SourceDocument(id="doc1", project_id=pid, title="d", content_hash="h"))
        s.add(ChatSessionRow(id="sess1", project_id=pid))
        s.add(DOEPlanRow(id="plan1", project_id=pid, design_type="lhs"))
        s.add(LabTaskRow(id="task1", project_id=pid, task_no="T1", status="pending"))
        s.add(ProjectPayloadHistoryRow(project_id=pid, version=1, payload={}))
        s.add(WikiPage(id="wiki1", path="w1", project_id=pid, title="w"))
        s.add(WikiPage(id="wiki-global", path="wg", project_id=None, title="g"))
        s.add(ProjectMemberRow(project_id=pid, user_id="u1", role_code="project_lead"))
        s.add(ProductRow(id="prod1", code="PR1", name_zh="x", name_en="x", project_id=pid))
        s.commit()
    return store, engine, pid


def test_delete_removes_owned_rows_and_project_itself(tmp_path):
    store, engine, pid = _seed(tmp_path)
    result = store.delete(pid)
    assert result is not None
    assert result["lab_task_count"] == 1
    assert result["doe_plan_count"] == 1
    assert result["products_unlinked"] == 1
    with make_session_factory(engine)() as s:
        assert s.get(ProjectRow, pid) is None
        for model, key in ((SourceDocument, "doc1"), (ChatSessionRow, "sess1"),
                           (DOEPlanRow, "plan1"), (LabTaskRow, "task1"),
                           (WikiPage, "wiki1")):
            assert s.get(model, key) is None, model.__name__
        assert s.query(ProjectPayloadHistoryRow).filter_by(project_id=pid).count() == 0
        assert s.query(ProjectMemberRow).filter_by(project_id=pid).count() == 0
        # Global wiki row and the product itself survive; only the pointer goes.
        assert s.get(WikiPage, "wiki-global") is not None
        prod = s.get(ProductRow, "prod1")
        assert prod is not None and prod.project_id is None


def test_delete_missing_project_returns_none(tmp_path):
    store, _engine, _pid = _seed(tmp_path)
    assert store.delete("no-such-project") is None
