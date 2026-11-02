"""Stage 5 / 5-3: health zone moved to app.api.health (SR-089) and
/health answers carry freshness stamps (SR-094).

The move itself is covered by the existing health tests (HTTP-level
subset assertions) plus test_security_hardening's window test; this
file pins only what 5-3 adds: as_of/cached_at/cached on /health, and
that the public names are still importable from app.main.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app, health, health_detailed, reset_health_cache

client = TestClient(app, raise_server_exceptions=False)


def test_health_carries_freshness_stamp_and_cache_flag():
    reset_health_cache()
    first = client.get("/health").json()
    assert first["as_of"] and first["as_of"] == first["cached_at"]
    assert first["cached"] is False
    second = client.get("/health").json()
    assert second["cached"] is True
    assert second["as_of"] == first["as_of"]
    reset_health_cache()


def test_health_names_still_importable_from_main():
    assert callable(health) and callable(health_detailed) and callable(reset_health_cache)
