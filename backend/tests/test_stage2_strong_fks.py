"""Stage 2 / SR-032/033: 0089 strong-reference FKs + ondelete unification.

Two shapes, because the 0001 baseline builds fresh databases from the
current models (which already declare these FKs, unnamed):

* fresh DB (``run_upgrade`` to head): the end state must carry exactly
  one FK per column with the right parent and ondelete — 0089 skips
  creation when a matching FK already exists instead of duplicating it.
* legacy DB (hand-built minimal schema stamped at 0088): 0089 creates
  the NAMED FKs, refuses on orphan rows, and downgrades cleanly.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.alembic_helpers import run_downgrade, run_upgrade


@pytest.fixture()
def tmp_db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"sqlite:///{tmp_path}/stage2_fks.db"
    monkeypatch.setenv("FORMUNEX_DB_URL", url)
    return url


def _fks(db_url: str, table: str) -> list:
    # A list, not a name-keyed dict: several FKs here are unnamed, and
    # keying by name would collapse them into one entry.
    engine = create_engine(db_url)
    try:
        return inspect(engine).get_foreign_keys(table)
    finally:
        engine.dispose()


def _col_fks(fks: list, column: str) -> list:
    return [fk for fk in fks if fk["constrained_columns"] == [column]]


def _exec(db_url: str, sql: str) -> None:
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


def _legacy_schema_0088(db_url: str) -> None:
    """Minimal pre-0089 tables (no FKs except the untouched one) stamped
    at 0088, so the upgrade under test is exactly 0089."""
    stmts = [
        "CREATE TABLE batches (id VARCHAR(36) PRIMARY KEY)",
        "CREATE TABLE measurements (id VARCHAR(36) PRIMARY KEY, "
        "sample_id VARCHAR(36), lab_task_id VARCHAR(36))",
        "CREATE TABLE doe_plans (id VARCHAR(36) PRIMARY KEY)",
        "CREATE TABLE samples (id VARCHAR(36) PRIMARY KEY)",
        "CREATE TABLE lab_tasks (id VARCHAR(36) PRIMARY KEY, doe_plan_id VARCHAR(36))",
        "CREATE TABLE regulatory_documents (id VARCHAR(36) PRIMARY KEY, batch_id VARCHAR(36))",
        "CREATE TABLE batch_qc_results (id VARCHAR(36) PRIMARY KEY, measurement_id VARCHAR(36))",
        "CREATE TABLE substances (id VARCHAR(36) PRIMARY KEY)",
        "CREATE TABLE material_compositions (id VARCHAR(36) PRIMARY KEY, "
        "material_id VARCHAR(36), substance_id VARCHAR(36), "
        "FOREIGN KEY(substance_id) REFERENCES substances (id))",
        "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)",
        "INSERT INTO alembic_version VALUES ('0088')",
    ]
    for sql in stmts:
        _exec(db_url, sql)


# (table, column, parent, ondelete) — the 0089 end state on any shape.
_EXPECTED = (
    ("regulatory_documents", "batch_id", "batches", "SET NULL"),
    ("batch_qc_results", "measurement_id", "measurements", "SET NULL"),
    ("lab_tasks", "doe_plan_id", "doe_plans", "SET NULL"),
    ("measurements", "sample_id", "samples", "SET NULL"),
    ("material_compositions", "substance_id", "substances", "CASCADE"),
)


def test_fresh_upgrade_end_state(tmp_db_url, monkeypatch):
    run_upgrade(tmp_db_url, monkeypatch)
    for table, column, parent, ondelete in _EXPECTED:
        matches = [fk for fk in _col_fks(_fks(tmp_db_url, table), column)
                   if fk["referred_table"] == parent
                   and (fk["options"] or {}).get("ondelete") == ondelete]
        assert len(matches) == 1, (table, column, matches)
    # Sibling FKs survive (measurements keeps experiment_id,
    # source_document_id and the unnamed 0059 lab_task_id FK, once each).
    meas = _fks(tmp_db_url, "measurements")
    for col in ("experiment_id", "source_document_id", "lab_task_id"):
        assert len(_col_fks(meas, col)) == 1, col


def test_legacy_upgrade_creates_named_fks(tmp_db_url, monkeypatch):
    _legacy_schema_0088(tmp_db_url)
    run_upgrade(tmp_db_url, monkeypatch)
    names = {fk["name"] for fk in _fks(tmp_db_url, "regulatory_documents")}
    assert "fk_regulatory_documents_batch_id" in names
    names = {fk["name"] for fk in _fks(tmp_db_url, "lab_tasks")}
    assert "fk_lab_tasks_doe_plan_id" in names
    names = {fk["name"] for fk in _fks(tmp_db_url, "material_compositions")}
    assert "fk_material_compositions_substance_cascade" in names


def test_legacy_upgrade_refuses_orphans(tmp_db_url, monkeypatch):
    _legacy_schema_0088(tmp_db_url)
    _exec(tmp_db_url,
          "INSERT INTO lab_tasks (id, doe_plan_id) VALUES ('t-orphan', 'ghost-plan')")
    with pytest.raises(RuntimeError, match="0089 upgrade refused"):
        run_upgrade(tmp_db_url, monkeypatch)


def test_legacy_downgrade_restores_plain_substance_fk(tmp_db_url, monkeypatch):
    _legacy_schema_0088(tmp_db_url)
    run_upgrade(tmp_db_url, monkeypatch)
    run_downgrade(tmp_db_url, monkeypatch, "0088")
    assert not any(fk["name"] == "fk_lab_tasks_doe_plan_id"
                   for fk in _fks(tmp_db_url, "lab_tasks"))
    assert not any(fk["name"] == "fk_regulatory_documents_batch_id"
                   for fk in _fks(tmp_db_url, "regulatory_documents"))
    comp = _fks(tmp_db_url, "material_compositions")
    assert not any(fk["name"] == "fk_material_compositions_substance_cascade" for fk in comp)
    # Behavior restored: a plain (no-ondelete) substance FK remains.
    plain = [fk for fk in comp if fk["name"] == "fk_material_compositions_substance_plain"]
    assert len(plain) == 1 and plain[0]["options"].get("ondelete") is None
