"""FormuMind → FormuNex env-var rename: legacy FORMUMIND_* keys keep working."""
from __future__ import annotations

from app.env_compat import alias_legacy_env_files, alias_legacy_environ


def test_legacy_environ_key_is_promoted():
    env = {"FORMUMIND_DB_URL": "sqlite:///legacy.db"}
    applied = alias_legacy_environ(env)
    assert env["FORMUNEX_DB_URL"] == "sqlite:///legacy.db"
    assert applied == ["FORMUMIND_DB_URL"]


def test_new_name_wins_over_legacy():
    env = {"FORMUMIND_DB_URL": "sqlite:///legacy.db", "FORMUNEX_DB_URL": "postgresql://new"}
    assert alias_legacy_environ(env) == []
    assert env["FORMUNEX_DB_URL"] == "postgresql://new"


def test_unrelated_keys_untouched():
    env = {"PATH": "/bin", "FORMUNEX_X": "1"}
    alias_legacy_environ(env)
    assert env == {"PATH": "/bin", "FORMUNEX_X": "1"}


def test_legacy_env_file_key_is_promoted(tmp_path):
    f = tmp_path / ".env"
    f.write_text(
        "# comment\nexport FORMUMIND_LLM_PROVIDER='deepseek'\nFORMUMIND_KG_ENABLED=\"true\"\nOTHER=1\n",
        encoding="utf-8",
    )
    env: dict[str, str] = {}
    applied = alias_legacy_env_files([f], env)
    assert env == {"FORMUNEX_LLM_PROVIDER": "deepseek", "FORMUNEX_KG_ENABLED": "true"}
    assert sorted(applied) == ["FORMUMIND_KG_ENABLED", "FORMUMIND_LLM_PROVIDER"]


def test_env_file_new_key_in_any_file_blocks_legacy(tmp_path):
    a = tmp_path / "a.env"
    b = tmp_path / "b.env"
    a.write_text("FORMUMIND_LLM_PROVIDER=qwen\n", encoding="utf-8")
    b.write_text("FORMUNEX_LLM_PROVIDER=anthropic\n", encoding="utf-8")
    env: dict[str, str] = {}
    assert alias_legacy_env_files([a, b], env) == []
    assert env == {}


def test_environment_beats_legacy_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("FORMUMIND_LLM_PROVIDER=qwen\n", encoding="utf-8")
    env = {"FORMUNEX_LLM_PROVIDER": "anthropic"}
    alias_legacy_env_files([f], env)
    assert env["FORMUNEX_LLM_PROVIDER"] == "anthropic"


def test_missing_env_file_is_ignored(tmp_path):
    env: dict[str, str] = {}
    assert alias_legacy_env_files([tmp_path / "nope.env"], env) == []


def test_settings_reads_legacy_environment(monkeypatch):
    from app.config import get_settings

    monkeypatch.delenv("FORMUNEX_MIN_TRAIN_SAMPLES", raising=False)
    monkeypatch.setenv("FORMUMIND_MIN_TRAIN_SAMPLES", "7")
    get_settings.cache_clear()
    try:
        assert get_settings().min_train_samples == 7
    finally:
        monkeypatch.delenv("FORMUNEX_MIN_TRAIN_SAMPLES", raising=False)
        monkeypatch.delenv("FORMUMIND_MIN_TRAIN_SAMPLES", raising=False)
        get_settings.cache_clear()
