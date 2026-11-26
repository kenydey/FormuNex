"""SQLite + disk store for LLM Wiki pages (W1)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from .models import WikiPage
from .session_utils import commit_session
from ..config import get_settings
from ..domain.pure import content_hash, wiki_root_from_db_url
from ..services.wiki.embed import embed_wiki_page
from ..services.wiki.fts import index_page
from ..services.wiki.schema import normalize_okf_front_matter, utcnow_iso

logger = logging.getLogger(__name__)


class WikiProjectCollisionError(ValueError):
    """Raised when upsert targets a path owned by a different project.

    The write is refused (fail-closed); callers that bulk-compile wiki pages
    should catch it and skip the page (fail-open) instead of overwriting
    another project's content.
    """


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WikiStore:
    def __init__(self, session_factory: sessionmaker[Session], *, root: Path | None = None) -> None:
        self._session_factory = session_factory
        self._root = root

    def root(self) -> Path:
        if self._root is not None:
            return self._root
        return wiki_root_from_db_url(get_settings().db_url)

    @property
    def session_factory(self) -> sessionmaker[Session]:
        """Session factory the wiki pages (and their FTS index) live in."""
        return self._session_factory

    def _abs(self, rel_path: str) -> Path:
        root = self.root().resolve()
        path = (root / rel_path).resolve()
        if not str(path).startswith(str(root)):
            raise ValueError(f"wiki path escapes root: {rel_path}")
        return path

    def upsert_page(
        self,
        *,
        path: str,
        kind: str,
        title: str,
        norm_key: str,
        entity_id: str | None,
        markdown: str,
        source_ids: list[str] | None = None,
        flags: list[str] | None = None,
        replace_source_ids: bool = False,
        project_id: str | None = None,
    ) -> WikiPage:
        rel = path.replace("\\", "/").lstrip("/")
        body = markdown
        digest = content_hash(body)
        abs_path = self._abs(rel)
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        scoped_project = (project_id or "").strip() or None
        # 批 5-2 OKF 对齐（默认关）：归一化 front matter（version 取最终
        # DB revision）并维护 index.md / log.md；关时行为与此前一致。
        okf = bool(getattr(get_settings(), "wiki_okf_enabled", False))
        log_entry: tuple[str, int] | None = None

        with commit_session(self._session_factory) as session:
            row = (
                session.query(WikiPage)
                .filter(WikiPage.path == rel)
                .one_or_none()
            )
            if row is None:
                if okf:
                    body = normalize_okf_front_matter(body, kind=kind, version=1, title=title)
                    digest = content_hash(body)
                row = WikiPage(
                    id=str(uuid.uuid4()),
                    path=rel,
                    kind=kind,
                    title=title[:512],
                    norm_key=(norm_key or "")[:200],
                    entity_id=(entity_id or None),
                    content_hash=digest,
                    source_ids=list(source_ids or []),
                    flags=list(flags or []),
                    revision=1,
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                    project_id=scoped_project,
                )
                session.add(row)
                abs_path.write_text(body, encoding="utf-8")
                session.flush()
                session.expunge(row)
                out = row
                if okf:
                    log_entry = ("created", 1)
            else:
                incoming = list(source_ids or [])
                if replace_source_ids:
                    merged_sources = incoming
                else:
                    merged_sources = list(dict.fromkeys([*(row.source_ids or []), *incoming]))

                # P0-2: adopt project scope when the row has none; refuse
                # cross-project path collision (fail-closed — see 0033).
                if scoped_project is not None and not row.project_id:
                    row.project_id = scoped_project
                elif (
                    scoped_project is not None
                    and row.project_id
                    and row.project_id != scoped_project
                ):
                    raise WikiProjectCollisionError(
                        f"wiki path {rel!r} belongs to project {row.project_id!r}, "
                        f"not {scoped_project!r}; refusing to overwrite"
                    )

                if okf:
                    # 先按当前 revision 归一化再比 hash：已含 OKF 字段且
                    # 内容未变时走 unchanged 分支（revision 不 bump）。
                    provisional = normalize_okf_front_matter(
                        body,
                        kind=kind,
                        version=int(row.revision or 1),
                        title=title,
                    )
                    if content_hash(provisional) == row.content_hash:
                        body = provisional
                        changed = False
                        if merged_sources != list(row.source_ids or []):
                            row.source_ids = merged_sources
                            changed = True
                        if flags is not None and list(flags) != list(row.flags or []):
                            row.flags = list(flags)
                            changed = True
                        if changed:
                            row.updated_at = _utcnow()
                        # 批 5：先 flush 再 expunge——否则仅改 source_ids /
                        # flags（内容不变）时，expunge 会把未落库的改动
                        # 随对象一起丢掉，返回的行与库里不一致。
                        session.flush()
                        session.expunge(row)
                        out = row
                    else:
                        new_revision = int(row.revision or 1) + 1
                        body = normalize_okf_front_matter(
                            body,
                            kind=kind,
                            version=new_revision,
                            title=title,
                        )
                        digest = content_hash(body)
                        row.title = title[:512]
                        row.kind = kind
                        row.norm_key = (norm_key or "")[:200]
                        row.entity_id = entity_id or row.entity_id
                        row.content_hash = digest
                        row.source_ids = merged_sources
                        if flags is not None:
                            row.flags = list(flags)
                        row.revision = new_revision
                        row.updated_at = _utcnow()
                        abs_path.write_text(body, encoding="utf-8")
                        session.flush()
                        session.expunge(row)
                        out = row
                        log_entry = ("updated", new_revision)
                elif row.content_hash == digest:
                    changed = False
                    if merged_sources != list(row.source_ids or []):
                        row.source_ids = merged_sources
                        changed = True
                    if flags is not None and list(flags) != list(row.flags or []):
                        row.flags = list(flags)
                        changed = True
                    if changed:
                        row.updated_at = _utcnow()
                    # 批 5：先 flush 再 expunge——否则仅改 source_ids /
                    # flags（内容不变）时，expunge 会把未落库的改动随对象
                    # 一起丢掉，返回的行与库里不一致（同秒重写必现）。
                    session.flush()
                    session.expunge(row)
                    out = row
                else:
                    row.title = title[:512]
                    row.kind = kind
                    row.norm_key = (norm_key or "")[:200]
                    row.entity_id = entity_id or row.entity_id
                    row.content_hash = digest
                    row.source_ids = merged_sources
                    if flags is not None:
                        row.flags = list(flags)
                    row.revision = int(row.revision or 1) + 1
                    row.updated_at = _utcnow()
                    abs_path.write_text(body, encoding="utf-8")
                    session.flush()
                    session.expunge(row)
                    out = row

        # Best-effort FTS sync (never breaks compile)
        try:
            index_page(
                self._session_factory,
                path=rel,
                title=out.title or title,
                kind=out.kind or kind,
                norm_key=out.norm_key or norm_key or "",
                flags=list(out.flags or []),
                markdown=body,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("wiki FTS sync skipped: %s", exc)
        # Best-effort Phase-3 embed into document_chunks (flag-gated)
        try:
            embed_wiki_page(rel)
        except Exception as exc:  # noqa: BLE001
            logger.debug("wiki embed sync skipped: %s", exc)
        # Best-effort OKF index / log maintenance (never breaks compile).
        if okf and log_entry is not None:
            try:
                self._append_okf_log(rel, kind, log_entry[0], log_entry[1])
            except Exception as exc:  # noqa: BLE001
                logger.debug("wiki OKF log skipped: %s", exc)
            try:
                self._refresh_okf_indexes()
            except Exception as exc:  # noqa: BLE001
                logger.debug("wiki OKF index refresh skipped: %s", exc)
        return out

    def _append_okf_log(self, rel: str, kind: str, action: str, revision: int) -> None:
        """Append one line to the wiki root ``log.md`` (批 5-2)."""
        root = self.root()
        log_path = root / "log.md"
        if not log_path.exists():
            log_path.write_text("# Wiki Change Log\n\n", encoding="utf-8")
        line = (
            f"- [okf] {utcnow_iso()} {rel} ({kind}, revision {revision}, {action})\n"
        )
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(line)

    def append_invalidation_log(self, source_id: str, reason: str, detail: str = "") -> bool:
        """Append one source-invalidation line to the wiki root ``log.md`` (批 6).

        The log is the change history for the unified invalidation protocol:
        one line per ``invalidate_source`` run, independent of the OKF
        switch (the file is created here when absent). Never raises;
        returns False when the write failed.
        """
        try:
            root = self.root()
            log_path = root / "log.md"
            if not log_path.exists():
                log_path.write_text("# Wiki Change Log\n\n", encoding="utf-8")
            suffix = f" ({detail})" if (detail or "").strip() else ""
            line = f"- [invalidation] {utcnow_iso()} source {source_id} {reason}{suffix}\n"
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            return True
        except Exception as exc:  # noqa: BLE001 — log must never break invalidation
            logger.debug("wiki invalidation log skipped: %s", exc)
            return False

    def _refresh_okf_indexes(self) -> None:
        """Regenerate root ``index.md`` + per-directory ``index.md`` (批 5-2)."""
        rows = self.list_pages(limit=500)
        by_dir: dict[str, list] = {}
        for r in rows:
            d = (r.path or "").rsplit("/", 1)[0] if "/" in (r.path or "") else ""
            by_dir.setdefault(d, []).append(r)
        for rs in by_dir.values():
            rs.sort(key=lambda r: r.path or "")
        root = self.root()

        def _entry(r, *, link: str) -> str:
            title = (r.title or "").strip() or (r.path or "")
            return (
                f"- [{title}]({link}) — {r.kind or 'page'}, "
                f"revision {int(r.revision or 1)}\n"
            )

        root_lines = ["# Wiki Index\n", "\n"]
        for d in sorted(by_dir):
            label = f"{d}/" if d else "(root)"
            root_lines.append(f"## {label}\n\n")
            for r in by_dir[d]:
                root_lines.append(_entry(r, link=r.path or ""))
            root_lines.append("\n")
        (root / "index.md").write_text("".join(root_lines), encoding="utf-8")
        for d, rs in by_dir.items():
            if not d:
                continue
            sub = root / d
            sub.mkdir(parents=True, exist_ok=True)
            sub_lines = [f"# Index: {d}/\n", "\n"]
            for r in rs:
                sub_lines.append(
                    _entry(r, link=(r.path or "").rsplit("/", 1)[-1])
                )
            (sub / "index.md").write_text("".join(sub_lines), encoding="utf-8")

    def get(self, page_id: str) -> WikiPage | None:
        with self._session_factory() as session:
            return session.get(WikiPage, page_id)

    def get_by_path(self, path: str) -> WikiPage | None:
        rel = path.replace("\\", "/").lstrip("/")
        with self._session_factory() as session:
            return (
                session.query(WikiPage)
                .filter(WikiPage.path == rel)
                .one_or_none()
            )

    def list_pages(
        self,
        *,
        kind: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[WikiPage]:
        with self._session_factory() as session:
            q = session.query(WikiPage).order_by(WikiPage.updated_at.desc())
            if kind:
                q = q.filter(WikiPage.kind == kind)
            if project_id:
                # B2（2026-11-26 全面审查）：项目内检索须同时看到本项目页
                # 与全局页（project_id IS NULL），与 chunk_store /
                # entity_store 的“项目 OR 全局”口径一致；此前严格相等
                # 导致项目内 Wiki 关键词臂恒为空。跨项目页仍不可见；
                # upsert 的跨项目路径冲突 fail-closed 不受此改动影响。
                q = q.filter(
                    (WikiPage.project_id == project_id)
                    | (WikiPage.project_id.is_(None))
                )
            return q.offset(max(0, offset)).limit(min(500, max(1, limit))).all()

    def read_markdown(self, path: str) -> str | None:
        try:
            p = self._abs(path)
        except ValueError:
            return None
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8")


_store: WikiStore | None = None


def get_wiki_store() -> WikiStore:
    global _store
    if _store is None:
        from .database import default_session_factory

        _store = WikiStore(default_session_factory())
    return _store
