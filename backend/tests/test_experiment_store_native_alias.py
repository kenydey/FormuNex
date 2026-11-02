"""Datalab removal batch 2: the experiment store is always native.

Salvaged from tests/test_experiment_backend_auto.py (deleted: every auto
test asserted the Datalab switch). The one surviving contract is that an
explicit "sqlite" backend lands on LocalExperimentStore — now trivially
true because no probe code remains, but pinned so a future backend switch
cannot silently reintroduce probing.
"""

from __future__ import annotations

from app.config import Settings
from app.db.store import LocalExperimentStore, get_experiment_store, reset_experiment_store


def test_explicit_sqlite_uses_local_store(monkeypatch):
    s = Settings(experiment_backend="sqlite", datalab_api_url="http://datalab.test:5001")
    reset_experiment_store(None)
    try:
        store = get_experiment_store(s)
        assert isinstance(store, LocalExperimentStore)
    finally:
        reset_experiment_store(None)
