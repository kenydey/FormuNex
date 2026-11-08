"""Batch 3: supplier SDS import invalidates the classifications cache (P2-C9)."""
from __future__ import annotations

import uuid

import pytest

from app.config import get_settings
from app.db.database import default_session_factory
from app.db.models_regulatory import SubstanceRow
from app.services.plm.ghs.cache import CACHE, _MISSING
from app.services.plm.regulatory.supplier_sds import ParsedClassification, ParsedSds, import_sds


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUNEX_DB_URL", f"sqlite:///{tmp_path / 'sup.db'}")
    get_settings.cache_clear()
    factory = default_session_factory()
    with factory() as s:
        s.add(SubstanceRow(id="sub-1", cas_no="111-11-1", name_zh="测试物质", name_en="Test", aliases=[]))
        s.commit()
    yield factory
    get_settings.cache_clear()


def test_supplier_import_invalidates_classifications_cache(db):
    CACHE.put("classifications", "sub-1", {"stale": True})
    parsed = ParsedSds(cas_no="111-11-1",
                       classifications=[ParsedClassification("flammable_liquid", "3", "H226")])
    with db() as session:
        import_sds(session, parsed, filename="sup.pdf", digest="d1")
    assert CACHE.get("classifications", "sub-1") is _MISSING
