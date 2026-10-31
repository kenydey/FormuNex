"""W1-5: author-attribution gap in document review.

A document with ``created_by`` empty (service API key generation) must
not pass the natural-person review path: the reviewer cannot be told
apart from the author. It can only be approved through the audited
exemption path, with a reason, a re-authentication and an audit
record. Documents WITH an author keep the old self-review refusal.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models_plm import AuditLogRow, ProductRevisionRow, ProductRow, RegulatoryDocumentRow
from app.services.identity.rbac import Principal
from app.services.plm.release import esign, signoff


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def _stub_esign(monkeypatch):
    monkeypatch.setattr(esign, "sign", lambda session, **kw: SimpleNamespace(id="sig-1"))


def _principal():
    return Principal(user_id="u1", global_permissions=frozenset({"plm.document.review"}),
                     project_permissions={})


_REV_SEQ = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _doc(session, *, created_by):
    if session.get(ProductRow, "prod-x") is None:
        session.add(ProductRow(id="prod-x", code="P-1", name_zh="产品"))
    rev = ProductRevisionRow(id=str(uuid.uuid4()), product_id="prod-x", rev=next(_REV_SEQ), state="draft")
    session.add(rev)
    session.flush()
    doc = RegulatoryDocumentRow(id=str(uuid.uuid4()), revision_id=rev.id, doc_type="sds", lang="zh-CN",
                                regulation="CN", version_no=1, status="draft",
                                content={"meta": {}, "validation": {}},
                                content_hash="h", created_by=created_by)
    session.add(doc)
    session.flush()
    return doc


def test_unattributed_doc_refused_on_normal_path(session):
    doc = _doc(session, created_by=None)
    with pytest.raises(signoff.Forbidden, match="无编制人归属"):
        signoff.approve_document(session, doc.id, user_id="u1", principal=_principal(), password="x")
    assert session.get(RegulatoryDocumentRow, doc.id).status == "draft"


def test_self_review_still_refused(session):
    doc = _doc(session, created_by="u1")
    with pytest.raises(signoff.Forbidden, match="不能审核自己"):
        signoff.approve_document(session, doc.id, user_id="u1", principal=_principal(), password="x")


def test_other_author_approves_normally(session):
    doc = _doc(session, created_by="u2")
    row = signoff.approve_document(session, doc.id, user_id="u1", principal=_principal(), password="x")
    assert row.status == "approved"


def test_exemption_approves_unattributed_doc_with_audit_trail(session):
    doc = _doc(session, created_by=None)
    row = signoff.approve_document_exempt(session, doc.id, user_id="u1", principal=_principal(),
                                          password="x", reason="服务 key 生成，EHS 已线下确认编制人")
    assert row.status == "approved"
    assert row.content["meta"]["approval_exemption"]["by"] == "u1"
    assert "服务 key" in row.content["meta"]["approval_exemption"]["reason"]
    audit = session.execute(select(AuditLogRow).where(
        AuditLogRow.action == "document.approve_exempt",
        AuditLogRow.entity_id == doc.id)).first()
    assert audit is not None


def test_exemption_refuses_attributed_doc_and_empty_reason(session):
    doc = _doc(session, created_by="u2")
    with pytest.raises(signoff.ReleaseError, match="正常审核路径"):
        signoff.approve_document_exempt(session, doc.id, user_id="u1", principal=_principal(),
                                        password="x", reason="r")
    doc2 = _doc(session, created_by=None)
    with pytest.raises(signoff.ReleaseError, match="必须说明理由"):
        signoff.approve_document_exempt(session, doc2.id, user_id="u1", principal=_principal(),
                                        password="x", reason="  ")
