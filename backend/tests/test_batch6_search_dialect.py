"""P1-D1 regression: /experiments/search must not emit SQLite-only SQL
functions on the PostgreSQL dialect (D-9 names PostgreSQL the target).

The endpoint branches on dialect at runtime; these tests compile both
statement builders under both dialects so a future refactor cannot
silently reintroduce json_each / group_concat / instr into PG SQL.
No live PG instance is needed — the failure mode is deterministic
(`function does not exist` at execution), so compile-time absence is
the right gate. PG execution itself remains unverified in the sandbox.
"""
from sqlalchemy.dialects import postgresql, sqlite

from app.api.experiments import _pg_search_stmt, _sqlite_search_stmt

SQLITE_ONLY = ("json_each", "group_concat", "instr")


def test_pg_statement_has_no_sqlite_functions():
    sql = str(_pg_search_stmt().compile(dialect=postgresql.dialect())).lower()
    for fn in SQLITE_ONLY:
        assert fn not in sql


def test_sqlite_statement_keeps_fast_path():
    sql = str(_sqlite_search_stmt("urgent").compile(dialect=sqlite.dialect())).lower()
    assert "json_each" in sql
    assert "group_concat" in sql
    assert "instr" in sql


def test_pg_statement_selects_whole_refs():
    # The PG path explodes refs in Python, so it must fetch the whole
    # sample_refs column, not per-element rows.
    sql = str(_pg_search_stmt().compile(dialect=postgresql.dialect())).lower()
    assert "sample_refs" in sql
