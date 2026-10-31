"""File storage for rendered documents and uploaded templates.

A local directory (``plm_storage_dir``, a mounted volume in Docker). Keys are
relative paths; the interface is small so an object store (MinIO / S3) can
replace it without touching callers.
"""
from __future__ import annotations

from pathlib import Path

from ...config import get_settings


def _root() -> Path:
    return Path(get_settings().plm_storage_dir).resolve()


def _path(key: str) -> Path:
    root = _root()
    path = (root / key).resolve()
    if root not in path.parents:
        raise ValueError(f"storage key escapes the storage root: {key!r}")
    return path


def put(key: str, data: bytes) -> str:
    path = _path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key


def get(key: str) -> bytes:
    return _path(key).read_bytes()


def delete(key: str) -> bool:
    """Best-effort delete of one key; False when nothing was there.

    W1-4: document generation cleans up files it wrote when the
    database write that was supposed to reference them fails, so a
    failed generation leaves no orphaned renders behind. A delete
    that itself fails is the caller's problem to log for reclamation
    (a later sweep can list files with no referencing row).
    """
    path = _path(key)
    if not path.is_file():
        return False
    path.unlink()
    return True


def exists(key: str) -> bool:
    return _path(key).is_file()
