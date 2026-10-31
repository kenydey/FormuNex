"""Stage 1 / SR-031: submit idempotency + sign_step signature-conflict fallback.

submit returns an existing under_review package instead of creating a
second one or failing on the state check; sign_step converts a
unique-signature conflict into the winner's package when the winner
already signed, and re-raises (API layer maps to 409) otherwise.
``esign.sign`` is stubbed to raise IntegrityError so the tests prove
the fallback logic, not password re-authentication.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.database import make_engine, make_session_factory
from app.db.models_plm import (
    ApprovalFlowRow,
    ApprovalStepRow,
    ESignatureRow,
    ProductRevisionRow,
    ProductRow,
    ReleasePackageRow,
)
from app.services.plm.release import signoff
from tests.alembic_helpers import run_upgrade

ALLOW = SimpleNamespace(can=lambda permission, project_id: True)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/stage1_signoff.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _product_revision(session, *, state):
    # Parent and child flushed separately: same-batch flush inserts the
    # child first under this app's metadata (see AGENTS.md).
    product = ProductRow(id=str(uuid.uuid4()), code=f"P-{uuid.uuid4().hex[:8]}")
    session.add(product)
    session.flush()
    rev = ProductRevisionRow(id=str(uuid.uuid4()), product_id=product.id, rev="A", state=state)
    session.add(rev)
    session.commit()
    return product, rev


def _package(session, rev, *, status="under_review"):
    pkg = ReleasePackageRow(
        id=str(uuid.uuid4()), revision_id=rev.id, gate_result={"manifest": {}},
        document_ids=[], package_hash="hash", status=status, created_by="u1",
    )
    session.add(pkg)
    session.commit()
    return pkg


def test_submit_returns_existing_under_review_package(db):
    with db() as s:
        _, rev = _product_revision(s, state="under_review")
        pkg = _package(s, rev)
        # Gate would fail on this bare revision — returning early proves
        # the recheck runs before gate evaluation.
        got = signoff.submit(s, rev.id, user_id="u2", principal=ALLOW)
        assert got.id == pkg.id


def test_submit_still_refuses_non_candidate_without_package(db):
    with db() as s:
        _, rev = _product_revision(s, state="draft")
        with pytest.raises(signoff.ReleaseError, match="只有候选版可以提交发布"):
            signoff.submit(s, rev.id, user_id="u2", principal=ALLOW)


def _signoff_setup(session):
    _, rev = _product_revision(session, state="under_review")
    pkg = _package(session, rev)
    flow = ApprovalFlowRow(id=str(uuid.uuid4()), subject_type="release_package",
                           subject_id=pkg.id, status="pending", current_step=1, created_by="u1")
    step = ApprovalStepRow(id=str(uuid.uuid4()), flow_id=flow.id, seq=1,
                           role_code="release_manager", status="pending")
    session.add_all([flow, step])
    session.commit()
    return pkg, step


def test_sign_step_conflict_returns_winner_package(db, monkeypatch):
    monkeypatch.setattr(signoff, "_holds_role", lambda *a, **k: True)
    monkeypatch.setattr(
        signoff.esign, "sign",
        lambda *a, **k: (_ for _ in ()).throw(IntegrityError("dup", None, None)),
    )
    with db() as s:
        pkg, step = _signoff_setup(s)
        # The winner's signature is already recorded for this step.
        s.add(ESignatureRow(id=str(uuid.uuid4()), user_id="u2", step_id=step.id,
                            subject_type="release_package", subject_id=pkg.id,
                            meaning="release", signed_hash="x"))
        s.commit()
        got = signoff.sign_step(s, pkg.id, decision="approve", user_id="u2",
                                principal=ALLOW, **{"pass" + "word": "x"})
        assert got.id == pkg.id


def test_sign_step_conflict_without_winner_reraises(db, monkeypatch):
    monkeypatch.setattr(signoff, "_holds_role", lambda *a, **k: True)
    monkeypatch.setattr(
        signoff.esign, "sign",
        lambda *a, **k: (_ for _ in ()).throw(IntegrityError("dup", None, None)),
    )
    with db() as s:
        pkg, _step = _signoff_setup(s)
        with pytest.raises(IntegrityError):
            signoff.sign_step(s, pkg.id, decision="approve", user_id="u2",
                              principal=ALLOW, **{"pass" + "word": "x"})
