"""W1-4 concurrency hardening.

- (step, user, meaning) signatures are unique: a double sign races
  into IntegrityError instead of a duplicate signature row.
- At most one under_review package per revision (partial unique
  index): a second submit races into IntegrityError, while a
  rejected package still lets its revision resubmit.
- Chunk replace retries inside a SAVEPOINT: the caller's transaction
  (e.g. its SourceDocument row) survives the retry.
- Auto-coded product creation retries inside a SAVEPOINT too: no
  fresh-session requirement, and the caller's pending work survives.
- Failed document generation deletes the renders it already wrote.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.chunk_store import ChunkStore
from app.db.database import Base
from app.db.models import SourceDocument
from app.db.models_plm import (
    ESignatureRow,
    ProductRevisionRow,
    ProductRow,
    RegulatoryDocumentRow,
    ReleasePackageRow,
)
from app.services.plm import coding as coding_mod
from app.services.plm import storage as storage_mod
from app.services.plm.documents import service as docs_service


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_esignature_step_user_meaning_unique(session):
    row = ESignatureRow(id=str(uuid.uuid4()), user_id="u1", step_id="step-1",
                        subject_type="release_package", subject_id="pkg-1",
                        meaning="approve", signed_hash="h")
    session.add(row)
    session.flush()
    session.add(ESignatureRow(id=str(uuid.uuid4()), user_id="u1", step_id="step-1",
                              subject_type="release_package", subject_id="pkg-1",
                              meaning="approve", signed_hash="h"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_esignature_null_step_rows_stay_distinct(session):
    # NULL step_id (document review, withdraw) must not collide: both
    # SQLite and Postgres treat NULLs as distinct in unique constraints.
    for i in range(2):
        session.add(ESignatureRow(id=str(uuid.uuid4()), user_id="u1", step_id=None,
                                  subject_type="regulatory_document", subject_id=f"doc-{i}",
                                  meaning="review", signed_hash="h"))
    session.flush()  # must not raise


def test_active_package_per_revision_partial_unique(session):
    session.add(ReleasePackageRow(id="pkg-1", revision_id="rev-1", status="under_review"))
    session.add(ReleasePackageRow(id="pkg-2", revision_id="rev-1", status="rejected"))
    session.flush()
    # Resubmission after rejection is fine (rejected is not active)...
    session.add(ReleasePackageRow(id="pkg-3", revision_id="rev-1", status="under_review"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_chunk_replace_retry_preserves_caller_transaction(session, monkeypatch):
    store = ChunkStore(sessionmaker(bind=session.get_bind()))
    caller_doc = SourceDocument(id="src-1", content_hash="h" * 64)
    session.add(caller_doc)  # pending on purpose: the pre-flush must not lose it
    calls = {"n": 0}
    real_inner = ChunkStore._replace_for_source_inner

    def flaky_inner(self, s, source_id, chunks):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("INSERT", {}, Exception("unique"))
        return 3

    monkeypatch.setattr(ChunkStore, "_replace_for_source_inner", flaky_inner)
    assert store.replace_for_source_in(session, "src-1", []) == 3
    assert calls["n"] == 2
    # The old session.rollback() retry would have discarded this row.
    session.commit()
    assert session.get(SourceDocument, "src-1") is not None


def test_chunk_replace_success_keeps_outer_rollback_and_commit_semantics(session):
    """W1-4 savepoint discipline: the success path must not release the
    savepoint (on this stack RELEASE makes a later outer rollback a
    no-op), so outer rollback still undoes the replace and outer
    commit still persists it."""
    from app.db.models import DocumentChunk

    store = ChunkStore(sessionmaker(bind=session.get_bind()))
    chunks = [{"text": "line one", "heading_path": "", "page_no": None,
               "paragraph_idx": 0, "offset_start": 0, "offset_end": 8}]
    store.replace_for_source_in(session, "src-rb", chunks)
    session.rollback()
    assert session.query(DocumentChunk).filter_by(source_id="src-rb").count() == 0
    store.replace_for_source_in(session, "src-ok", chunks)
    session.commit()
    assert session.query(DocumentChunk).filter_by(source_id="src-ok").count() == 1


def test_auto_code_retry_preserves_caller_work(session, monkeypatch):
    manual = ProductRow(id=str(uuid.uuid4()), code="MANUAL-1", name_zh="手动")
    session.add(manual)  # pending on purpose
    session.add(ProductRow(id=str(uuid.uuid4()), code="AUTO-001", name_zh="占位"))
    session.flush()
    codes = iter(["AUTO-001", "AUTO-002"])
    monkeypatch.setattr(coding_mod, "default_company_rules", lambda s: {"categories": ["X"]})
    monkeypatch.setattr(coding_mod, "generate_product_code", lambda s, rules, cat: next(codes))
    row = docs_service.create_product(session, name_zh="新品", category_identifier="X")
    assert row.code == "AUTO-002"
    session.commit()
    assert session.get(ProductRow, manual.id) is not None
    assert session.execute(select(ProductRow.id).where(ProductRow.code == "MANUAL-1")).first() is not None


def test_failed_generation_deletes_written_renders(session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage_mod, "get_settings",
                        lambda: SimpleNamespace(plm_storage_dir=str(tmp_path)))
    rev = ProductRevisionRow(id=str(uuid.uuid4()), product_id="prod-x", rev="A", state="draft")
    session.add(rev)
    session.flush()
    monkeypatch.setattr(docs_service, "build_content",
                        lambda s, r, dt, lang, payload, regulation: (
                            {"meta": {}, "validation": {"warnings": []}}, {}))
    monkeypatch.setattr(docs_service, "render_pdf", lambda c, pdfa=False: (b"%PDF", {"font_embedded": True}))
    monkeypatch.setattr(docs_service, "render_docx", lambda c, t: b"docx")
    monkeypatch.setattr(docs_service, "field_sources", lambda c: {})
    monkeypatch.setattr(docs_service, "content_hash", lambda c: "h")
    real_put = storage_mod.put

    def failing_put(key, data):
        if key.endswith(".pdf"):
            raise RuntimeError("disk full on pdf")
        return real_put(key, data)

    monkeypatch.setattr(storage_mod, "put", failing_put)
    with pytest.raises(RuntimeError, match="disk full"):
        docs_service.generate_document(session, rev.id, "sds", lang="zh-CN", payload={})
    leftovers = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert leftovers == []
    assert session.execute(select(RegulatoryDocumentRow.id)).first() is None


def test_cleanup_logs_undeletable_keys_instead_of_masking(caplog, monkeypatch):
    import logging

    with caplog.at_level(logging.ERROR, logger=docs_service.__name__):
        docs_service._cleanup_rendered_keys([None, "missing.docx"])
    # Missing keys are skipped silently; a delete failure must be logged.
    calls = {"n": 0}

    def boom(key):
        calls["n"] += 1
        raise OSError("read-only fs")

    monkeypatch.setattr(storage_mod, "delete", boom)
    with caplog.at_level(logging.ERROR, logger=docs_service.__name__):
        docs_service._cleanup_rendered_keys(["stuck.pdf"])
    assert calls["n"] == 1
    assert any("reclamation" in r.message for r in caplog.records)
