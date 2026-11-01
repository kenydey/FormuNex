"""Stage 2 / SR-038: 0076/0079/0074 downgrades refuse instead of losing semantics.

Only downgrade() bodies changed; upgrade() paths are untouched. The
existing 0074 strip test must keep passing: rows matching the frozen
post-upgrade state still strip exactly as before.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text

from tests.alembic_helpers import run_downgrade, run_upgrade


@pytest.fixture()
def tmp_db_url(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point ``FORMUNEX_DB_URL`` at a fresh per-test SQLite database."""
    url = f"sqlite:///{tmp_path}/stage2_downgrade_test.db"
    monkeypatch.setenv("FORMUNEX_DB_URL", url)
    return url


def _exec(db_url: str, sql: str) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


def _fetch(db_url: str, sql: str) -> list:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(sql))]
    finally:
        engine.dispose()


def test_0076_downgrade_refuses_with_non_approved_rows(tmp_db_url, monkeypatch):
    run_upgrade(tmp_db_url, monkeypatch)
    _exec(tmp_db_url,
          "INSERT INTO regulatory_data_releases (id, label, source, stats, input_digest, status, created_at) "
          "VALUES ('r-draft', 't', 'seed', '{}', 'd1', 'draft', '2026-01-01 00:00:00')")
    with pytest.raises(RuntimeError, match="0076 downgrade refused"):
        run_downgrade(tmp_db_url, monkeypatch, "0075")
    _exec(tmp_db_url, "DELETE FROM regulatory_data_releases WHERE id = 'r-draft'")
    run_downgrade(tmp_db_url, monkeypatch, "0075")
    cols = [r[1] for r in _fetch(tmp_db_url, "PRAGMA table_info(regulatory_data_releases)")]
    assert "status" not in cols


def test_0079_downgrade_refuses_with_restore_targets(tmp_db_url, monkeypatch):
    run_upgrade(tmp_db_url, monkeypatch)
    _exec(tmp_db_url,
          "INSERT INTO products (id, code, name_zh, name_en, lifecycle_state, archived_from_state, created_at, updated_at) "
          "VALUES ('p1', 'P1', 'x', 'x', 'rd', 'rd', '2026-01-01 00:00:00', '2026-01-01 00:00:00')")
    with pytest.raises(RuntimeError, match="0079 downgrade refused"):
        run_downgrade(tmp_db_url, monkeypatch, "0078")
    _exec(tmp_db_url, "UPDATE products SET archived_from_state = NULL WHERE id = 'p1'")
    run_downgrade(tmp_db_url, monkeypatch, "0078")
    cols = [r[1] for r in _fetch(tmp_db_url, "PRAGMA table_info(products)")]
    assert "archived_from_state" not in cols


def test_0074_downgrade_refuses_manually_edited_builtin_rows(tmp_db_url, monkeypatch):
    run_upgrade(tmp_db_url, monkeypatch)
    row = _fetch(tmp_db_url, "SELECT permissions FROM roles WHERE code = 'sys_admin'")[0][0]
    perms = json.loads(row) if isinstance(row, str) else list(row)
    perms.append("formulation.write")  # a manual grant after the upgrade
    _exec(tmp_db_url,
          f"UPDATE roles SET permissions = '{json.dumps(perms)}' WHERE code = 'sys_admin'")
    with pytest.raises(RuntimeError, match="0074 downgrade refused"):
        run_downgrade(tmp_db_url, monkeypatch, "0073")
    # Restore the frozen state: the strip proceeds exactly as before.
    perms.remove("formulation.write")
    _exec(tmp_db_url,
          f"UPDATE roles SET permissions = '{json.dumps(perms)}' WHERE code = 'sys_admin'")
    run_downgrade(tmp_db_url, monkeypatch, "0073")
    row = _fetch(tmp_db_url, "SELECT permissions FROM roles WHERE code = 'sys_admin'")[0][0]
    perms = json.loads(row) if isinstance(row, str) else list(row)
    assert "chemical.read" not in perms
