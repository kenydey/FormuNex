"""G9 lightweight archive / restore: withdrawn <-> rd."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.models_plm import ProductRow
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
        p = ProductRow(id="p-1", code="P-1", lifecycle_state="released")
        s.add(p)
        s.flush()
        row = archive.archive_product(s, "p-1")
        assert row.lifecycle_state == "withdrawn"
        with pytest.raises(ValueError, match="已归档"):
            archive.archive_product(s, "p-1")
        row = archive.restore_product(s, "p-1")
        assert row.lifecycle_state == "rd"  # pre-archive state is not stored
        with pytest.raises(ValueError, match="只有已归档"):
            archive.restore_product(s, "p-1")
        with pytest.raises(ValueError, match="不存在"):
            archive.archive_product(s, "nope")
