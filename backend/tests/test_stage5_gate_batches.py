"""Stage 5 / SR-091: gate batches resolve with one IN query, not one get per batch."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models_plm import BatchRow, ProductRevisionRow
from app.services.plm.release import gate


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class _Doc:
    def __init__(self, batch_id):
        self.id = str(uuid.uuid4())
        self.doc_type = "sds"
        self.lang = "zh-CN"
        self.version_no = 1
        self.status = "draft"
        self.batch_id = batch_id
        self.regulation = "CN"


def _rev(session):
    rev = ProductRevisionRow(id=str(uuid.uuid4()), product_id="prod-x", rev="A", spec_snapshot={})
    session.add(rev)
    session.flush()
    return rev


def test_batches_keep_only_existing_ids(session, monkeypatch):
    session.add(BatchRow(id="b1", product_id="prod-x", batch_no="B1"))
    session.flush()
    docs = [_Doc("b1"), _Doc("b-gone"), _Doc(None)]
    monkeypatch.setattr(gate, "select_package_documents", lambda s, r: (docs, []))
    result = gate.evaluate(session, _rev(session).id)
    assert result["batches"] == ["b1"]


def test_batches_empty_set_issues_no_batch_query(session, monkeypatch):
    monkeypatch.setattr(gate, "select_package_documents", lambda s, r: ([_Doc(None)], []))
    queries = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        if "batches" in statement:
            queries.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", _before)
    try:
        result = gate.evaluate(session, _rev(session).id)
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", _before)
    assert result["batches"] == []
    assert queries == []
