"""Stage 1 / SR-036: startup database head + schema gate."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from app.startup_checks import run_startup_db_checks


def _settings(db_url, head_check=True):
    return SimpleNamespace(db_url=db_url, startup_alembic_head_check=head_check)


def _db(tmp_path, version=None, tables=()):
    url = f"sqlite:///{tmp_path / 'db.sqlite'}"
    engine = create_engine(url)
    with engine.begin() as conn:
        if version is not None:
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(text(f"INSERT INTO alembic_version VALUES ('{version}')"))
        for ddl in tables:
            conn.execute(text(ddl))
    engine.dispose()
    return url


def test_head_behind_refuses_by_default(tmp_path):
    url = _db(tmp_path, version="0083")
    with pytest.raises(RuntimeError, match="not at head"):
        run_startup_db_checks(_settings(url))


def test_head_behind_warns_when_opted_out(tmp_path):
    url = _db(tmp_path, version="0083")
    warnings = run_startup_db_checks(_settings(url, head_check=False))
    assert any("not at head" in w for w in warnings)


def test_missing_version_table_skips(tmp_path):
    # Fresh create_all database: schema comes from the models, no stamp.
    url = _db(tmp_path, version=None)
    assert run_startup_db_checks(_settings(url)) == []


def test_schema_drift_refuses(tmp_path):
    # Stamped version must be the current head (0096 since deep-review batch 6):
    # otherwise the version check refuses before drift is even examined.
    url = _db(tmp_path, version="0096", tables=(
        "CREATE TABLE regulatory_data_releases (id VARCHAR(36) PRIMARY KEY, status VARCHAR(16))",
    ))
    with pytest.raises(RuntimeError, match="schema drift"):
        run_startup_db_checks(_settings(url))


def test_missing_builtin_role_rows_warn(tmp_path):
    # Same head bump: stamped 0092 would refuse as "not at head" before
    # the role check can warn.
    url = _db(tmp_path, version="0096", tables=(
        "CREATE TABLE roles (code VARCHAR(40) PRIMARY KEY)",
        "INSERT INTO roles VALUES ('sys_admin')",
    ))
    warnings = run_startup_db_checks(_settings(url))
    assert any("rd_engineer" in w for w in warnings)


def test_unreachable_database_warns_not_refuses():
    warnings = run_startup_db_checks(
        _settings("postgresql://user:pw@127.0.0.1:1/formunex"))
    # Sandbox has no psycopg driver, so engine creation itself fails;
    # either way the outcome is a warning, never a refusal.
    assert warnings and ("unreachable" in warnings[0] or "cannot build" in warnings[0])
