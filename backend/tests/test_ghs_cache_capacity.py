"""Stage 3 / SR-061: RegulatoryDataCache must stay bounded."""

from app.services.plm.ghs.cache import _MISSING, RegulatoryDataCache


def test_cache_evicts_oldest_when_full():
    cache = RegulatoryDataCache(ttl_s=300, max_entries=3)
    cache.put("substance", "a", 1)
    cache.put("substance", "b", 2)
    cache.put("substance", "c", 3)
    assert len(cache) == 3
    cache.put("substance", "d", 4)
    assert len(cache) == 3
    # FIFO: the oldest insertion ("a") is gone, the rest survive. get()
    # (not fetch()) so the check itself does not reload and re-evict.
    assert cache.get("substance", "a") is _MISSING
    assert cache.get("substance", "b") == 2
    assert cache.get("substance", "c") == 3
    assert cache.get("substance", "d") == 4


def test_reputting_existing_key_does_not_evict():
    cache = RegulatoryDataCache(ttl_s=300, max_entries=2)
    cache.put("substance", "a", 1)
    cache.put("substance", "b", 2)
    cache.put("substance", "a", 10)
    assert len(cache) == 2
    assert cache.get("substance", "a") == 10
    assert cache.get("substance", "b") == 2
