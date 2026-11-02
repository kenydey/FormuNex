"""Pure derivation helpers with no application dependencies (stage 4 / SR-019).

This module imports only the standard library, so it is importable from
every layer (api, services, db, worker) without ever taking part in an
import cycle. The functions sunk here used to live in services/kb_dedup.py
and services/wiki/schema.py; the db layer imported them from services,
which is the backward edge SR-019 bans. The old modules re-export them
for migration and existing-caller compatibility; new code imports from
here. The layering test (tests/test_stage4_layering.py) pins this module
to stdlib-only imports.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

_WS_RE = re.compile(r"\s+")


def normalize_text(text: str | None) -> str:
    """Canonical form for the L1 exact-dup key."""
    return _WS_RE.sub(" ", (text or "").strip().lower())


def chunk_dedup_key(text: str | None, heading_path: str | None, page_no: Any) -> str:
    """L1 dedup key: normalized text + provenance.

    Same-document chunks may share identical text across sections/pages
    (different ``heading_path`` / ``page_no``) — those are legitimate and
    must be kept. Only text+provenance-identical rows under a *different*
    source count as duplicates.
    """
    canon = "\x1f".join(
        [normalize_text(text), normalize_text(heading_path), str(page_no or "")]
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def wiki_root_from_db_url(db_url: str) -> Path:
    """Derive the Wiki directory from the database URL.

    ``sqlite:///./data/formunex.db`` → ``./data/wiki``

    For a server database (``postgresql://user:pw@host:5432/db``) the URL is not
    a filesystem path, so there is nothing to derive: fall back to the default
    ``data/wiki``. The previous version only stripped the ``sqlite:///`` prefix
    and fed the rest to ``Path()``, which silently created a directory literally
    named ``postgresql:`` and buried every Wiki page under it.
    """
    raw = (db_url or "").strip()
    if not raw.startswith("sqlite"):
        return Path("data") / "wiki"
    raw = raw.replace("sqlite:///", "").strip()
    if not raw or raw == ":memory:":
        return Path("data") / "wiki"
    parent = Path(raw).expanduser().resolve().parent
    return parent / "wiki"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
