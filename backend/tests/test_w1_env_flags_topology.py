"""W1-10 ⑤: the UI must not override a deployment-owned topology flag."""
from __future__ import annotations

import os

import pytest

from app.services import secrets_store
from app.services.env_flags import update_env_flags

KEY = "FORMUNEX_CELERY_EAGER"


@pytest.fixture(autouse=True)
def _isolate_ui_managed(monkeypatch):
    # The full suite leaks marks: an earlier test can leave a key in
    # _UI_MANAGED_KEYS (snapshot/restore alone does not help — the leak is
    # already present when this file starts). Each test here states its own
    # premise, so start from a clean set and restore afterwards.
    saved = set(secrets_store._UI_MANAGED_KEYS)
    secrets_store._UI_MANAGED_KEYS.clear()
    yield
    secrets_store._UI_MANAGED_KEYS.clear()
    secrets_store._UI_MANAGED_KEYS.update(saved)


@pytest.fixture(autouse=True)
def _stub_env_persist(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(secrets_store, "write_env_updates", lambda updates, path=None: calls.append(dict(updates)))
    return calls


def test_deployment_owned_topology_flag_is_refused(monkeypatch, _stub_env_persist):
    """Compose sets FORMUNEX_CELERY_EAGER and the UI never touched it: the UI
    change is rejected, the deployment value stands, nothing is persisted."""
    monkeypatch.setenv(KEY, "false")
    updated, rejected = update_env_flags({"celery_eager": True})
    assert updated == [] and rejected == ["celery_eager"]
    assert os.environ[KEY] == "false"
    assert _stub_env_persist == []


def test_ui_owned_topology_flag_stays_changeable(monkeypatch, _stub_env_persist):
    """No deployment value: the UI writes it once (and owns it from then on),
    so a later change — including back — is allowed."""
    monkeypatch.delenv(KEY, raising=False)
    updated, rejected = update_env_flags({"celery_eager": True})
    assert updated == ["celery_eager"] and rejected == []
    assert os.environ[KEY] == "true"
    updated, rejected = update_env_flags({"celery_eager": False})
    assert updated == ["celery_eager"] and rejected == []
    assert os.environ[KEY] == "false"


def test_startup_restored_topology_flag_stays_changeable(monkeypatch, _stub_env_persist):
    """A value restored from the UI-persisted .env at startup is UI-owned,
    even though it now sits in os.environ: changing it is allowed."""
    monkeypatch.setenv(KEY, "true")
    secrets_store.mark_ui_managed({KEY})
    updated, rejected = update_env_flags({"celery_eager": False})
    assert updated == ["celery_eager"] and rejected == []
    assert os.environ[KEY] == "false"


def test_non_topology_flag_ignores_the_guard(monkeypatch, _stub_env_persist):
    """The guard covers topology flags only: an ordinary flag stays writable
    even when the deployment environment already sets it."""
    monkeypatch.setenv("FORMUNEX_AUTO_RETRAIN", "false")
    updated, rejected = update_env_flags({"auto_retrain": True})
    assert updated == ["auto_retrain"] and rejected == []
    assert os.environ["FORMUNEX_AUTO_RETRAIN"] == "true"
