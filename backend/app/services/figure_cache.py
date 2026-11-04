"""Three-library C1: per-figure / per-page JSON file cache + resume.

MERMaid's resume idea, file-level only: FormuNex already has the outbox
task model, so no second task system is built here. A cache hit means
the caller skips recomputation (resume); a miss or corrupt entry means
compute normally. All failures fail open (None / False), never raise.

Default root is ``backend/data/figure_cache`` (gitignored); tests pass
their own root. Keys are caller-built strings (content_sha + page +
bbox for figures, content_sha + page for detections); the file name is
their SHA-256, so no caller string ever becomes a path.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ._fsutil import atomic_write_text

FIGURE_KIND = "figure"
DETECTION_KIND = "detection"


def figure_key(content_sha: str, page_no: int, bbox: tuple) -> str:
    """Stable key for one figure result."""
    return f"figure:{content_sha}:{page_no}:{tuple(bbox)}"


def detection_key(content_sha: str, page_no: int) -> str:
    """Stable key for one page's detection result."""
    return f"detection:{content_sha}:{page_no}"


class FigureCache:
    """JSON file cache; writes go through _fsutil.atomic_write_text
    (the app's one atomic-write path — a bare os.replace here would
    trip test_every_atomic_write_in_the_app_goes_through_the_helper)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, kind: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / kind / f"{digest}.json"

    def get(self, kind: str, key: str) -> dict | None:
        """Cached JSON dict, or None on miss / corruption / any error."""
        try:
            path = self._path(kind, key)
            if not path.is_file():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def put(self, kind: str, key: str, value: dict) -> bool:
        """Store a JSON dict atomically. False on any error."""
        try:
            path = self._path(kind, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, json.dumps(value, ensure_ascii=False))
            return True
        except Exception:
            return False
