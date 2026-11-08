"""Transient shard-result cache for sharded PDF parsing (batch 1).

A sharded parse caches each completed shard's markdown keyed by content
SHA-256 + shard index. A crashed or retried parse of the same bytes then
resumes from finished shards instead of re-OCRing them. Rows are deleted
after a successful merge; crash residue is swept opportunistically.

Fail-open by contract (same as ``persist_structured``): any DB error —
missing table on a database that has not run migration 0075 yet, a locked
database, an unreachable engine — behaves as a cache miss and only logs a
warning. A cache bug must never fail a parse.

Stage 2 / SR-037: the SQL is dialect-neutral (SQLAlchemy ``text()`` with
named parameters; no ``?`` placeholders, no ``INSERT OR REPLACE``), so
the cache works on PostgreSQL exactly as on SQLite. ``updated_at`` is
written as a naive-UTC datetime — the same convention as every other
database column (``app.clock.utcnow``) — instead of an aware ISO string,
so the stale sweep compares like with like. Each row carries a parser
version: shards written by an older shard format are cache misses, never
resumed into a merge built by a newer one.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from sqlalchemy import text

logger = logging.getLogger(__name__)

_TABLE = "parse_shards"

# Bump when the cached shard format changes (markdown layout, extras_json
# schema, page-numbering convention). Rows without a matching version —
# including legacy rows written before the column existed — are misses.
PARSER_VERSION = "1"


def content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _engine():
    from ..db.database import default_engine

    return default_engine()


def get_shard(
    content_sha: str,
    shard_index: int,
    *,
    parser: str | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
) -> dict | None:
    """A cached completed shard, or None on a miss or any DB error.

    A row whose parser version differs from ``PARSER_VERSION`` is a
    miss: it is never merged into a parse built by another format.

    P1-B7: when the caller states what it expects — a parser name and/or
    the page range — a row that disagrees is also a miss. A shard cached
    under a different page window must never be merged into this window's
    slot: page markers downstream would point at the wrong pages.
    """
    try:
        engine = _engine()
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT page_start, page_end, parser, markdown, extras_json, parser_version "
                    f"FROM {_TABLE} WHERE content_sha256 = :sha AND shard_index = :idx"
                ),
                {"sha": content_sha, "idx": shard_index},
            ).first()
    except Exception as exc:  # noqa: BLE001 - fail-open cache
        logger.warning("parse_shards: cache read failed (fail-open): %s", exc)
        return None
    if row is None:
        return None
    if row[5] != PARSER_VERSION:
        return None
    if parser is not None and row[2] != parser:
        return None
    if page_start is not None and row[0] != page_start:
        return None
    if page_end is not None and row[1] != page_end:
        return None
    return {
        "page_start": row[0],
        "page_end": row[1],
        "parser": row[2],
        "markdown": row[3],
        "extras_json": row[4],
        "parser_version": row[5],
    }


def put_shard(
    content_sha: str,
    shard_index: int,
    page_start: int,
    page_end: int,
    parser: str,
    markdown: str,
    extras_json: str = "",
) -> bool:
    """Cache a completed shard; False on any DB error (fail-open).

    The upsert is a DELETE-then-INSERT inside one transaction: it works
    identically on SQLite and PostgreSQL, where ``INSERT OR REPLACE``
    does not exist.

    P1-B7: empty markdown is refused (False) — caching an empty shard
    would let a later resume merge a hole as if the page had parsed.
    """
    # P1-B7: refuse before touching the DB, so a failed shard cannot evict
    # a good row for the same index.
    if not (markdown or "").strip():
        logger.warning(
            "parse_shards: refusing to cache empty markdown (sha %s, shard %d)",
            content_sha[:12], shard_index,
        )
        return False
    try:
        from ..clock import utcnow

        engine = _engine()
        with engine.begin() as conn:
            conn.execute(
                text(f"DELETE FROM {_TABLE} WHERE content_sha256 = :sha AND shard_index = :idx"),
                {"sha": content_sha, "idx": shard_index},
            )
            conn.execute(
                text(
                    f"INSERT INTO {_TABLE} "
                    "(content_sha256, shard_index, page_start, page_end, parser, markdown, "
                    "extras_json, parser_version, updated_at) "
                    "VALUES (:sha, :idx, :ps, :pe, :parser, :md, :extras, :ver, :now)"
                ),
                {"sha": content_sha, "idx": shard_index, "ps": page_start, "pe": page_end,
                 "parser": parser, "md": markdown, "extras": extras_json,
                 "ver": PARSER_VERSION, "now": utcnow()},
            )
    except Exception as exc:  # noqa: BLE001 - fail-open cache
        logger.warning("parse_shards: cache write failed (fail-open): %s", exc)
        return False
    return True


def delete_for_content(content_sha: str) -> None:
    """Drop all cached shards for a document (after a successful merge)."""
    try:
        engine = _engine()
        with engine.begin() as conn:
            conn.execute(
                text(f"DELETE FROM {_TABLE} WHERE content_sha256 = :sha"),
                {"sha": content_sha},
            )
    except Exception as exc:  # noqa: BLE001 - fail-open cache
        logger.warning("parse_shards: cache delete failed (fail-open): %s", exc)


def sweep_stale(stale_days: int) -> int:
    """Delete crash-residue rows older than ``stale_days``; returns the count."""
    try:
        from ..clock import utcnow

        cutoff = utcnow() - timedelta(days=stale_days)
        engine = _engine()
        with engine.begin() as conn:
            result = conn.execute(
                text(f"DELETE FROM {_TABLE} WHERE updated_at < :cutoff"),
                {"cutoff": cutoff},
            )
            return result.rowcount or 0
    except Exception as exc:  # noqa: BLE001 - fail-open cache
        logger.warning("parse_shards: stale sweep failed (fail-open): %s", exc)
        return 0
