"""Transient shard-result cache for sharded PDF parsing (batch 1).

A sharded parse caches each completed shard's markdown keyed by content
SHA-256 + shard index. A crashed or retried parse of the same bytes then
resumes from finished shards instead of re-OCRing them. Rows are deleted
after a successful merge; crash residue is swept opportunistically.

Fail-open by contract (same as ``persist_structured``): any DB error —
missing table on a database that has not run migration 0075 yet, a locked
database, an unreachable engine — behaves as a cache miss and only logs a
warning. A cache bug must never fail a parse.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_TABLE = "parse_shards"


def content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _engine():
    from ..db.database import default_engine

    return default_engine()


def get_shard(content_sha: str, shard_index: int) -> dict | None:
    """A cached completed shard, or None on a miss or any DB error."""
    try:
        engine = _engine()
        with engine.connect() as conn:
            row = conn.exec_driver_sql(
                f"SELECT page_start, page_end, parser, markdown, extras_json FROM {_TABLE} "
                "WHERE content_sha256 = ? AND shard_index = ?",
                (content_sha, shard_index),
            ).first()
    except Exception as exc:  # noqa: BLE001 - fail-open cache
        logger.warning("parse_shards: cache read failed (fail-open): %s", exc)
        return None
    if row is None:
        return None
    return {
        "page_start": row[0],
        "page_end": row[1],
        "parser": row[2],
        "markdown": row[3],
        "extras_json": row[4],
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
    """Cache a completed shard; False on any DB error (fail-open)."""
    try:
        engine = _engine()
        now = datetime.now(timezone.utc).isoformat()
        with engine.begin() as conn:
            conn.exec_driver_sql(
                f"INSERT OR REPLACE INTO {_TABLE} "
                "(content_sha256, shard_index, page_start, page_end, parser, markdown, extras_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (content_sha, shard_index, page_start, page_end, parser, markdown, extras_json, now),
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
            conn.exec_driver_sql(
                f"DELETE FROM {_TABLE} WHERE content_sha256 = ?",
                (content_sha,),
            )
    except Exception as exc:  # noqa: BLE001 - fail-open cache
        logger.warning("parse_shards: cache delete failed (fail-open): %s", exc)


def sweep_stale(stale_days: int) -> int:
    """Delete crash-residue rows older than ``stale_days``; returns the count."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat()
        engine = _engine()
        with engine.begin() as conn:
            result = conn.exec_driver_sql(
                f"DELETE FROM {_TABLE} WHERE updated_at < ?",
                (cutoff,),
            )
            return result.rowcount or 0
    except Exception as exc:  # noqa: BLE001 - fail-open cache
        logger.warning("parse_shards: stale sweep failed (fail-open): %s", exc)
        return 0
