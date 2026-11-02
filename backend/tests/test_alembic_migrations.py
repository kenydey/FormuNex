"""Tests for idempotent post-baseline Alembic migrations 0002-0006 (Task 0.2a).

Covers three guarantees:

1. A *legacy* database (pre-migration schema, missing the columns added by
   migrations 0002-0005 and still containing ``experiment_records``) gains
   every new column and loses the legacy table after ``alembic upgrade head``.
2. A *fresh* database (baseline 0001 already contains all columns) runs the
   same upgrade chain silently — every post-baseline migration is a no-op.
3. The revision chain is linear and ends at head ``0009``.

The Alembic-invocation helper lives in ``tests/alembic_helpers.py`` (Task
0.2b-2) and is shared with the other migration-oriented suites.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.alembic_helpers import ALEMBIC_INI, run_upgrade


def _column_names(db_url: str, table: str) -> set[str]:
    """Return the set of column names for ``table`` in the target database."""
    engine = create_engine(db_url)
    try:
        return {c["name"] for c in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def _table_names(db_url: str) -> set[str]:
    """Return the set of table names present in the target database."""
    engine = create_engine(db_url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _index_names(db_url: str, table: str) -> set[str]:
    """Return the set of index names for ``table`` in the target database."""
    engine = create_engine(db_url)
    try:
        return {ix["name"] for ix in inspect(engine).get_indexes(table)}
    finally:
        engine.dispose()


def _column_types(db_url: str, table: str) -> dict[str, str]:
    """Return ``{column_name: type_string}`` for ``table`` in the target database."""
    engine = create_engine(db_url)
    try:
        return {c["name"]: str(c["type"]).upper() for c in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def _fetch_all(db_url: str, sql: str) -> list[tuple]:
    """Execute ``sql`` against the target database and return all rows."""
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            return [tuple(row) for row in conn.execute(text(sql))]
    finally:
        engine.dispose()


def _exec(db_url: str, sql: str) -> None:
    """Execute a write statement against the target database."""
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


@pytest.fixture()
def tmp_db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point ``FORMUNEX_DB_URL`` at a fresh per-test SQLite database."""
    url = f"sqlite:///{tmp_path}/alembic_migrations_test.db"
    monkeypatch.setenv("FORMUNEX_DB_URL", url)
    return url


def _create_legacy_schema(db_url: str) -> None:
    """Create the pre-migration table shapes directly with raw SQL.

    Mirrors the schema as it existed before columns were added to the ORM
    models, plus the legacy ``experiment_records`` table that migration 0006
    is responsible for dropping.
    """
    engine = create_engine(db_url)
    statements = [
        """
        CREATE TABLE experiments (
            id INTEGER PRIMARY KEY,
            domain VARCHAR(64),
            factors JSON,
            cure_temperature_c FLOAT,
            measured JSON,
            source VARCHAR(64),
            label VARCHAR(255),
            created_at DATETIME
        )
        """,
        """
        CREATE TABLE campaigns (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255),
            strategy VARCHAR(64),
            status VARCHAR(32),
            created_at DATETIME,
            updated_at DATETIME
        )
        """,
        """
        CREATE TABLE source_documents (
            id VARCHAR(36) PRIMARY KEY,
            filename VARCHAR(512),
            title VARCHAR(512),
            source_kind VARCHAR(32),
            content_hash VARCHAR(64),
            full_text TEXT,
            raw_text_chars INTEGER,
            source_guide JSON,
            extraction_status VARCHAR(32),
            extraction_error TEXT,
            created_at DATETIME
        )
        """,
        """
        CREATE TABLE document_chunks (
            id VARCHAR(36) PRIMARY KEY,
            source_id VARCHAR(36),
            ord INTEGER,
            text TEXT,
            heading_path VARCHAR(120),
            embedding JSON,
            embedding_model VARCHAR(80),
            created_at DATETIME
        )
        """,
        """
        CREATE TABLE kb_entity_links (
            id VARCHAR(36) PRIMARY KEY,
            src_entity_id VARCHAR(64),
            dst_entity_id VARCHAR(64),
            link_type VARCHAR(32),
            confidence FLOAT,
            evidence_refs JSON,
            created_at DATETIME
        )
        """,
        """
        CREATE TABLE experiment_records (
            id INTEGER PRIMARY KEY
        )
        """,
    ]
    try:
        with engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))
    finally:
        engine.dispose()


