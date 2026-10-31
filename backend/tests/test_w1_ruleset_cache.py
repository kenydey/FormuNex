"""W1-2: rule-set and font caches invalidate when the file changes.

Before W1-2 both were ``lru_cache``-only: a rule-set status flip
(draft → released) or a font configuration change needed a service
restart to take effect. Now a stat check (mtime + size) reloads.
"""
from __future__ import annotations

import os

import pytest

from app.services.plm.ghs import ruleset as ruleset_mod
from app.services.plm.render import fonts as fonts_mod


@pytest.fixture(autouse=True)
def _clear_caches():
    ruleset_mod.load_ruleset.cache_clear()
    fonts_mod.resolve_font.cache_clear()
    yield
    ruleset_mod.load_ruleset.cache_clear()
    fonts_mod.resolve_font.cache_clear()


def test_ruleset_reloads_after_file_change(tmp_path, monkeypatch):
    monkeypatch.setattr(ruleset_mod, "RULESET_DIR", tmp_path)
    path = tmp_path / "probe.v1.yaml"
    path.write_text("version: probe.v1\nstatus: draft\n", encoding="utf-8")
    assert ruleset_mod.load_ruleset("probe.v1").status == "draft"
    # Same name, changed content (and a distinct mtime, deterministically).
    path.write_text("version: probe.v1\nstatus: released\n", encoding="utf-8")
    os.utime(path, ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
    assert ruleset_mod.load_ruleset("probe.v1").status == "released"


def test_ruleset_missing_file_evicts_and_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(ruleset_mod, "RULESET_DIR", tmp_path)
    path = tmp_path / "probe.v1.yaml"
    path.write_text("version: probe.v1\nstatus: draft\n", encoding="utf-8")
    assert ruleset_mod.load_ruleset("probe.v1").status == "draft"
    path.unlink()
    with pytest.raises(FileNotFoundError):
        ruleset_mod.load_ruleset("probe.v1")
    # A re-created file loads again instead of serving the stale entry.
    path.write_text("version: probe.v1\nstatus: released\n", encoding="utf-8")
    assert ruleset_mod.load_ruleset("probe.v1").status == "released"


def test_font_reresolves_when_configuration_changes(tmp_path, monkeypatch):
    calls: list = []

    def fake_register(path, index):
        calls.append(path)
        return True

    monkeypatch.setattr(fonts_mod, "_try_register", fake_register)
    font_a = tmp_path / "a.ttf"
    font_b = tmp_path / "b.ttf"
    font_a.write_bytes(b"")
    font_b.write_bytes(b"")
    monkeypatch.setenv("FORMUNEX_PLM_DOC_FONT_PATH", str(font_a))
    first = fonts_mod.resolve_font()
    assert first.path == str(font_a)
    assert len(calls) == 1
    # Same configuration: cache hit, no re-registration.
    assert fonts_mod.resolve_font() is first
    assert len(calls) == 1
    # Changed configuration: cache miss, re-resolution happens.
    monkeypatch.setenv("FORMUNEX_PLM_DOC_FONT_PATH", str(font_b))
    second = fonts_mod.resolve_font()
    assert second.path == str(font_b)
    assert len(calls) == 2
