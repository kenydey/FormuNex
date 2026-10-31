"""W1-3: a raising check becomes a fail-closed gate item, never a 500.

Covers the three cases in the report: a broken rule-set file (here
simulated by a raising classification step), a broken sign-off yaml
(simulated by a raising document-selection step), and a raising
classifier (the objectives step). In every case ``evaluate`` returns
``passed=False`` so ``submit`` still audits ``release.gate_failed``.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models_plm import ProductRevisionRow
from app.services.plm.release import gate


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _rev(session):
    rev = ProductRevisionRow(id=str(uuid.uuid4()), product_id="prod-x", rev="A",
                             spec_snapshot={})
    session.add(rev)
    session.flush()
    return rev


def _check(result, key):
    return next(c for c in result["checks"] if c["key"] == key)


def test_raising_check_becomes_fail_item_not_500(session, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("classifier exploded")

    monkeypatch.setattr(gate, "_check_objectives", boom)
    result = gate.evaluate(session, _rev(session).id)
    assert result["passed"] is False
    item = next(i for i in _check(result, "objectives")["items"] if i["key"] == "evaluation_error")
    assert "RuntimeError" in item["message"] and "classifier exploded" in item["message"]
    assert item["waivable"] is False


def test_document_selection_failure_fails_all_doc_dependent_checks(session, monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("sign-off yaml is not a mapping")

    monkeypatch.setattr(gate, "select_package_documents", boom)
    result = gate.evaluate(session, _rev(session).id)
    assert result["passed"] is False
    for key in ("documents", "consistency", "sds_label", "required_fields"):
        check = _check(result, key)
        assert check["status"] == "fail"
        assert any(i["key"] == "evaluation_error" for i in check["items"])


def test_classification_failure_also_fails_data_gaps(session, monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("rule-set file missing")

    monkeypatch.setattr(gate, "_check_classification", boom)
    result = gate.evaluate(session, _rev(session).id)
    assert result["passed"] is False
    assert _check(result, "classification_current")["status"] == "fail"
    gaps = _check(result, "data_gaps")
    assert gaps["status"] == "fail"
    assert any(i["key"] == "evaluation_error" for i in gaps["items"])
