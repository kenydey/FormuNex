"""FormuNex migrations 0051-0061 on a database that predates them.

``0001_baseline`` builds every *current* table with ``create_all``, so an
upgrade from empty never exercises the frozen ``op.create_table`` bodies of
the new revisions. These tests rebuild a genuine FormuMind-era (0050) schema
by stripping the FormuNex objects, then upgrade it and compare with the models.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.alembic_helpers import run_downgrade, run_upgrade

NEW_TABLES = [
    # dependants first, so foreign keys never dangle while dropping
    "eln_sync_conflicts", "measurement_picks", "measurement_amendments",
    "user_sessions", "release_gate_waivers",
    "eln_links", "samples", "lab_tasks", "instruments", "test_methods",
    "e_signatures", "approval_steps", "approval_flows", "release_packages", "audit_log",
    "batch_qc_results", "regulatory_documents", "batches", "doc_templates",
    "lineage_edges", "classification_results", "product_revisions", "products",
    "document_toc_trees",
    "material_compositions",
    "regulatory_review_queue", "substance_generic_entries", "ghs_statements", "substance_transport",
    "substance_tox_records", "substance_eco_records", "substance_eco_profiles",
    "substance_oel", "substance_classifications", "substances", "regulatory_data_releases",
    "user_roles", "project_members", "roles", "users",
]
NEW_COLUMNS = {
    "materials": ["material_type", "phys_state", "regulatory_status", "sds_document_id"],
    "measurements": [
        "sample_id", "lab_task_id", "method_id", "instrument_id", "operator_id", "raw_file_key",
        "review_status", "reviewed_by", "reviewed_at", "external_ref",
    ],
}


def _strip_to_0050(url: str) -> None:
    """Remove every object 0051-0061 own, leaving a FormuMind-era schema stamped at 0050."""
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for t in NEW_TABLES:
                conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
            insp = inspect(conn)
            for table, cols in NEW_COLUMNS.items():
                for ix in insp.get_indexes(table):
                    if set(ix["column_names"]) & set(cols):
                        conn.execute(text(f"DROP INDEX {ix['name']}"))
                for col in cols:
                    conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {col}"))
    finally:
        engine.dispose()


@pytest.fixture
def legacy_url(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    run_upgrade(url, monkeypatch, "0050")
    _strip_to_0050(url)
    engine = create_engine(url)
    try:
        insp = inspect(engine)
        assert "users" not in insp.get_table_names()
        assert "review_status" not in {c["name"] for c in insp.get_columns("measurements")}
    finally:
        engine.dispose()
    return url


def test_upgrade_from_0050_matches_models(legacy_url, monkeypatch):
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    import app.db.models  # noqa: F401  (registers every table)
    from app.db.database import Base

    run_upgrade(legacy_url, monkeypatch)
    engine = create_engine(legacy_url)
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": False}), Base.metadata)
    finally:
        engine.dispose()
    new = set(NEW_TABLES) | set(NEW_COLUMNS)

    def touches_new(entry) -> bool:
        # Diff entries are tuples like ("add_table", Table), ("add_index", Index),
        # ("add_column", schema, table_name, Column); match on the table they belong to.
        for item in entry if isinstance(entry, tuple) else (entry,):
            if isinstance(item, str) and item in new:
                return True
            table = getattr(item, "table", None)
            if getattr(table, "name", None) in new:
                return True
            if item.__class__.__name__ == "Table" and getattr(item, "name", None) in new:
                return True
        return False

    drift = [d for d in diff if touches_new(d[0] if isinstance(d, list) else d)]
    assert drift == []


def test_builtin_roles_seeded_once(legacy_url, monkeypatch):
    run_upgrade(legacy_url, monkeypatch)
    run_downgrade(legacy_url, monkeypatch, "0050")
    run_upgrade(legacy_url, monkeypatch)
    engine = create_engine(legacy_url)
    try:
        with engine.connect() as conn:
            codes = [r[0] for r in conn.execute(text("SELECT code FROM roles ORDER BY code"))]
    finally:
        engine.dispose()
    assert codes == sorted([
        "rd_engineer", "project_lead", "lab_technician", "ehs_regulatory", "quality", "release_manager",
        "sys_admin",
    ])


def test_legacy_owners_become_inactive_users(legacy_url, monkeypatch):
    engine = create_engine(legacy_url)
    try:
        with engine.begin() as conn:
            for owner in ("alice", "bob", "alice", None):
                conn.execute(
                    text(
                        "INSERT INTO experiments (domain, project_id, owner_id, factors, measured, source, label, "
                        "created_at) VALUES ('coating', '', :o, '{}', '{}', 'lab', '', '2026-01-01')"
                    ),
                    {"o": owner},
                )
    finally:
        engine.dispose()
    run_upgrade(legacy_url, monkeypatch)
    engine = create_engine(legacy_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT username, auth_provider, is_active, password_hash FROM users ORDER BY username")
            ).all()
    finally:
        engine.dispose()
    assert [tuple(r) for r in rows] == [("alice", "legacy_token", 0, None), ("bob", "legacy_token", 0, None)]


def test_audit_log_is_append_only(legacy_url, monkeypatch):
    run_upgrade(legacy_url, monkeypatch)
    engine = create_engine(legacy_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO audit_log (ts, actor, action, entity_type, entity_id, detail, prev_hash, hash) "
                    "VALUES ('2026-01-01', 'u', 'create', 'product', 'p1', '{}', :z, :h)"
                ),
                {"z": "0" * 64, "h": "a" * 64},
            )
        for stmt in ("UPDATE audit_log SET actor = 'mallory'", "DELETE FROM audit_log"):
            with pytest.raises(Exception, match="append-only"):
                with engine.begin() as conn:
                    conn.execute(text(stmt))
        with engine.connect() as conn:
            assert conn.execute(text("SELECT actor FROM audit_log")).scalar_one() == "u"
    finally:
        engine.dispose()


def test_append_only_guard_also_on_fresh_database(tmp_path, monkeypatch):
    """On a fresh DB the baseline creates audit_log; 0057 must still install the trigger."""
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    run_upgrade(url, monkeypatch)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            triggers = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))}
    finally:
        engine.dispose()
    assert {"audit_log_no_update", "audit_log_no_delete"} <= triggers


def test_0068_regulatory_document_unique_key_includes_batch_id(legacy_url, monkeypatch):
    """P1-20: after upgrading past 0068 the regulatory_documents unique key
    carries batch_id, and downgrading to 0067 + re-upgrading is clean.
    G2 (0071) extends the same key with regulation; the batch_id part of
    the assertion is unchanged."""
    run_upgrade(legacy_url, monkeypatch)
    engine = create_engine(legacy_url)
    try:
        insp = inspect(engine)
        uqs = {c["name"]: list(c["column_names"]) for c in insp.get_unique_constraints("regulatory_documents")}
        assert uqs["uq_regulatory_document"] == ["revision_id", "doc_type", "lang", "batch_id",
                                                "regulation", "version_no"]
    finally:
        engine.dispose()
    run_downgrade(legacy_url, monkeypatch, "0067")
    engine = create_engine(legacy_url)
    try:
        insp = inspect(engine)
        uqs = {c["name"]: list(c["column_names"]) for c in insp.get_unique_constraints("regulatory_documents")}
        assert uqs["uq_regulatory_document"] == ["revision_id", "doc_type", "lang", "version_no"]
    finally:
        engine.dispose()
    run_upgrade(legacy_url, monkeypatch)


def test_downgrade_to_0050_removes_formunex_objects(legacy_url, monkeypatch):
    run_upgrade(legacy_url, monkeypatch)
    run_downgrade(legacy_url, monkeypatch, "0050")
    engine = create_engine(legacy_url)
    try:
        insp = inspect(engine)
        tables = set(insp.get_table_names())
        assert not (set(NEW_TABLES) & tables)
        for table, cols in NEW_COLUMNS.items():
            assert not (set(cols) & {c["name"] for c in insp.get_columns(table)})
    finally:
        engine.dispose()


def test_0069_nulls_noncat_catalog_numbers(tmp_path, monkeypatch):
    """P1-23 data repair: upgrading past 0069 NULLs the file row numbers
    wrongly seeded as catalogue 序号 for the 29 non-catalogue CAS, leaves
    a genuine catalogue substance untouched, and re-upgrading is clean."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "m0069",
        Path("app/db/alembic/versions/0069_noncat_catalog_numbers.py"),
    )
    assert spec is not None and spec.loader is not None
    m0069 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m0069)

    url = f"sqlite:///{tmp_path / 'repair.db'}"
    run_upgrade(url, monkeypatch, "0068")
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for i, cas in enumerate(m0069._NONCATALOGUE_CAS):
                conn.execute(
                    text(
                        "INSERT INTO substances (id, cas_no, name_zh, name_en, aliases, hazardous_catalog_no, created_at, updated_at) "
                        "VALUES (:id, :cas, :zh, :en, '[]', :no, '2026-01-01', '2026-01-01')"
                    ),
                    {"id": f"n{i:02d}", "cas": cas, "zh": f"非目录{i}", "en": "", "no": 2700 + i},
                )
            conn.execute(
                text(
                    "INSERT INTO substances (id, cas_no, name_zh, name_en, aliases, hazardous_catalog_no, created_at, updated_at) "
                    "VALUES ('ctl', '8008-60-4', '阿片', '', '[]', 1, '2026-01-01', '2026-01-01')"
                ),
            )
    finally:
        engine.dispose()
    run_upgrade(url, monkeypatch)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            wrong = conn.execute(
                text(
                    "SELECT COUNT(*) FROM substances WHERE cas_no IN "
                    "(SELECT cas_no FROM substances) AND hazardous_catalog_no IS NOT NULL "
                    "AND cas_no != '8008-60-4'"
                )
            ).scalar_one()
            assert wrong == 0
            assert (
                conn.execute(
                    text("SELECT hazardous_catalog_no FROM substances WHERE cas_no = '8008-60-4'")
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()
    run_upgrade(url, monkeypatch)
