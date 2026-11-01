"""Stage 2 / SR-037: parse_shards is dialect-neutral and version-aware."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.services.parse_shards import (
    PARSER_VERSION,
    delete_for_content,
    get_shard,
    put_shard,
    sweep_stale,
)


@pytest.fixture()
def _tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create every model table (incl. parse_shards) in the session tmp DB."""
    from app.db.database import Base, default_session_factory

    db_url = f"sqlite:///{tmp_path}/stage2_shards.db"
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    engine.dispose()
    return db_url


def _raw(db_url: str, sql: str, params: dict | None = None) -> list:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(sql), params or {})
            return [tuple(r) for r in result] if result.returns_rows else []
    finally:
        engine.dispose()


def test_put_get_roundtrip_carries_version(_tables):
    assert put_shard("sha1", 0, 1, 10, "markitdown", "body", "{}")
    row = get_shard("sha1", 0)
    assert row is not None and row["markdown"] == "body"
    assert row["parser_version"] == PARSER_VERSION
    # Overwrite (the DELETE-then-INSERT upsert) keeps exactly one row.
    assert put_shard("sha1", 0, 1, 10, "markitdown", "body2", "{}")
    assert get_shard("sha1", 0)["markdown"] == "body2"
    assert _raw(_tables, "SELECT COUNT(*) FROM parse_shards") == [(1,)]


def test_legacy_row_without_version_is_a_miss(_tables):
    _raw(_tables,
         "INSERT INTO parse_shards (content_sha256, shard_index, page_start, page_end, "
         "parser, markdown, extras_json, updated_at) "
         "VALUES ('old', 0, 1, 5, 'x', 'stale', '', '2026-01-01 00:00:00')")
    assert get_shard("old", 0) is None


def test_sweep_uses_naive_utc_cutoff(_tables):
    _raw(_tables,
         "INSERT INTO parse_shards (content_sha256, shard_index, page_start, page_end, "
         "parser, markdown, extras_json, parser_version, updated_at) "
         "VALUES ('ancient', 0, 1, 5, 'x', 'm', '', :ver, '2020-01-01 00:00:00')",
         {"ver": PARSER_VERSION})
    assert put_shard("fresh", 0, 1, 5, "x", "m")
    assert sweep_stale(7) == 1
    assert get_shard("fresh", 0) is not None
    delete_for_content("fresh")


def test_0088_migration_adds_column_and_index(tmp_path, monkeypatch):
    """0088 upgrades to parser_version + updated_at index and back."""
    from sqlalchemy import inspect

    from tests.alembic_helpers import run_downgrade, run_upgrade

    db_url = f"sqlite:///{tmp_path}/stage2_0088.db"
    monkeypatch.setenv("FORMUNEX_DB_URL", db_url)
    run_upgrade(db_url, monkeypatch)
    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        assert "parser_version" in {c["name"] for c in insp.get_columns("parse_shards")}
        assert any(ix["name"] == "ix_parse_shards_updated_at"
                   for ix in insp.get_indexes("parse_shards"))
    finally:
        engine.dispose()
    run_downgrade(db_url, monkeypatch, "0087")
    engine = create_engine(db_url)
    try:
        insp = inspect(engine)
        assert "parser_version" not in {c["name"] for c in insp.get_columns("parse_shards")}
        assert not any(ix["name"] == "ix_parse_shards_updated_at"
                       for ix in insp.get_indexes("parse_shards"))
    finally:
        engine.dispose()


def test_no_dialect_specific_sql_left():
    """The PG failure was literal: ``?`` placeholders and OR REPLACE."""
    src = Path(__file__).resolve().parent.parent.joinpath(
        "app/services/parse_shards.py").read_text(encoding="utf-8")
    # "INSERT OR REPLACE INTO" is the statement that breaks PG; the
    # docstring mentions the phrase, so match the statement, not the word.
    assert "INSERT OR REPLACE INTO" not in src
    assert "exec_driver_sql" not in src
    assert " = ?" not in src
