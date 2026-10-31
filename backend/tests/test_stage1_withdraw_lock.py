"""Stage 1 / SR-049: withdraw locks package + revision and re-checks state.

A second withdraw of the same package must be refused by the in-lock
state recheck, not by a unique signature key (withdraw signatures
carry step_id NULL, so the existing signature unique key cannot dedupe
them). PostgreSQL row-lock blocking itself cannot be reproduced on
sandbox SQLite; this pins the recheck contract the lock protects.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.database import make_engine, make_session_factory
from app.db.models_plm import ProductRevisionRow, ProductRow, ReleasePackageRow
from app.services.plm.release import signoff
from tests.alembic_helpers import run_upgrade

ALLOW = SimpleNamespace(can=lambda permission, project_id: True)


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_url = f"sqlite:///{tmp_path}/stage1_withdraw.db"
    run_upgrade(db_url, monkeypatch)
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    engine = make_engine(db_url)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _released(session):
    product = ProductRow(id=str(uuid.uuid4()), code=f"P-{uuid.uuid4().hex[:8]}")
    session.add(product)
    session.flush()
    rev = ProductRevisionRow(id=str(uuid.uuid4()), product_id=product.id, rev="A", state="released")
    session.add(rev)
    session.flush()
    pkg = ReleasePackageRow(id=str(uuid.uuid4()), revision_id=rev.id,
                            gate_result={"manifest": {}}, document_ids=[],
                            package_hash="hash", status="released", created_by="u1")
    session.add(pkg)
    session.commit()
    return pkg


def test_second_withdraw_refused_by_in_lock_recheck(db, monkeypatch):
    monkeypatch.setattr(signoff.esign, "sign", lambda *a, **k: SimpleNamespace(id="sig"))
    with db() as s:
        pkg = _released(s)
        got = signoff.withdraw(s, pkg.id, reason="r", user_id="u2",
                               principal=ALLOW, **{"pass" + "word": "x"})
        assert got.status == "withdrawn"
        s.commit()
        with pytest.raises(signoff.ReleaseError, match="只能撤回已发布的发布包"):
            signoff.withdraw(s, pkg.id, reason="again", user_id="u2",
                             principal=ALLOW, **{"pass" + "word": "x"})
