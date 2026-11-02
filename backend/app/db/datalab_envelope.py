"""Datalab item-envelope helpers, kept for the migration path only.

Datalab removal batch 3 deletes ``datalab_client.py`` (HTTP) but the
one-time migration (``app.services.lab.datalab_migration``) still parses
Datalab item envelopes via ``campaign_store._parse_row_from_item`` and
``store._record_from_item_data``. Those two functions need exactly the
envelope builders/validators below — no HTTP, no settings, no client.
Everything else from datalab_client is gone.

This module dies with the migration in batch 5, once the import has run
(or confirmed unnecessary). Do not build new features on it.
"""

from __future__ import annotations

from typing import Any


class DatalabStoreError(ValueError):
    """Raised when a Datalab item envelope fails contract validation."""


# Datalab Headless API uses ``blocktype`` (no underscore) and string ``type`` on samples.
# Comment blocks store arbitrary JSON in ``data`` (see datalab CommentBlock).
DATALAB_BLOCK_KIND = "comment"


def datalab_block(block_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Build a blocks_obj entry accepted by Datalab ``/new-sample/``."""
    return {
        "block_id": block_id,
        "blocktype": DATALAB_BLOCK_KIND,
        "data": data,
    }


def validate_blocks(item_data: dict[str, Any], required_blocks: tuple[str, ...]) -> None:
    blocks = item_data.get("blocks_obj")
    if not isinstance(blocks, dict):
        raise DatalabStoreError("Datalab item_data.blocks_obj missing or invalid")
    for key in required_blocks:
        block = blocks.get(key)
        if not isinstance(block, dict) or "data" not in block:
            raise DatalabStoreError(f"Datalab block {key!r} missing or invalid")
