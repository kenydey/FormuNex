"""Production startup self-checks: fail-fast on truly required configuration.

Only checks that are cheap (no network) and deterministic belong here, so the
lifespan manager can run them unconditionally — including under the test
fast-path. Anything optional degrades to a logged warning instead of a hard
failure.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_SUPPORTED_DB_SCHEMES = ("sqlite", "postgresql", "postgres")


def _sqlite_file_path(db_url: str) -> Path | None:
    """Return the filesystem path for a sqlite ``db_url``, else None."""
    parsed = urlparse(db_url)
    if parsed.scheme != "sqlite":
        return None
    # sqlite:///./data/formunex.db -> relative "data/formunex.db"
    # sqlite:///data/formunex.db   -> relative "data/formunex.db"
    # sqlite:////abs/path.db        -> absolute "/abs/path.db"
    # sqlite:///:memory:            -> ":memory:" (no filesystem check needed)
    path = parsed.path
    if path.startswith("//"):
        return Path(path[1:])
    if parsed.netloc in ("", ".", "localhost") and path.startswith("/"):
        path = path[1:]
    elif parsed.netloc and parsed.netloc not in (".", "localhost"):
        path = f"//{parsed.netloc}{path}"
    return Path(path or "formunex.db")


def run_startup_checks(settings) -> list[str]:
    """Validate required startup configuration.

    Raises:
        RuntimeError: on a hard misconfiguration, with an actionable message
            naming the variable and where to fix it.
    Returns:
        A list of non-fatal warning strings (caller logs them).
    """
    warnings: list[str] = []
    errors: list[str] = []

    # 1. db_url must use a supported scheme.
    db_url = (getattr(settings, "db_url", "") or "").strip()
    # SQLAlchemy permits a driver-qualified scheme (postgresql+psycopg2://).
    # The driver is an implementation detail, not a different database, so strip
    # it before matching — docker-compose and .env.example both ship that form.
    scheme = urlparse(db_url).scheme.lower().split("+", 1)[0]
    if scheme not in _SUPPORTED_DB_SCHEMES:
        errors.append(
            f"FORMUNEX_DB_URL has unsupported scheme {scheme!r} "
            f"(url={db_url!r}); supported: sqlite://, postgresql://. "
            "Fix: set FORMUNEX_DB_URL in .env or the environment."
        )

    # 2. sqlite: the database directory must exist and be writable.
    sqlite_path = _sqlite_file_path(db_url) if not errors else None
    if sqlite_path is not None:
        parent = sqlite_path.parent.resolve()
        if not parent.is_dir():
            errors.append(
                f"SQLite data directory {parent} does not exist "
                f"(from FORMUNEX_DB_URL={db_url!r}). "
                "Fix: create it, or mount the data volume (see docs/deployment.md)."
            )
        elif not os.access(parent, os.W_OK):
            errors.append(
                f"SQLite data directory {parent} is not writable "
                f"(from FORMUNEX_DB_URL={db_url!r}). "
                "Fix: check ownership/permissions or the container volume mount."
            )

    # 3. ELN-mandatory deploys must point at a real Datalab URL.
    if getattr(settings, "datalab_required", False):
        url = (getattr(settings, "datalab_api_url", "") or "").strip()
        if not url:
            errors.append(
                "FORMUNEX_DATALAB_REQUIRED=true but FORMUNEX_DATALAB_API_URL "
                "is empty. Fix: set the Datalab URL in .env, or set "
                "FORMUNEX_DATALAB_REQUIRED=false to fall back to the local ledger."
            )

    # 4. Auth explicitly disabled in production is almost certainly a mistake
    #    (config auto-enables it for production envs).
    env = (getattr(settings, "environment", "") or "").strip().lower()
    if env in ("production", "prod") and getattr(settings, "api_auth_enabled", None) is False:
        warnings.append(
            "FORMUNEX_API_AUTH_ENABLED=false with FORMUNEX_ENVIRONMENT=production: "
            "the API is unauthenticated. Set a FORMUNEX_API_TOKEN and enable auth "
            "unless this host is truly isolated."
        )

    # 5. The Neo4j password compose falls back to is published in the repository.
    if env in ("production", "prod") and getattr(settings, "neo4j_enabled", False):
        from .services import neo4j_kg

        if neo4j_kg.uses_default_password():
            warnings.append(
                "FORMUNEX_NEO4J_ENABLED=true with FORMUNEX_ENVIRONMENT=production but "
                "FORMUNEX_NEO4J_PASSWORD is unset or the shipped default: anyone who can reach "
                "the graph database can read it. Set a strong password in .env before the "
                "first start of the kg service (Neo4j stores it at initialisation; an existing "
                "install must change it in the database first)."
            )

    # 6. Account sign-in (M5) signs access tokens with FORMUNEX_AUTH_JWT_SECRET; production refuses the
    #    generated development secret, so sign-in would be unavailable.
    jwt_secret = (getattr(settings, "auth_jwt_secret", "") or "").strip()
    if env in ("production", "prod") and len(jwt_secret) < 32:
        warnings.append(
            "FORMUNEX_AUTH_JWT_SECRET is unset or shorter than 32 characters with FORMUNEX_ENVIRONMENT=production: "
            "account sign-in and e-signatures are unavailable. Set a long random value in .env "
            "(e.g. `openssl rand -base64 48`)."
        )

    # 7. SQLite without a Redis URL: the cross-process write lock in
    #    db/sqlite_lock.py degrades to a silent no-op for an empty URL
    #    (it yields unlocked without logging), so uvicorn + celery writers
    #    fall back to busy_timeout + retry. Surface it at startup instead.
    #    :memory: has no cross-process contention by construction.
    #    (An unreachable Redis is a different case: the lock attempt itself
    #    logs "Redis write lock unavailable" at lock time.)
    if (
        scheme == "sqlite"
        and sqlite_path is not None
        and str(sqlite_path) != ":memory:"
        and not (getattr(settings, "redis_url", "") or "").strip()
    ):
        warnings.append(
            "SQLite db_url with FORMUNEX_REDIS_URL unset/empty: the cross-process "
            "SQLite write lock is disabled, so concurrent writers (uvicorn + celery) "
            "fall back to busy_timeout + retry. Set FORMUNEX_REDIS_URL, or point "
            "FORMUNEX_DB_URL at PostgreSQL for multi-process deployments."
        )

    if errors:
        raise RuntimeError("Startup configuration check failed:\n- " + "\n- ".join(errors))
    return warnings


# ── Stage 1 / SR-036: database schema checks (lifespan only, NOT in
# run_startup_checks) ──────────────────────────────────────────────────────
# run_startup_checks is also called directly by unit tests against the
# development database, which legitimately sits behind head (0083 while
# head is 0086) — a refusal there would break the test suite. These
# checks therefore live here as a separate function that lifespan calls
# only when the test fast-path (FORMUNEX_SKIP_LIFESPAN_BOOTSTRAP) is off,
# i.e. in real deployments. They are read-only SELECTs plus inspector
# reads; they never migrate, repair or delete anything.

_BUILTIN_ROLE_CODES = (
    "rd_engineer", "project_lead", "lab_technician", "ehs_regulatory",
    "quality", "release_manager", "sys_admin",
)

# (table, columns) the 0075–0081 migrations added; a second line of
# defence behind the alembic version stamp, catching half-applied
# migrations. Tables that do not exist at all are skipped — a fresh
# create_all database has them from the models, and a partial test
# database is not a deployment.
_STAGE1_REQUIRED_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("parse_shards", ("content_sha256", "shard_index", "page_start", "page_end",
                       "parser", "markdown", "extras_json", "updated_at")),
    ("regulatory_data_releases", ("status", "approved_by", "approved_at")),
    ("products", ("archived_from_state",)),
    ("batches", ("revision_id",)),
    ("eln_sync_conflicts", ("remote_units",)),
)

# (table, index) the 0078/0085 migrations added. The 0086 transport
# index is deliberately absent: migration 0087 reverted it (the key
# does not hold on the shipped GB 12268 data).
_STAGE1_REQUIRED_INDEXES: tuple[tuple[str, str], ...] = (
    ("release_packages", "uq_active_package_per_revision"),
    ("regulatory_data_releases", "uq_approved_release_per_digest"),
)

# (table, column) the 0077 tightening made NOT NULL.
_STAGE1_REQUIRED_NOT_NULL: tuple[tuple[str, str], ...] = (
    ("doe_plans", "project_id"),
    ("lab_tasks", "project_id"),
)


def _schema_drift(engine) -> list[str]:
    """Collect 0075–0086 schema drift on an existing database (read-only)."""
    from sqlalchemy import inspect

    drift: list[str] = []
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, columns in _STAGE1_REQUIRED_COLUMNS:
        if table not in tables:
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        for col in columns:
            if col not in existing:
                drift.append(f"{table}.{col} missing")
    for table, index in _STAGE1_REQUIRED_INDEXES:
        if table not in tables:
            continue
        if index not in {ix["name"] for ix in inspector.get_indexes(table)}:
            drift.append(f"{table} index {index} missing")
    for table, col in _STAGE1_REQUIRED_NOT_NULL:
        if table not in tables:
            continue
        for c in inspector.get_columns(table):
            if c["name"] == col and c.get("nullable", True):
                drift.append(f"{table}.{col} is nullable (0077 requires NOT NULL)")
    if "substance_oel" in tables:
        for c in inspector.get_columns("substance_oel"):
            if c["name"] == "notes":
                length = getattr(c["type"], "length", None)
                if length is not None and length < 2000:
                    drift.append(f"substance_oel.notes width {length} < 2000 (0080)")
    return drift


def run_startup_db_checks(settings, engine=None) -> list[str]:
    """Verify the database is at the alembic head and matches 0075–0086.

    Raises:
        RuntimeError: when the stamped version is behind/ahead of head
            (default) or when schema drift is found, with the actionable
            fix (``alembic upgrade head``). A stamped version is the
            deployment's own claim; a fresh database without an
            ``alembic_version`` row (create_all from current models) is
            skipped, not refused.
    Returns:
        Warning strings (opt-out downgrade, unreachable database,
        missing builtin role rows). An unreachable database only warns:
        refusing here would also refuse deploys whose database starts
        after the API, and the first real query still fails fast.
    """
    from sqlalchemy import create_engine, text

    warnings: list[str] = []
    db_url = (getattr(settings, "db_url", "") or "").strip()
    own_engine = engine is None
    if own_engine:
        connect_args = {}
        if db_url.startswith("postgresql"):
            connect_args = {"connect_timeout": 5}
        try:
            engine = create_engine(db_url, connect_args=connect_args)
        except Exception as exc:
            return [f"startup db check skipped: cannot build an engine for the database ({exc})"]
    try:
        from sqlalchemy import inspect as sa_inspect

        inspector_tables = set(sa_inspect(engine).get_table_names())
    except Exception as exc:
        if own_engine:
            engine.dispose()
        return [f"startup db check skipped: database unreachable ({exc}); "
                "the first real query will fail fast if it stays down"]
    try:
        if "alembic_version" not in inspector_tables:
            return warnings  # fresh create_all database: schema comes from models
        with engine.connect() as conn:
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        ini = Path(__file__).resolve().parent.parent / "alembic.ini"
        if not ini.is_file():
            warnings.append("startup db check skipped: alembic.ini not found, head unknown")
        else:
            from alembic.config import Config
            from alembic.script import ScriptDirectory

            heads = ScriptDirectory.from_config(Config(str(ini))).get_heads()
            if len(heads) != 1 or current not in heads:
                msg = (f"database alembic version {current!r} is not at head {heads}; "
                       "refusing startup. Fix: run `alembic upgrade head` against "
                       "FORMUNEX_DB_URL, then restart. (Set "
                       "FORMUNEX_STARTUP_ALEMBIC_HEAD_CHECK=false only to downgrade "
                       "this refusal to a warning.)")
                if getattr(settings, "startup_alembic_head_check", True):
                    raise RuntimeError(msg)
                warnings.append(msg)
        drift = _schema_drift(engine)
        if drift:
            raise RuntimeError(
                "schema drift: " + "; ".join(drift) +
                ". Fix: run `alembic upgrade head` against FORMUNEX_DB_URL, then restart."
            )
        if "roles" in inspector_tables:
            with engine.connect() as conn:
                have = {r[0] for r in conn.execute(text("SELECT code FROM roles"))}
            missing = [c for c in _BUILTIN_ROLE_CODES if c not in have]
            if missing:
                warnings.append(
                    "builtin role rows missing from the roles table (0074): " +
                    ", ".join(missing) + ". Built-in roles added after 0074 will not "
                    "resolve until the rows are seeded."
                )
    finally:
        if own_engine:
            engine.dispose()
    return warnings
