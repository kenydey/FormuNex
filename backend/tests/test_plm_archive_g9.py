"""G9 archive / restore, W1-8 state-machine semantics (D9).

Supersedes the G9 lightweight variant: archiving a released product is
refused, restore returns to the pre-archive state, and both actions
write hash-chained audit rows.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.models_plm import AuditLogRow, ProductRevisionRow, ProductRow
from app.services.plm import archive


@pytest.fixture()
def sf(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_PLM_STORAGE_DIR", str(tmp_path / "plm"))
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/g9.db")
    Base.metadata.create_all(engine)
    yield make_session_factory(engine)
    engine.dispose()
    get_settings.cache_clear()


def test_archive_restore_cycle_and_guards(sf):
    with sf() as s:
        p = ProductRow(id="p-1", code="P-1", lifecycle_state="rd")
        s.add(p)
        s.flush()
        row = archive.archive_product(s, "p-1", user_id="u1")
        assert row.lifecycle_state == "withdrawn" and row.archived_from_state == "rd"
        with pytest.raises(ValueError, match="已归档"):
            archive.archive_product(s, "p-1", user_id="u1")
        row = archive.restore_product(s, "p-1", user_id="u1")
        assert row.lifecycle_state == "rd" and row.archived_from_state is None
        with pytest.raises(ValueError, match="只有已归档"):
            archive.restore_product(s, "p-1", user_id="u1")
        with pytest.raises(ValueError, match="不存在"):
            archive.archive_product(s, "nope", user_id="u1")
        actions = {a.action for a in s.execute(
            select(AuditLogRow).where(AuditLogRow.entity_id == "p-1")).scalars()}
        assert actions == {"product.archive", "product.restore"}


def test_archive_refuses_released_product_and_released_revision(sf):
    with sf() as s:
        s.add(ProductRow(id="p-2", code="P-2", lifecycle_state="released"))
        s.flush()
        with pytest.raises(ValueError, match="已发布产品不能直接归档"):
            archive.archive_product(s, "p-2", user_id="u1")
        assert s.get(ProductRow, "p-2").lifecycle_state == "released"
        # Product state drifted, but a released revision remains: still refused.
        s.add(ProductRow(id="p-3", code="P-3", lifecycle_state="rd"))
        s.flush()
        s.add(ProductRevisionRow(id="r-3", product_id="p-3", rev="A", state="released"))
        s.flush()
        with pytest.raises(ValueError, match="仍有已发布修订版"):
            archive.archive_product(s, "p-3", user_id="u1")


def test_restore_returns_to_pre_archive_state_and_legacy_fallback(sf):
    with sf() as s:
        s.add(ProductRow(id="p-4", code="P-4", lifecycle_state="candidate"))
        s.flush()
        archive.archive_product(s, "p-4", user_id="u1")
        assert archive.restore_product(s, "p-4", user_id="u1").lifecycle_state == "candidate"
        # Legacy row archived before archived_from_state existed.
        s.add(ProductRow(id="p-5", code="P-5", lifecycle_state="withdrawn", archived_from_state=None))
        s.flush()
        assert archive.restore_product(s, "p-5", user_id="u1").lifecycle_state == "rd"


def test_generate_refuses_archived_product(sf):
    from app.services.plm.documents import service as docs

    with sf() as s:
        s.add(ProductRow(id="p-6", code="P-6", lifecycle_state="withdrawn"))
        s.flush()
        s.add(ProductRevisionRow(id="r-6", product_id="p-6", rev="A", state="draft"))
        s.flush()
        with pytest.raises(docs.DocumentError, match="已归档"):
            docs.generate_document(s, "r-6", "sds")
