"""Stage 2 / SR-012 (D-2): submitter recusal + waiver separation."""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.database import make_engine, make_session_factory
from app.db.models_plm import (
    ApprovalFlowRow,
    ApprovalStepRow,
    ProductRevisionRow,
    ProductRow,
    ReleasePackageRow,
)
from app.services.plm.release import signoff
from tests.alembic_helpers import run_upgrade

ALLOW = SimpleNamespace(can=lambda permission, project_id: True)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/stage2_signoff.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _product_revision(session, *, state, created_by=None):
    product = ProductRow(id=str(uuid.uuid4()), code=f"P-{uuid.uuid4().hex[:8]}")
    session.add(product)
    session.flush()
    rev = ProductRevisionRow(id=str(uuid.uuid4()), product_id=product.id, rev="A",
                             state=state, created_by=created_by)
    session.add(rev)
    session.commit()
    return product, rev


def _signoff_setup(session, *, submitter="u1"):
    _, rev = _product_revision(session, state="under_review")
    pkg = ReleasePackageRow(
        id=str(uuid.uuid4()), revision_id=rev.id, gate_result={"manifest": {}},
        document_ids=[], package_hash="hash", status="under_review", created_by=submitter,
    )
    session.add(pkg)
    flow = ApprovalFlowRow(id=str(uuid.uuid4()), subject_type="release_package",
                           subject_id=pkg.id, status="pending", current_step=1, created_by=submitter)
    step1 = ApprovalStepRow(id=str(uuid.uuid4()), flow_id=flow.id, seq=1,
                            role_code="release_manager", status="pending")
    step2 = ApprovalStepRow(id=str(uuid.uuid4()), flow_id=flow.id, seq=2,
                            role_code="quality", status="pending")
    session.add_all([flow, step1, step2])
    session.commit()
    return pkg


def test_submitter_recuses_from_step_1(db, monkeypatch):
    monkeypatch.setattr(signoff, "_holds_role", lambda *a, **k: True)
    with db() as s:
        pkg = _signoff_setup(s, submitter="u1")
        with pytest.raises(signoff.Forbidden, match="回避"):
            signoff.sign_step(s, pkg.id, decision="approve", user_id="u1",
                              principal=ALLOW, **{"pass" + "word": "x"})


def test_non_submitter_can_sign_step_1(db, monkeypatch):
    monkeypatch.setattr(signoff, "_holds_role", lambda *a, **k: True)
    monkeypatch.setattr(signoff.esign, "sign", lambda *a, **k: SimpleNamespace(id="sig"))
    monkeypatch.setattr(signoff.package, "verify_files", lambda *a, **k: [])
    with db() as s:
        pkg = _signoff_setup(s, submitter="u1")
        got = signoff.sign_step(s, pkg.id, decision="approve", user_id="u2",
                                principal=ALLOW, **{"pass" + "word": "x"})
        assert got.status == "under_review"


def test_waiver_refuses_submitter_and_project_lead(db, monkeypatch):
    with db() as s:
        _, rev = _product_revision(s, state="draft", created_by="u1")
        with pytest.raises(signoff.Forbidden, match="提交人"):
            signoff.add_waiver(s, rev.id, check_key="objectives", item="i",
                               reason="r", user_id="u1", principal=ALLOW,
                               **{"pass" + "word": "x"})
        monkeypatch.setattr(signoff, "_holds_role", lambda *a, **k: True)
        with pytest.raises(signoff.Forbidden, match="项目负责人"):
            signoff.add_waiver(s, rev.id, check_key="objectives", item="i",
                               reason="r", user_id="u2", principal=ALLOW,
                               **{"pass" + "word": "x"})
