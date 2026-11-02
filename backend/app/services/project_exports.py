"""Project-scoped export file shelf (Dim-4) — filesystem only, no sandbox."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config import get_settings

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB
MAX_EXPORT_BYTES = _MAX_BYTES


class ProjectExportError(ValueError):
    """Base for project-export failures (stage 4 / SR-054).

    Services raise these, never HTTPException: the route layer maps each
    subclass to its HTTP status (see api/projects.py).
    """


class ProjectExportNotFoundError(ProjectExportError):
    """Project or export file not found (maps to HTTP 404)."""


class ProjectExportValidationError(ProjectExportError):
    """Bad project id, filename, symlink or path escape (maps to HTTP 400)."""


class ProjectExportTooLargeError(ProjectExportError):
    """File exceeds MAX_EXPORT_BYTES (maps to HTTP 413)."""


@dataclass(frozen=True)
class ExportFileInfo:
    name: str
    size: int
    updated_at: str
    content_type: str


def _db_parent() -> Path:
    url = get_settings().db_url
    if url.startswith("sqlite:///"):
        return Path(url.replace("sqlite:///", "", 1)).expanduser().resolve().parent
    # Non-sqlite (rare in FormuNex): fall back next to CWD data/
    return Path("data").resolve()


def exports_dir(project_id: str) -> Path:
    if not project_id or "/" in project_id or "\\" in project_id or ".." in project_id:
        raise ProjectExportValidationError("invalid project_id")
    return _db_parent() / "project_exports" / project_id


def sanitize_filename(name: str) -> str:
    raw = (name or "").strip()
    if not raw or "/" in raw or "\\" in raw or ".." in raw:
        raise ProjectExportValidationError(
            "filename must be 1–128 chars: letters, digits, ._- (no path)")
    if not _SAFE_NAME.match(raw):
        raise ProjectExportValidationError(
            "filename must be 1–128 chars: letters, digits, ._- (no path)")
    return raw


def guess_content_type(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith(".csv"):
        return "text/csv; charset=utf-8"
    if lower.endswith(".md"):
        return "text/markdown; charset=utf-8"
    if lower.endswith(".txt"):
        return "text/plain; charset=utf-8"
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith((".xlsx", ".xls")):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"


def ensure_project_exists(project_id: str) -> None:
    from ..db.project_store import get_project_store

    if get_project_store().get(project_id) is None:
        raise ProjectExportNotFoundError("Project not found")


def list_exports(project_id: str) -> list[ExportFileInfo]:
    ensure_project_exists(project_id)
    root = exports_dir(project_id)
    if not root.is_dir():
        return []
    out: list[ExportFileInfo] = []
    for p in sorted(root.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not p.is_file():
            continue
        try:
            name = sanitize_filename(p.name)
        except ProjectExportValidationError:
            continue
        st = p.stat()
        out.append(
            ExportFileInfo(
                name=name,
                size=st.st_size,
                updated_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                content_type=guess_content_type(name),
            )
        )
    return out


def save_export_bytes(project_id: str, filename: str, data: bytes) -> ExportFileInfo:
    ensure_project_exists(project_id)
    name = sanitize_filename(filename)
    if len(data) > _MAX_BYTES:
        raise ProjectExportTooLargeError(f"file exceeds {_MAX_BYTES} bytes")
    root = exports_dir(project_id)
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    # Refuse symlink escape
    if path.is_symlink():
        raise ProjectExportValidationError("refusing symlink target")
    path.write_bytes(data)
    st = path.stat()
    return ExportFileInfo(
        name=name,
        size=st.st_size,
        updated_at=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        content_type=guess_content_type(name),
    )


def resolve_export_path(project_id: str, filename: str) -> Path:
    ensure_project_exists(project_id)
    name = sanitize_filename(filename)
    root = exports_dir(project_id).resolve()
    path = (root / name).resolve()
    if root not in path.parents and path != root:
        raise ProjectExportValidationError("path escape blocked")
    if not path.is_file():
        raise ProjectExportNotFoundError("export not found")
    return path


def delete_export(project_id: str, filename: str) -> None:
    path = resolve_export_path(project_id, filename)
    path.unlink(missing_ok=True)


def purge_project_exports(project_id: str) -> int:
    """Best-effort cleanup when a project is deleted. Returns removed file count."""
    root = exports_dir(project_id)
    if not root.is_dir():
        return 0
    n = 0
    for p in root.iterdir():
        if p.is_file():
            p.unlink(missing_ok=True)
            n += 1
    try:
        root.rmdir()
    except OSError:
        pass
    return n