def test_legacy_db_gets_columns_via_migration(
    tmp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A legacy DB stamped at 0001 gains all new columns; legacy table is dropped."""
    _create_legacy_schema(tmp_db_url)
    # Seed rows so the updated_at backfill (0005) has data to repair.
    _exec(
        tmp_db_url,
        "INSERT INTO kb_entity_links (id, src_entity_id, dst_entity_id, link_type,"
        " confidence, evidence_refs, created_at) VALUES"
        " ('link-1', 'e1', 'e2', 'cites', 0.9, '[]', '2026-01-01 00:00:00')",
    )

    run_upgrade(tmp_db_url, monkeypatch)

    assert {"item_id", "project_id"} <= _column_names(tmp_db_url, "experiments")
    assert {
        "project_id",
        "primary_metric",
        "objectives_snapshot",
        "lever_snapshot",
        "sample_refs",
        "loop_history",
    } <= _column_names(tmp_db_url, "campaigns")
    assert {"origin_url", "project_id"} <= _column_names(tmp_db_url, "source_documents")
    assert {"page_no", "meta"} <= _column_names(tmp_db_url, "document_chunks")
    assert {
        "metadata_json",
        "is_valid",
        "extraction_method",
        "updated_at",
    } <= _column_names(tmp_db_url, "kb_entity_links")

    assert "experiment_records" not in _table_names(tmp_db_url)

    # Migrations 0002-0004 must also backfill the ORM ``index=True`` indexes
    # that the 0001 baseline only creates for fresh databases.
    assert {"ix_experiments_item_id", "ix_experiments_project_id"} <= _index_names(
        tmp_db_url, "experiments"
    )
    assert "ix_campaigns_project_id" in _index_names(tmp_db_url, "campaigns")
    assert {
        "ix_source_documents_origin_url",
        "ix_source_documents_project_id",
    } <= _index_names(tmp_db_url, "source_documents")

    # The 0005 backfill must leave no NULL updated_at rows behind.
    rows = _fetch_all(tmp_db_url, "SELECT updated_at FROM kb_entity_links")
    assert rows and all(row[0] is not None for row in rows)

    # Pin down the migrated column types so silent type drift fails loudly.
    experiments_types = _column_types(tmp_db_url, "experiments")
    assert "VARCHAR" in experiments_types["item_id"]
    assert "VARCHAR" in experiments_types["project_id"]
    campaigns_types = _column_types(tmp_db_url, "campaigns")
    assert "JSON" in campaigns_types["objectives_snapshot"]
    assert "JSON" in campaigns_types["lever_snapshot"]
    chunks_types = _column_types(tmp_db_url, "document_chunks")
    assert "INTEGER" in chunks_types["page_no"]
    assert "JSON" in chunks_types["meta"]
    links_types = _column_types(tmp_db_url, "kb_entity_links")
    assert "JSON" in links_types["metadata_json"]
    assert "BOOLEAN" in links_types["is_valid"]
    assert "VARCHAR" in links_types["extraction_method"]
    assert "DATETIME" in links_types["updated_at"]


def test_migrations_idempotent_on_fresh_db(
    tmp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`upgrade head` on an empty DB runs 0002-0006 as silent no-ops."""
    run_upgrade(tmp_db_url, monkeypatch)

    # Baseline 0001 already creates every column; the post-baseline
    # migrations must not raise and the full column set must be present.
    assert {"item_id", "project_id"} <= _column_names(tmp_db_url, "experiments")
    assert {
        "project_id",
        "primary_metric",
        "objectives_snapshot",
        "lever_snapshot",
        "sample_refs",
        "loop_history",
    } <= _column_names(tmp_db_url, "campaigns")
    assert {"origin_url", "project_id"} <= _column_names(tmp_db_url, "source_documents")
    assert {"page_no", "meta"} <= _column_names(tmp_db_url, "document_chunks")
    assert {
        "metadata_json",
        "is_valid",
        "extraction_method",
        "updated_at",
    } <= _column_names(tmp_db_url, "kb_entity_links")
    assert "experiment_records" not in _table_names(tmp_db_url)


def test_revision_chain_head_is_0092(tmp_db_url: str) -> None:
    """The revision chain is linear with a single head (``0092_stage5_data_semantics``)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ALEMBIC_INI))
    script = ScriptDirectory.from_config(cfg)

    heads = script.get_heads()
    assert len(heads) == 1, f"expected a single head, got {heads}"
    assert heads[0] == "0092"


def test_0090_allow_ttl_column_up_down(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """0090 adds mcp_tool_policies.expires_at and drops it on downgrade."""
    from tests.alembic_helpers import run_downgrade, run_upgrade

    run_upgrade(tmp_db_url, monkeypatch)
    assert "expires_at" in _column_names(tmp_db_url, "mcp_tool_policies")
    run_downgrade(tmp_db_url, monkeypatch, "0089")
    assert "expires_at" not in _column_names(tmp_db_url, "mcp_tool_policies")


def test_0091_indexes_up_down(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """0091 adds the stage-3 list/dashboard indexes and drops them on downgrade."""
    from tests.alembic_helpers import run_downgrade, run_upgrade

    def _index_names(table: str) -> set[str]:
        engine = create_engine(tmp_db_url)
        try:
            return {i["name"] for i in inspect(engine).get_indexes(table)}
        finally:
            engine.dispose()

    run_upgrade(tmp_db_url, monkeypatch)
    assert "ix_experiments_created_at" in _index_names("experiments")
    assert "ix_experiments_project_created" in _index_names("experiments")
    assert "ix_projects_archived_updated" in _index_names("projects")
    run_downgrade(tmp_db_url, monkeypatch, "0090")
    assert "ix_experiments_created_at" not in _index_names("experiments")
    assert "ix_experiments_project_created" not in _index_names("experiments")
    assert "ix_projects_archived_updated" not in _index_names("projects")


def test_0092_constraints_up_down(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """0092 adds the release CHECK and drops it on downgrade.

    The batch rebuild for the CHECK must not take the 0085 partial unique
    index down with it — asserted on both directions. (The
    classification composite unique key was removed from 0092 by
    decision: stacking is the accepted design.)
    """
    from tests.alembic_helpers import run_downgrade, run_upgrade

    def _checks(table: str) -> set[str]:
        engine = create_engine(tmp_db_url)
        try:
            return {c["name"] for c in inspect(engine).get_check_constraints(table)}
        finally:
            engine.dispose()

    def _index_names(table: str) -> set[str]:
        engine = create_engine(tmp_db_url)
        try:
            return {i["name"] for i in inspect(engine).get_indexes(table)}
        finally:
            engine.dispose()

    run_upgrade(tmp_db_url, monkeypatch)
    assert "ck_release_status" in _checks("regulatory_data_releases")
    assert "uq_approved_release_per_digest" in _index_names("regulatory_data_releases")
    run_downgrade(tmp_db_url, monkeypatch, "0091")
    assert "ck_release_status" not in _checks("regulatory_data_releases")
    assert "uq_approved_release_per_digest" in _index_names("regulatory_data_releases")


def test_migrations_partial_columns_branch(
    tmp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A legacy DB that already has *some* new columns upgrades cleanly.

    Simulates a database where ``experiments.item_id`` was added by hand
    before Alembic adoption: the guard must skip the existing column while
    still adding every remaining column and index.
    """
    _create_legacy_schema(tmp_db_url)
    _exec(tmp_db_url, "ALTER TABLE experiments ADD COLUMN item_id VARCHAR(128)")

    run_upgrade(tmp_db_url, monkeypatch)  # must not raise on the pre-existing column

    assert {"item_id", "project_id"} <= _column_names(tmp_db_url, "experiments")
    assert {
        "project_id",
        "primary_metric",
        "objectives_snapshot",
        "lever_snapshot",
        "sample_refs",
        "loop_history",
    } <= _column_names(tmp_db_url, "campaigns")
    assert {"origin_url", "project_id"} <= _column_names(tmp_db_url, "source_documents")
    assert {"page_no", "meta"} <= _column_names(tmp_db_url, "document_chunks")
    assert {
        "metadata_json",
        "is_valid",
        "extraction_method",
        "updated_at",
    } <= _column_names(tmp_db_url, "kb_entity_links")

    assert {"ix_experiments_item_id", "ix_experiments_project_id"} <= _index_names(
        tmp_db_url, "experiments"
    )
    assert "ix_campaigns_project_id" in _index_names(tmp_db_url, "campaigns")
    assert {
        "ix_source_documents_origin_url",
        "ix_source_documents_project_id",
    } <= _index_names(tmp_db_url, "source_documents")
    assert "experiment_records" not in _table_names(tmp_db_url)


def test_migration_0006_logs_row_count(
    tmp_db_url: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Dropping ``experiment_records`` logs a warning including the row count."""
    _create_legacy_schema(tmp_db_url)
    _exec(tmp_db_url, "INSERT INTO experiment_records (id) VALUES (1)")
    _exec(tmp_db_url, "INSERT INTO experiment_records (id) VALUES (2)")

    with caplog.at_level(logging.WARNING):
        run_upgrade(tmp_db_url, monkeypatch)

    assert "experiment_records" not in _table_names(tmp_db_url)
    drop_warnings = [
        record
        for record in caplog.records
        if "experiment_records" in record.getMessage() and "2" in record.getMessage()
    ]
    assert drop_warnings, (
        "expected a warning mentioning dropping experiment_records with 2 rows, "
        f"got: {[r.getMessage() for r in caplog.records]}"
    )


def test_make_engine_emits_no_alter_after_upgrade(
    tmp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After `alembic upgrade head`, make_engine must not emit any runtime ALTER.

    Schema drift repair is owned by Alembic migrations; make_engine is a
    read-only validator and must issue zero ALTER statements.
    """
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    run_upgrade(tmp_db_url, monkeypatch)

    from app.db.database import make_engine

    statements: list[str] = []

    @event.listens_for(Engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    try:
        engine = make_engine(tmp_db_url)
    finally:
        event.remove(Engine, "before_cursor_execute", _capture)
    try:
        alter_count = sum(1 for s in statements if "ALTER" in s.upper())
        assert alter_count == 0, (
            f"make_engine emitted {alter_count} ALTER statement(s): "
            f"{[s for s in statements if 'ALTER' in s.upper()]}"
        )
    finally:
        engine.dispose()


def test_validator_raises_actionable_on_legacy_db(tmp_db_url: str) -> None:
    """A legacy DB (missing item_id/project_id) must fail loudly with a
    pointer to `alembic upgrade head` instead of being silently ALTERed."""
    from app.db.database import make_engine

    engine = create_engine(tmp_db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE experiments (
                        id INTEGER PRIMARY KEY,
                        domain VARCHAR(64),
                        factors JSON,
                        cure_temperature_c FLOAT,
                        measured JSON,
                        source VARCHAR(64),
                        label VARCHAR(255),
                        created_at DATETIME
                    )
                    """
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="alembic upgrade head") as exc_info:
        make_engine(tmp_db_url)
    assert "experiments.item_id" in str(exc_info.value)


# ── migration 0014: doe_plans reference types ────────────────────────────────


def _create_legacy_doe_plans(db_url: str) -> None:
    """Build ``doe_plans`` in its pre-0014 shape: String reference columns.

    The 0001 baseline runs ``Base.metadata.create_all`` against the *current*
    ORM, so a database built by the migration chain already has the corrected
    integer columns and 0014 short-circuits. Only a hand-built legacy table
    exercises the conversion, which is the shape every deployed database is in.
    """
    _exec(
        db_url,
        """
        CREATE TABLE doe_plans (
            id VARCHAR(36) PRIMARY KEY,
            experiment_id VARCHAR(36),
            campaign_id VARCHAR(36),
            design_type VARCHAR(32) NOT NULL,
            parameters JSON NOT NULL,
            created_at DATETIME NOT NULL
        )
        """,
    )


def _foreign_keys(db_url: str, table: str) -> set[tuple[str, str]]:
    """Return ``{(constrained_column, referred_table)}`` for ``table``."""
    engine = create_engine(db_url)
    try:
        return {
            (fk["constrained_columns"][0], fk["referred_table"])
            for fk in inspect(engine).get_foreign_keys(table)
        }
    finally:
        engine.dispose()


def test_0014_converts_legacy_string_references_to_integers(
    tmp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real deployed database converts, keeps valid ids, and drops garbage."""
    _create_legacy_schema(tmp_db_url)
    _create_legacy_doe_plans(tmp_db_url)
    _exec(
        tmp_db_url,
        "INSERT INTO campaigns (id, name, strategy, status, created_at, updated_at)"
        " VALUES (7, 'c', 's', 'IN_PROGRESS', '2026-01-01 00:00:00',"
        " '2026-01-01 00:00:00')",
    )
    for plan_id, experiment_id, campaign_id in (
        ("p-uuid", "'8b4f-1c2d-dead-beef'", "'8b4f-1c2d-dead-beef'"),
        ("p-valid", "NULL", "'7'"),
        # Integer-shaped but pointing at a row that does not exist.
        ("p-dangling", "NULL", "'999'"),
        # A UUID whose leading digits collide with a real campaign id. On
        # SQLite the orphan sweep already rejects it (the column is still TEXT
        # when the sweep runs, and TEXT never compares equal to an integer id),
        # so this row does not discriminate between the two cleanup passes —
        # it is here because it is the input most likely to produce a *wrong*
        # link rather than a detectable orphan if either pass regresses.
        ("p-prefix-collision", "NULL", "'7f3a-1c2d-dead-beef'"),
    ):
        _exec(
            tmp_db_url,
            "INSERT INTO doe_plans VALUES"
            f" ('{plan_id}', {experiment_id}, {campaign_id}, 'lhs', '{{}}',"
            " '2026-01-01 00:00:00')",
        )

    # Pinned to 0076, not head: these fixture rows are deliberately
    # project-less (their whole point is the reference conversion), and
    # 0077's guard would refuse to tighten over them. The guard itself is
    # covered by test_p12_tightening; this test is about 0014 only.
    run_upgrade(tmp_db_url, monkeypatch, "0076")

    types = _column_types(tmp_db_url, "doe_plans")
    assert "INTEGER" in types["experiment_id"]
    assert "INTEGER" in types["campaign_id"]
    assert _foreign_keys(tmp_db_url, "doe_plans") == {
        ("experiment_id", "experiments"),
        ("campaign_id", "campaigns"),
    }
    # 0008's indexes must survive the table rebuild.
    assert {"ix_doe_plans_experiment", "ix_doe_plans_campaign"} <= _index_names(
        tmp_db_url, "doe_plans"
    )

    rows = dict(
        _fetch_all(tmp_db_url, "SELECT id, campaign_id FROM doe_plans ORDER BY id")
    )
    assert rows == {
        "p-uuid": None,
        "p-valid": 7,
        "p-dangling": None,
        "p-prefix-collision": None,
    }
    # SQLite rebuilds a table without re-checking foreign keys, so the only
    # proof the result is consistent is to ask it explicitly.
    assert _fetch_all(tmp_db_url, "PRAGMA foreign_key_check(doe_plans)") == []


def test_migration_0066_sys_admin_knowledge_read(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """0066 adds ``knowledge.read`` to the stored ``sys_admin`` seed row (live
    definition gained it in 6db1a65); downgrade removes it; re-upgrade is
    idempotent.

    (A fresh DB seeds ``sys_admin`` with ``knowledge.read`` already — the
    0051 file copy of ``_ROLES`` was updated in place to match live — so the
    migration is exercised through a downgrade first.)
    """
    import json

    from tests.alembic_helpers import run_downgrade

    def _sys_admin_perms() -> list[str]:
        rows = _fetch_all(
            tmp_db_url, "SELECT permissions FROM roles WHERE code='sys_admin'"
        )
        assert rows, "sys_admin seed row missing"
        perms = rows[0][0]
        return json.loads(perms) if isinstance(perms, str) else list(perms)

    run_upgrade(tmp_db_url, monkeypatch, "head")
    assert "knowledge.read" in _sys_admin_perms()

    run_downgrade(tmp_db_url, monkeypatch, "0065")
    assert "knowledge.read" not in _sys_admin_perms()

    # Re-upgrade is idempotent (twice).
    run_upgrade(tmp_db_url, monkeypatch, "head")
    run_upgrade(tmp_db_url, monkeypatch, "head")
    assert "knowledge.read" in _sys_admin_perms()


def test_migration_0074_builtin_role_new_permissions(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """0074 adds W0's ``chemical.read`` / ``plm.document.read`` to the stored
    built-in role rows (all seven gain ``chemical.read``; only the five
    business roles gain ``plm.document.read``); downgrade removes exactly
    those two strings; re-upgrade is idempotent and leaves custom rows
    untouched.

    (A fresh DB seeds the new permissions already — the 0051 file copy was
    updated in place to match live — so the old-database path is exercised
    by downgrading to 0073 first, which strips both strings, then
    re-upgrading: rows at 0073 without the new permissions are exactly what
    a pre-W0 database looks like.)
    """
    import json

    from tests.alembic_helpers import run_downgrade

    _BUSINESS = ("rd_engineer", "project_lead", "ehs_regulatory", "quality", "release_manager")
    _ALL = _BUSINESS + ("lab_technician", "sys_admin")

    def _role_perms(code: str) -> list[str]:
        rows = _fetch_all(
            tmp_db_url, f"SELECT permissions FROM roles WHERE code='{code}'"
        )
        assert rows, f"{code} seed row missing"
        perms = rows[0][0]
        return json.loads(perms) if isinstance(perms, str) else list(perms)

    def _assert_upgraded() -> None:
        for code in _ALL:
            perms = _role_perms(code)
            assert "chemical.read" in perms
            assert perms.count("chemical.read") == 1
            if code in _BUSINESS:
                assert "plm.document.read" in perms
                assert perms.count("plm.document.read") == 1
            else:
                assert "plm.document.read" not in perms

    def _assert_downgraded() -> None:
        for code in _ALL:
            perms = _role_perms(code)
            assert "chemical.read" not in perms
            assert "plm.document.read" not in perms

    run_upgrade(tmp_db_url, monkeypatch, "head")
    _assert_upgraded()

    run_downgrade(tmp_db_url, monkeypatch, "0073")
    _assert_downgraded()

    # A custom role row present at 0073 must survive the 0074 upgrade untouched.
    # (created_at is NOT NULL in the migrated DDL, so the row carries one.)
    _exec(
        tmp_db_url,
        "INSERT INTO roles (code, name_zh, name_en, permissions, builtin, created_at) "
        "VALUES ('custom_role', '自定义', 'Custom', '[\"knowledge.read\"]', 0, '2026-10-27 00:00:00')",
    )
    run_upgrade(tmp_db_url, monkeypatch, "head")
    _assert_upgraded()
    rows = _fetch_all(
        tmp_db_url, "SELECT permissions FROM roles WHERE code='custom_role'"
    )
    custom = rows[0][0]
    assert (json.loads(custom) if isinstance(custom, str) else list(custom)) == ["knowledge.read"]

    # Re-upgrade is idempotent (twice): no duplicated permission strings.
    run_upgrade(tmp_db_url, monkeypatch, "head")
    run_upgrade(tmp_db_url, monkeypatch, "head")
    _assert_upgraded()


def test_migration_0065_substances_name_lower_indexes(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """0065 adds the ``lower(name_zh)`` / ``lower(name_en)`` expression indexes on
    ``substances`` (case-insensitive local-library lookup stays index-backed);
    downgrade drops them; re-upgrade is idempotent.

    (A fresh DB already has the indexes at every revision — ``0001_baseline``
    runs ``create_all`` from the current models — so the migration is exercised
    through a downgrade first.)

    NOTE: SQLAlchemy's ``inspect().get_indexes()`` cannot reflect expression
    indexes, so the assertion reads ``sqlite_master`` directly.
    """
    from tests.alembic_helpers import run_downgrade

    def _all_index_names() -> set[str]:
        rows = _fetch_all(
            tmp_db_url, "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='substances'"
        )
        return {r[0] for r in rows}

    run_upgrade(tmp_db_url, monkeypatch, "head")
    assert {
        "ix_substances_name_zh_lower", "ix_substances_name_en_lower",
    } <= _all_index_names()

    run_downgrade(tmp_db_url, monkeypatch, "0064")
    assert not {
        "ix_substances_name_zh_lower", "ix_substances_name_en_lower",
    } & _all_index_names()

    # Re-upgrade is idempotent (twice).
    run_upgrade(tmp_db_url, monkeypatch, "head")
    run_upgrade(tmp_db_url, monkeypatch, "head")
    assert {
        "ix_substances_name_zh_lower", "ix_substances_name_en_lower",
    } <= _all_index_names()


def test_migration_0035_parser_column(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """P3-5: 0035 adds ``source_documents.parser``; downgrade drops it; re-upgrade idempotent."""
    from tests.alembic_helpers import run_downgrade

    run_upgrade(tmp_db_url, monkeypatch, "head")
    assert "parser" in _column_names(tmp_db_url, "source_documents")

    run_downgrade(tmp_db_url, monkeypatch, "0034")
    assert "parser" not in _column_names(tmp_db_url, "source_documents")

    # Re-upgrade is idempotent (twice).
    run_upgrade(tmp_db_url, monkeypatch, "head")
    run_upgrade(tmp_db_url, monkeypatch, "head")
    assert "parser" in _column_names(tmp_db_url, "source_documents")


def test_migration_0036_kb_coverage_counters(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """B-3: 0036 adds ``kb_coverage_counters``; downgrade drops it; re-upgrade idempotent."""
    from tests.alembic_helpers import run_downgrade

    run_upgrade(tmp_db_url, monkeypatch, "head")
    assert "kb_coverage_counters" in _table_names(tmp_db_url)

    run_downgrade(tmp_db_url, monkeypatch, "0035")
    assert "kb_coverage_counters" not in _table_names(tmp_db_url)

    # Re-upgrade is idempotent (twice).
    run_upgrade(tmp_db_url, monkeypatch, "head")
    run_upgrade(tmp_db_url, monkeypatch, "head")
    assert "kb_coverage_counters" in _table_names(tmp_db_url)


def test_migration_0043_adds_outbox_task_id(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """0043 adds ``task_outbox.task_id`` (replay under the client's Celery id); downgrade
    drops it; re-upgrade is idempotent.

    (A fresh DB already has the column at every revision — ``0001_baseline`` runs
    ``create_all`` from the current models — so the migration is exercised through a
    downgrade first.)
    """
    from tests.alembic_helpers import run_downgrade

    run_upgrade(tmp_db_url, monkeypatch, "head")
    assert "task_id" in _column_names(tmp_db_url, "task_outbox")

    run_downgrade(tmp_db_url, monkeypatch, "0042")
    assert "task_id" not in _column_names(tmp_db_url, "task_outbox")

    run_upgrade(tmp_db_url, monkeypatch, "head")
    run_upgrade(tmp_db_url, monkeypatch, "head")
    assert "task_id" in _column_names(tmp_db_url, "task_outbox")


def test_make_engine_adds_task_id_to_a_database_that_was_not_upgraded(
    tmp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every submission writes an outbox row; a model column the table lacks would make
    each async endpoint 500 until someone runs ``alembic upgrade head``."""
    import uuid

    from sqlalchemy.orm import Session

    from app.db.database import make_engine
    from app.db.models import TaskOutbox
    from tests.alembic_helpers import run_downgrade

    run_upgrade(tmp_db_url, monkeypatch, "head")
    run_downgrade(tmp_db_url, monkeypatch, "0042")  # the pre-0043 table shape
    assert "task_id" not in _column_names(tmp_db_url, "task_outbox")

    engine = make_engine(tmp_db_url)
    try:
        assert "task_id" in _column_names(tmp_db_url, "task_outbox")
        with Session(engine) as session:
            session.add(
                TaskOutbox(
                    id=str(uuid.uuid4()),
                    operation="doe_cycle",
                    idempotency_key="k",
                    payload={},
                    task_id="abc",
                )
            )
            session.commit()
            assert session.query(TaskOutbox).one().task_id == "abc"
    finally:
        engine.dispose()


def test_migration_0044_drops_the_unused_kg_formulation_links_table(
    tmp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing ever wrote or read ``kg_formulation_links``; a fresh database no longer has it,
    and the downgrade restores the shape revision 0020 created."""
    from tests.alembic_helpers import run_downgrade

    run_upgrade(tmp_db_url, monkeypatch, "head")
    assert "kg_formulation_links" not in _table_names(tmp_db_url)

    run_downgrade(tmp_db_url, monkeypatch, "0043")
    assert {
        "experiment_id",
        "entity_id",
        "role",
        "weight_pct",
        "link_type",
        "project_id",
        "created_at",
    } <= _column_names(tmp_db_url, "kg_formulation_links")

    run_upgrade(tmp_db_url, monkeypatch, "head")
    run_upgrade(tmp_db_url, monkeypatch, "head")  # repeatable
    assert "kg_formulation_links" not in _table_names(tmp_db_url)


def test_an_upgrade_from_before_the_table_existed_ends_in_the_same_place(
    tmp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0020 creates the table when the baseline did not (it no longer does); 0044 removes it."""
    from tests.alembic_helpers import run_downgrade

    run_upgrade(tmp_db_url, monkeypatch, "head")
    run_downgrade(tmp_db_url, monkeypatch, "0019_add_task_id_to_experiments")
    assert "kg_formulation_links" not in _table_names(tmp_db_url)

    run_upgrade(tmp_db_url, monkeypatch, "0020")
    assert "kg_formulation_links" in _table_names(tmp_db_url)

    run_upgrade(tmp_db_url, monkeypatch, "head")
    assert "kg_formulation_links" not in _table_names(tmp_db_url)


# ── the migrated schema and the models agree ─────────────────────────────────────────────────────────────────────────

# Objects the migrations create that no model declares. alembic 0019 added experiments.task_id / attempt_count (and an
# index on task_id) that nothing reads or writes; dropping columns from a live SQLite table means rebuilding it, which
# is not worth the risk for three unused objects. Anything else showing up here is real drift.
_KNOWN_MIGRATION_ONLY = {
    ("remove_column", "experiments", "task_id"),
    ("remove_column", "experiments", "attempt_count"),
    ("remove_index", "experiments", "ix_experiments_task_id"),
}


def _drift(diff) -> set[tuple[str, str, str]]:
    out = set()
    for entry in diff:
        for item in entry if isinstance(entry, list) else [entry]:
            kind = item[0]
            if kind == "remove_column":
                out.add((kind, item[2], item[3].name))
            elif kind in ("remove_index", "add_index"):
                out.add((kind, item[1].table.name, item[1].name))
            else:
                out.add((kind, str(item[1:])[:80], ""))
    return out


def test_a_migrated_database_has_the_same_objects_as_the_models(tmp_path, monkeypatch):
    """create_all (Docker, tests) and ``alembic upgrade head`` must leave the same tables, columns and indexes.

    They had drifted: two indexes existed only for migrated databases, so a database built by create_all was slower.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from app.db.database import Base
    import app.db.models  # noqa: F401  (registers every table)

    url = f"sqlite:///{tmp_path / 'm.db'}"
    run_upgrade(url, monkeypatch)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": False}), Base.metadata)
    finally:
        engine.dispose()
    assert _drift(diff) - _KNOWN_MIGRATION_ONLY == set()


def test_create_all_builds_the_indexes_the_migrations_add(tmp_path):
    from sqlalchemy import create_engine

    from app.db.database import Base
    import app.db.models  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}")
    try:
        Base.metadata.create_all(engine)
        insp = inspect(engine)
        assert "ix_document_chunks_source_offset" in {i["name"] for i in insp.get_indexes("document_chunks")}
        assert "ix_structure_fingerprints_fp_hex" in {i["name"] for i in insp.get_indexes("structure_fingerprints")}
    finally:
        engine.dispose()


def _create_0080_batch_schema(db_url: str, *, dirty: bool = False) -> None:
    """Full-form 0080-shaped schema: every model column, the product_id FK
    (table-level, ON DELETE CASCADE, as SQLAlchemy emits it), uq_batch and
    both indexes — everything except the 0081 revision FK. Stage 1 / SR-039:
    a minimal fixture cannot prove a batch rebuild preserves anything, so
    the fixture mirrors the models and the upgrade tests assert the whole
    FK/unique/index/row set, not just the added FK."""
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE products (id VARCHAR(36) NOT NULL PRIMARY KEY, code VARCHAR(64) NOT NULL, "
                "name_zh VARCHAR(200) NOT NULL, name_en VARCHAR(200) NOT NULL, "
                "category_identifier VARCHAR(8), legacy_code VARCHAR(64), project_id VARCHAR(64), "
                "lifecycle_state VARCHAR(16) NOT NULL, archived_from_state VARCHAR(16), "
                "created_by VARCHAR(36), created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"))
            # The model declares code unique=True AND index=True; SQLAlchemy
            # merges that into a single UNIQUE index (not a plain index plus
            # a separate constraint), so the fixture does the same.
            conn.execute(text("CREATE UNIQUE INDEX ix_products_code ON products (code)"))
            conn.execute(text("CREATE INDEX ix_products_project_id ON products (project_id)"))
            conn.execute(text("CREATE INDEX ix_products_lifecycle_state ON products (lifecycle_state)"))
            conn.execute(text(
                "CREATE TABLE product_revisions (id VARCHAR(36) NOT NULL PRIMARY KEY, "
                "product_id VARCHAR(36) NOT NULL, rev VARCHAR(8) NOT NULL, "
                "formulation_version_id VARCHAR(36), winning_experiment_id INTEGER, "
                "spec_snapshot JSON NOT NULL, state VARCHAR(16) NOT NULL, frozen_at DATETIME, "
                "created_by VARCHAR(36), created_at DATETIME NOT NULL, "
                "CONSTRAINT uq_product_revision UNIQUE (product_id, rev), "
                "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE, "
                "FOREIGN KEY(formulation_version_id) REFERENCES formulation_versions (id) ON DELETE SET NULL)"))
            conn.execute(text("CREATE INDEX ix_product_revisions_product_id ON product_revisions (product_id)"))
            conn.execute(text("CREATE INDEX ix_product_revisions_formulation_version_id "
                              "ON product_revisions (formulation_version_id)"))
            conn.execute(text("CREATE INDEX ix_product_revisions_state ON product_revisions (state)"))
            conn.execute(text(
                "CREATE TABLE batches (id VARCHAR(36) NOT NULL PRIMARY KEY, product_id VARCHAR(36) NOT NULL, "
                "revision_id VARCHAR(36), batch_no VARCHAR(64) NOT NULL, produced_at DATETIME, "
                "quantity_kg FLOAT, status VARCHAR(16) NOT NULL, created_at DATETIME NOT NULL, "
                "CONSTRAINT uq_batch UNIQUE (product_id, batch_no), "
                "FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE CASCADE)"))
            conn.execute(text("CREATE INDEX ix_batches_product_id ON batches (product_id)"))
            conn.execute(text("CREATE INDEX ix_batches_revision_id ON batches (revision_id)"))
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(text("INSERT INTO alembic_version VALUES ('0080')"))
            conn.execute(text(
                "INSERT INTO products VALUES ('p1', 'FX-100', '', '', NULL, NULL, NULL, 'rd', NULL, NULL, "
                "'2026-10-01', '2026-10-01'), ('p2', 'FX-200', '', '', NULL, NULL, NULL, 'rd', NULL, NULL, "
                "'2026-10-01', '2026-10-01')"))
            conn.execute(text("INSERT INTO product_revisions VALUES ('r1', 'p1', 'A', NULL, NULL, '{}', "
                              "'draft', NULL, NULL, '2026-10-01')"))
            conn.execute(text(
                "INSERT INTO batches VALUES ('b1', 'p1', 'r1', 'B-1', NULL, NULL, 'open', '2026-10-01'), "
                "('b2', 'p1', NULL, 'B-2', NULL, NULL, 'open', '2026-10-01')"))
            if dirty:
                conn.execute(text(
                    "INSERT INTO batches VALUES ('b3', 'p1', 'missing-rev', 'B-3', NULL, NULL, 'open', "
                    "'2026-10-01')"))
                conn.execute(text(
                    "INSERT INTO batches VALUES ('b4', 'p2', 'r1', 'B-4', NULL, NULL, 'open', '2026-10-01')"))
    finally:
        engine.dispose()


def _batch_fks(db_url: str) -> list[dict]:
    engine = create_engine(db_url)
    try:
        return inspect(engine).get_foreign_keys("batches")
    finally:
        engine.dispose()


def _batch_fk_set(db_url: str) -> set[tuple[str, str, str]]:
    """(constrained column, referred table, ondelete) for batches, from the inspector."""
    engine = create_engine(db_url)
    try:
        return {(fk["constrained_columns"][0], fk["referred_table"],
                 (fk.get("options") or {}).get("ondelete"))
                for fk in inspect(engine).get_foreign_keys("batches")}
    finally:
        engine.dispose()


def _batch_rows(db_url: str) -> list[tuple]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(
                "SELECT id, product_id, revision_id, batch_no FROM batches ORDER BY id")).all()
    finally:
        engine.dispose()


def test_0081_adds_batch_revision_fk(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """W1-11 + stage 1 / SR-039: the 0081 batch rebuild adds the revision FK
    and preserves the whole pre-existing set — product FK (with CASCADE),
    uq_batch, both indexes, and every row — in both directions."""
    from tests.alembic_helpers import run_downgrade

    _create_0080_batch_schema(tmp_db_url)
    assert _batch_fk_set(tmp_db_url) == {("product_id", "products", "CASCADE")}
    run_upgrade(tmp_db_url, monkeypatch)
    assert _batch_fk_set(tmp_db_url) == {("product_id", "products", "CASCADE"),
                                          ("revision_id", "product_revisions", "SET NULL")}
    assert _batch_rows(tmp_db_url) == [("b1", "p1", "r1", "B-1"), ("b2", "p1", None, "B-2")]
    engine = create_engine(tmp_db_url)
    try:
        assert "uq_batch" in {u["name"] for u in inspect(engine).get_unique_constraints("batches")}
        assert {"ix_batches_product_id", "ix_batches_revision_id"} <= {
            i["name"] for i in inspect(engine).get_indexes("batches")}
    finally:
        engine.dispose()
    run_downgrade(tmp_db_url, monkeypatch, "0080")
    assert _batch_fk_set(tmp_db_url) == {("product_id", "products", "CASCADE")}
    assert _batch_rows(tmp_db_url) == [("b1", "p1", "r1", "B-1"), ("b2", "p1", None, "B-2")]
    run_upgrade(tmp_db_url, monkeypatch)
    assert _batch_fk_set(tmp_db_url) == {("product_id", "products", "CASCADE"),
                                          ("revision_id", "product_revisions", "SET NULL")}


def test_0081_refuses_dirty_revision_ids(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """W1-11 gate 2: dangling or wrong-product revision_ids refuse the upgrade."""
    _create_0080_batch_schema(tmp_db_url, dirty=True)
    with pytest.raises(RuntimeError, match="0081 upgrade refused"):
        run_upgrade(tmp_db_url, monkeypatch)


def _create_0082_outbox_schema(db_url: str) -> None:
    """Minimal 0082-shaped schema: task_outbox without the error column, stamped at 0082.

    An empty database upgraded to 0082 cannot be used here: the 0001 baseline
    creates tables from the current models, so ``error`` would already exist.
    """
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE task_outbox (id VARCHAR(36) PRIMARY KEY, operation VARCHAR(64) NOT NULL, "
                "idempotency_key VARCHAR(128) NOT NULL, payload JSON NOT NULL, owner_id VARCHAR(64), "
                "status VARCHAR(16) NOT NULL, claimed_by VARCHAR(128), claimed_at DATETIME, "
                "attempt_count INTEGER NOT NULL, task_id VARCHAR(64), "
                "created_at DATETIME, updated_at DATETIME)"))
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(text("INSERT INTO alembic_version VALUES ('0082')"))
    finally:
        engine.dispose()


def test_0083_adds_outbox_error_column(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stage 0 / SR-002: upgrading an 0082 database adds task_outbox.error; downgrade drops it."""
    from tests.alembic_helpers import run_downgrade

    _create_0082_outbox_schema(tmp_db_url)
    assert "error" not in _column_names(tmp_db_url, "task_outbox")
    run_upgrade(tmp_db_url, monkeypatch)
    assert "error" in _column_names(tmp_db_url, "task_outbox")
    run_downgrade(tmp_db_url, monkeypatch, "0082")
    assert "error" not in _column_names(tmp_db_url, "task_outbox")
    run_upgrade(tmp_db_url, monkeypatch)


def _create_0083_conflict_schema(db_url: str) -> None:
    """Minimal 0083-shaped schema: eln_sync_conflicts without remote_units, stamped at 0083."""
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE eln_sync_conflicts (id VARCHAR(36) PRIMARY KEY, link_id VARCHAR(36) NOT NULL, "
                "local_snapshot JSON NOT NULL, remote_snapshot JSON NOT NULL, status VARCHAR(16) NOT NULL, "
                "resolution VARCHAR(16) NOT NULL, resolved_by VARCHAR(36), resolved_at DATETIME, "
                "created_at DATETIME)"))
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(text("INSERT INTO alembic_version VALUES ('0083')"))
    finally:
        engine.dispose()


def test_0084_adds_conflict_remote_units_column(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stage 1 / SR-051: upgrading an 0083 database adds eln_sync_conflicts.remote_units; downgrade drops it."""
    from tests.alembic_helpers import run_downgrade

    _create_0083_conflict_schema(tmp_db_url)
    assert "remote_units" not in _column_names(tmp_db_url, "eln_sync_conflicts")
    run_upgrade(tmp_db_url, monkeypatch)
    assert "remote_units" in _column_names(tmp_db_url, "eln_sync_conflicts")
    run_downgrade(tmp_db_url, monkeypatch, "0083")
    assert "remote_units" not in _column_names(tmp_db_url, "eln_sync_conflicts")
    run_upgrade(tmp_db_url, monkeypatch)


def _create_0084_release_schema(db_url: str) -> None:
    """Minimal 0084-shaped schema: regulatory_data_releases without the index, stamped at 0084."""
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE regulatory_data_releases (id VARCHAR(36) PRIMARY KEY, label VARCHAR(80), "
                "source VARCHAR(40), stats JSON, input_digest VARCHAR(64), created_at DATETIME, "
                "status VARCHAR(16), approved_by VARCHAR(36), approved_at DATETIME)"))
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(text("INSERT INTO alembic_version VALUES ('0084')"))
    finally:
        engine.dispose()


def test_0085_adds_approved_digest_index_and_refuses_duplicates(
    tmp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stage 1 / SR-030: upgrade adds the partial unique index; duplicates refuse the migration."""
    from tests.alembic_helpers import run_downgrade

    _create_0084_release_schema(tmp_db_url)
    run_upgrade(tmp_db_url, monkeypatch)
    engine = create_engine(tmp_db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO regulatory_data_releases VALUES ('r1', 'a', 'seed', '{}', 'd1', "
                "'2026-10-01', 'approved', NULL, NULL)"))
        with pytest.raises(Exception, match="duplicate|UNIQUE"):
            with engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO regulatory_data_releases VALUES ('r2', 'b', 'seed', '{}', 'd1', "
                    "'2026-10-02', 'approved', NULL, NULL)"))
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO regulatory_data_releases VALUES ('r3', 'c', 'seed', '{}', 'd1', "
                "'2026-10-02', 'draft', NULL, NULL)"))
    finally:
        engine.dispose()
    run_downgrade(tmp_db_url, monkeypatch, "0084")
    run_upgrade(tmp_db_url, monkeypatch)


def test_0085_refuses_when_approved_duplicates_exist(
    tmp_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stage 1 / SR-030 dirty guard: pre-existing duplicate approved digests stop the migration."""
    _create_0084_release_schema(tmp_db_url)
    engine = create_engine(tmp_db_url)
    try:
        with engine.begin() as conn:
            for rid in ("r1", "r2"):
                conn.execute(text(
                    f"INSERT INTO regulatory_data_releases VALUES ('{rid}', 'x', 'seed', '{{}}', 'd1', "
                    f"'2026-10-01', 'approved', NULL, NULL)"))
    finally:
        engine.dispose()
    with pytest.raises(RuntimeError, match="duplicate APPROVED"):
        run_upgrade(tmp_db_url, monkeypatch)


def _create_0085_transport_schema(db_url: str) -> None:
    """Minimal 0085-shaped schema: substance_transport without the index, stamped at 0085."""
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE substance_transport (id VARCHAR(36) PRIMARY KEY, un_number VARCHAR(8), "
                "substance_id VARCHAR(36), proper_shipping_name_zh VARCHAR(500), standard VARCHAR(40))"))
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(text("INSERT INTO alembic_version VALUES ('0085')"))
    finally:
        engine.dispose()


def _create_0086_transport_schema(db_url: str) -> None:
    """0086-shaped schema: substance_transport WITH the unique index, stamped at 0086."""
    _create_0085_transport_schema(db_url)
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE UNIQUE INDEX uq_transport_un_name_substance ON substance_transport "
                "(un_number, proper_shipping_name_zh, substance_id)"))
            conn.execute(text("UPDATE alembic_version SET version_num = '0086'"))
    finally:
        engine.dispose()


def test_0087_drops_transport_unique_index(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stage 1 / SR-034 correction: 0087 drops the 0086 index, and the
    packing-group variants the index wrongly blocked insert again."""
    from tests.alembic_helpers import run_downgrade

    _create_0086_transport_schema(tmp_db_url)
    run_upgrade(tmp_db_url, monkeypatch)
    engine = create_engine(tmp_db_url)
    try:
        assert "uq_transport_un_name_substance" not in {
            ix["name"] for ix in inspect(engine).get_indexes("substance_transport")}
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO substance_transport VALUES ('t1', '1170', 's1', '乙醇', 'GB 12268-2025')"))
            conn.execute(text(
                "INSERT INTO substance_transport VALUES ('t2', '1170', 's1', '乙醇', 'GB 12268-2025')"))
    finally:
        engine.dispose()
    # Downgrade re-adds the index, so one duplicate row must go first;
    # with duplicates present the re-add itself refuses (that refusal is
    # the documented downgrade semantics, not a test setup detail).
    engine = create_engine(tmp_db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM substance_transport WHERE id = 't2'"))
    finally:
        engine.dispose()
    run_downgrade(tmp_db_url, monkeypatch, "0086")
    engine = create_engine(tmp_db_url)
    try:
        assert "uq_transport_un_name_substance" in {
            ix["name"] for ix in inspect(engine).get_indexes("substance_transport")}
    finally:
        engine.dispose()
    run_upgrade(tmp_db_url, monkeypatch)
    engine = create_engine(tmp_db_url)
    try:
        assert "uq_transport_un_name_substance" not in {
            ix["name"] for ix in inspect(engine).get_indexes("substance_transport")}
    finally:
        engine.dispose()


def _structural_metadata_diffs(db_url: str, table_names: set[str]) -> list:
    """alembic compare_metadata diffs for a subset of the model metadata.

    The fixture database holds only the fixture tables, so the metadata
    is subset to them first (otherwise every other model table reports
    add_table). Index/unique naming diffs are excluded from the
    structural set: SQLite reports column-unique constraints under
    autoindex names, which compare_metadata flags even when the
    constraint itself matches — those are asserted directly via the
    inspector in the FK tests instead. alembic_version is not in the
    metadata by design and is ignored.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import MetaData

    import app.db.models  # noqa: F401  (registers every table)
    from app.db.database import Base

    subset = MetaData()
    # formulation_versions is copied first only so product_revisions'
    # FK to it resolves inside the subset; its own diffs are filtered
    # out below (the fixture database does not hold that table).
    if "product_revisions" in table_names:
        Base.metadata.tables["formulation_versions"].to_metadata(subset)
    for name in table_names:
        Base.metadata.tables[name].to_metadata(subset)
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            diffs = compare_metadata(MigrationContext.configure(conn), subset)
    finally:
        engine.dispose()
    structural = {"add_table", "remove_table", "add_column", "remove_column",
                  "add_fk", "remove_fk", "modify_type", "modify_nullable",
                  "modify_server_default", "modify_default"}

    def _unwrap(d):
        # Alembic wraps modify diffs in a one-element list around the
        # tuple; the tuple itself contains a dict (existing_type info)
        # and is therefore unhashable, so unwrap before kind checks.
        while isinstance(d, list) and len(d) == 1:
            d = d[0]
        return d

    diffs = [_unwrap(d) for d in diffs]

    def _diff_table(d):
        for el in d[1:]:
            if isinstance(el, str):
                return el
            table = getattr(el, "table", None)
            if table is not None:
                return table.name
            if hasattr(el, "name") and not isinstance(el, str):
                try:
                    return el.name
                except Exception:
                    pass
        return None

    return [d for d in diffs if d[0] in structural and _diff_table(d) in table_names]


def test_0081_upgrade_matches_model_metadata(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stage 1 / SR-039: legacy 0080 fixture -> upgrade head -> the three
    tables match the current models on tables, columns, FKs, types and
    nullability (the compare_metadata path, not just a hand-rolled FK list)."""
    _create_0080_batch_schema(tmp_db_url)
    run_upgrade(tmp_db_url, monkeypatch)
    assert _structural_metadata_diffs(
        tmp_db_url, {"products", "product_revisions", "batches"}) == []


def _create_0079_oel_schema(db_url: str, note: str) -> None:
    """Full-form 0079-shaped schema: substance_oel with 200-char notes,
    its FK to substances, uq_substance_oel and its index, stamped at 0079."""
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE substances (id VARCHAR(36) PRIMARY KEY)"))
            conn.execute(text(
                "CREATE TABLE substance_oel (id VARCHAR(36) PRIMARY KEY, substance_id VARCHAR(36) NOT NULL, "
                "standard VARCHAR(40) NOT NULL, mac FLOAT, pc_twa FLOAT, pc_stel FLOAT, "
                "critical_effect VARCHAR(300) NOT NULL, notes VARCHAR(200) NOT NULL, release_id VARCHAR(36), "
                "CONSTRAINT uq_substance_oel UNIQUE (substance_id, standard), "
                "FOREIGN KEY(substance_id) REFERENCES substances (id) ON DELETE CASCADE)"))
            conn.execute(text("CREATE INDEX ix_substance_oel_substance_id ON substance_oel (substance_id)"))
            conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
            conn.execute(text("INSERT INTO alembic_version VALUES ('0079')"))
            conn.execute(text("INSERT INTO substances VALUES ('s1')"))
            conn.execute(text("INSERT INTO substance_oel VALUES ('o1', 's1', 'GBZ 2.1-2019', NULL, NULL, "
                              f"NULL, '', '{note}', NULL)"))
    finally:
        engine.dispose()


def test_0080_widens_oel_notes_preserving_fk(tmp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stage 1 / SR-039 (0080 twin of the 0081 test): the 0080 batch
    rebuild widens notes to 2000 and preserves the FK, the unique key,
    the index and the stored row — upgrade and downgrade."""
    from tests.alembic_helpers import run_downgrade

    note = "n" * 150
    _create_0079_oel_schema(tmp_db_url, note)
    run_upgrade(tmp_db_url, monkeypatch, "0080")
    engine = create_engine(tmp_db_url)
    try:
        cols = {c["name"]: c for c in inspect(engine).get_columns("substance_oel")}
        assert cols["notes"]["type"].length == 2000
        fks = inspect(engine).get_foreign_keys("substance_oel")
        assert len(fks) == 1 and fks[0]["constrained_columns"] == ["substance_id"] \
            and fks[0]["referred_table"] == "substances" \
            and (fks[0].get("options") or {}).get("ondelete") == "CASCADE"
        assert "uq_substance_oel" in {u["name"] for u in inspect(engine).get_unique_constraints("substance_oel")}
        assert "ix_substance_oel_substance_id" in {i["name"] for i in inspect(engine).get_indexes("substance_oel")}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT notes FROM substance_oel WHERE id = 'o1'")).scalar() == note
    finally:
        engine.dispose()
    run_downgrade(tmp_db_url, monkeypatch, "0079")
    engine = create_engine(tmp_db_url)
    try:
        cols = {c["name"]: c for c in inspect(engine).get_columns("substance_oel")}
        assert cols["notes"]["type"].length == 200
        assert len(inspect(engine).get_foreign_keys("substance_oel")) == 1
    finally:
        engine.dispose()


def test_0080_downgrade_refuses_overlong_notes(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """0080's own guard: downgrade refuses while a note longer than 200 chars is stored."""
    from tests.alembic_helpers import run_downgrade

    url = f"sqlite:///{tmp_path}/oel.db"
    monkeypatch.setenv("FORMUNEX_DB_URL", url)
    _create_0079_oel_schema(url, "n" * 250)
    run_upgrade(url, monkeypatch, "0080")
    with pytest.raises(RuntimeError, match="0080 downgrade refused"):
        run_downgrade(url, monkeypatch, "0079")


def test_batch_rebuild_with_unresolved_fks_preserves_constraints(tmp_path):
    """Stage 1 / SR-039, empirical result for resolve_fks=False.

    Verdict: a SQLite batch rebuild with reflect_kwargs resolve_fks=False
    does NOT drop pre-existing FKs, unique keys, indexes or rows — the
    rebuild re-emits them from the reflected table. The same holds for
    the real 0081 migration (verified separately through alembic on an
    isolated 0080 database: product FK kept ON DELETE CASCADE).

    One artifact to know about, NOT a resolve_fks effect: when the
    original DDL declares the FK inline as a column REFERENCES clause
    (which SQLAlchemy never emits — it always uses table-level FK
    constraints), the reflected rebuild drops the ON DELETE action.
    With table-level FK clauses, as this codebase emits, CASCADE/SET NULL
    survive even with resolve_fks=False. No migration change follows
    from this: 0067–0081 stay as shipped. On PostgreSQL the question is
    moot — batch mode there renders a plain ALTER TABLE ADD CONSTRAINT
    with no table rebuild at all.
    """
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    url = f"sqlite:///{tmp_path}/batch.db"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE substances (id VARCHAR(36) PRIMARY KEY)"))
        conn.execute(text(
            "CREATE TABLE substance_oel (id VARCHAR(36) PRIMARY KEY, substance_id VARCHAR(36) NOT NULL, "
            "standard VARCHAR(40) NOT NULL, notes VARCHAR(200) NOT NULL, "
            "CONSTRAINT uq_substance_oel UNIQUE (substance_id, standard), "
            "FOREIGN KEY(substance_id) REFERENCES substances (id) ON DELETE CASCADE)"))
        conn.execute(text("CREATE INDEX ix_substance_oel_substance_id ON substance_oel (substance_id)"))
        conn.execute(text("INSERT INTO substances VALUES ('s1')"))
        conn.execute(text("INSERT INTO substance_oel VALUES ('o1', 's1', 'GBZ 2.1-2019', 'n')"))
    with engine.begin() as conn:
        op = Operations(MigrationContext.configure(conn))
        with op.batch_alter_table("substance_oel", reflect_kwargs={"resolve_fks": False}) as batch:
            batch.alter_column("notes", type_=sa.String(2000), existing_type=sa.String(200),
                               existing_nullable=False)
    try:
        cols = {c["name"]: c for c in inspect(engine).get_columns("substance_oel")}
        assert cols["notes"]["type"].length == 2000
        fks = inspect(engine).get_foreign_keys("substance_oel")
        assert len(fks) == 1 and (fks[0].get("options") or {}).get("ondelete") == "CASCADE"
        assert "uq_substance_oel" in {u["name"] for u in inspect(engine).get_unique_constraints("substance_oel")}
        assert "ix_substance_oel_substance_id" in {i["name"] for i in inspect(engine).get_indexes("substance_oel")}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM substance_oel")).scalar() == 1
    finally:
        engine.dispose()
