"""Stage 3 / SR-062 (3-9): RegulatoryDataCache singleflight, tiered TTL,
targeted invalidation (no more after_flush full clear)."""

from __future__ import annotations

import threading
import time

import pytest

from app.services.plm.ghs.cache import (
    CACHE,
    _MISSING,
    RegulatoryDataCache,
    _invalidate_row,
)
from app.db.models_regulatory import (
    GhsStatementRow,
    MaterialCompositionRow,
    SubstanceClassificationRow,
    SubstanceRow,
    SubstanceTransportRow,
)
from app.db.models import MaterialRow


@pytest.fixture(autouse=True)
def _clean_global_cache():
    CACHE.clear()
    yield
    CACHE.clear()


def test_per_entry_ttl_and_tiers():
    """Entries expire on their own kind's clock, not one global clock."""
    cache = RegulatoryDataCache(ttl_s=10.0, ttl_by_kind={"fast": 0.05})
    cache.put("fast", "a", 1)
    cache.put("slow", "b", 2)
    time.sleep(0.15)
    assert cache.get("fast", "a") is _MISSING
    assert cache.get("slow", "b") == 2


def test_fetch_singleflights_concurrent_loads():
    """N threads fetching the same missing key trigger exactly one load."""
    cache = RegulatoryDataCache()
    calls: list[str] = []
    barrier = threading.Barrier(8)

    def _load():
        calls.append("load")
        time.sleep(0.2)
        return {"v": 42}

    results: list[dict] = []
    errors: list[BaseException] = []

    def _worker():
        try:
            barrier.wait(timeout=5)
            results.append(cache.fetch("substance", "s1", _load))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors
    assert len(results) == 8 and all(r == {"v": 42} for r in results)
    assert len(calls) == 1


def test_fetch_failed_leader_does_not_poison_followers():
    """A raising load raises to its caller; the next fetch retries."""
    cache = RegulatoryDataCache()
    attempts: list[int] = []

    def _flaky():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("db down")
        return "ok"

    with pytest.raises(RuntimeError):
        cache.fetch("substance", "s1", _flaky)
    assert cache.fetch("substance", "s1", _flaky) == "ok"
    assert len(attempts) == 2


def test_invalidate_row_targets_only_its_entries():
    """A substance write drops its own entries + the cas kind, nothing else."""
    CACHE.put("substance", "s1", "snap-s1")
    CACHE.put("substance", "s2", "snap-s2")
    CACHE.put("substance_cas", "50-00-0", "snap-cas")
    CACHE.put("classifications", "s1", ["c1"])
    CACHE.put("statement", "H315", {"zh": "x"})
    _invalidate_row(SubstanceRow(id="s1"), deleted=False)
    assert CACHE.get("substance", "s1") is _MISSING
    assert CACHE.get("substance", "s2") == "snap-s2"
    assert CACHE.get("substance_cas", "50-00-0") is _MISSING
    assert CACHE.get("classifications", "s1") == ["c1"]
    assert CACHE.get("statement", "H315") == {"zh": "x"}


def test_invalidate_row_delete_uses_kind_level_for_fk_keys():
    """Deleted rows cannot be trusted for FK attributes: kind-level drop."""
    CACHE.put("classifications", "s1", ["c1"])
    CACHE.put("classifications", "s2", ["c2"])
    CACHE.put("transport", "s1", ["t1"])
    CACHE.put("compositions", "m1", ["x"])
    _invalidate_row(SubstanceClassificationRow(substance_id="s1"), deleted=True)
    assert CACHE.get("classifications", "s1") is _MISSING
    assert CACHE.get("classifications", "s2") is _MISSING
    assert CACHE.get("transport", "s1") == ["t1"]
    _invalidate_row(MaterialCompositionRow(material_id="m1"), deleted=True)
    assert CACHE.get("compositions", "m1") is _MISSING


def test_invalidate_row_material_delete_drops_its_compositions():
    CACHE.put("material", "m1", "mat")
    CACHE.put("compositions", "m1", ["x"])
    CACHE.put("material", "m2", "other")
    _invalidate_row(MaterialRow(id="m1"), deleted=True)
    assert CACHE.get("material", "m1") is _MISSING
    assert CACHE.get("compositions", "m1") is _MISSING
    assert CACHE.get("material", "m2") == "other"


def test_invalidate_row_statement_uses_pk_code():
    CACHE.put("statement", "H315", {"zh": "x"})
    CACHE.put("statement", "P101", {"zh": "y"})
    _invalidate_row(GhsStatementRow(code="H315"), deleted=True)
    assert CACHE.get("statement", "H315") is _MISSING
    assert CACHE.get("statement", "P101") == {"zh": "y"}


def test_session_flush_invalidates_targeted_not_global(tmp_path):
    """End to end: a real flush drops only the touched substance's entries."""
    from app.db.database import Base, make_engine, make_session_factory

    engine = make_engine(f"sqlite:///{tmp_path}/cache39.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    CACHE.put("substance", "s1", "snap-s1")
    CACHE.put("substance", "s2", "snap-s2")
    CACHE.put("statement", "H315", {"zh": "x"})
    with factory() as session:
        session.add(SubstanceRow(id="s1", cas_no="50-00-0", name_en="Formaldehyde"))
        session.commit()
    assert CACHE.get("substance", "s1") is _MISSING
    assert CACHE.get("substance", "s2") == "snap-s2"
    assert CACHE.get("statement", "H315") == {"zh": "x"}
    engine.dispose()
