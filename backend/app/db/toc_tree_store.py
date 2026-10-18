"""Persistence for PageIndex trees (``document_toc_trees``, alembic 0054)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ..clock import utcnow
from .models_plm import DocumentTocTreeRow


class TocTreeStore:
    """``session_factory=None`` follows the process default database, re-resolved on
    every call so a changed ``FORMUNEX_DB_URL`` (tests, Settings) takes effect."""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._factory = session_factory

    def _session_factory(self) -> Session:
        if self._factory is not None:
            return self._factory()
        from .database import default_session_factory

        return default_session_factory()()

    def get(self, source_id: str) -> DocumentTocTreeRow | None:
        with self._session_factory() as session:
            return session.get(DocumentTocTreeRow, source_id)

    def get_many(self, source_ids: list[str]) -> dict[str, DocumentTocTreeRow]:
        if not source_ids:
            return {}
        with self._session_factory() as session:
            rows = session.execute(
                select(DocumentTocTreeRow).where(DocumentTocTreeRow.source_id.in_(source_ids))
            ).scalars().all()
            return {r.source_id: r for r in rows}

    def put(self, source_id: str, built: dict[str, Any], *, builder_version: str) -> DocumentTocTreeRow:
        stats = built.get("stats") or {}
        with self._session_factory() as session:
            row = session.get(DocumentTocTreeRow, source_id) or DocumentTocTreeRow(source_id=source_id)
            row.tree = built["tree"]
            row.builder_version = builder_version
            row.doc_kind = built.get("doc_kind") or "generic"
            row.node_count = int(stats.get("node_count") or 0)
            row.max_depth = int(stats.get("max_depth") or 0)
            row.page_count = int(stats.get("page_count") or 0)
            row.summaries_status = "none"
            row.built_at = utcnow()
            session.add(row)
            session.commit()
            return row

    def update_tree(self, source_id: str, tree: dict[str, Any]) -> DocumentTocTreeRow | None:
        """Replace the stored tree without touching summaries_status (summary jobs)."""
        with self._session_factory() as session:
            row = session.get(DocumentTocTreeRow, source_id)
            if row is None:
                return None
            row.tree = tree
            session.add(row)
            session.commit()
            return row

    def set_summaries_status(self, source_id: str, status: str) -> DocumentTocTreeRow | None:
        """Summaries state machine: none -> pending -> done | partial."""
        with self._session_factory() as session:
            row = session.get(DocumentTocTreeRow, source_id)
            if row is None:
                return None
            row.summaries_status = status
            session.add(row)
            session.commit()
            return row

    def delete(self, source_id: str) -> int:
        with self._session_factory() as session:
            n = session.execute(delete(DocumentTocTreeRow).where(DocumentTocTreeRow.source_id == source_id)).rowcount
            session.commit()
            return int(n or 0)


_store: TocTreeStore | None = None


def get_toc_tree_store() -> TocTreeStore:
    global _store
    if _store is None:
        _store = TocTreeStore()
    return _store
