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

# P1-B14 (hardening; audit 2026-11-05: no caller in app/ passes user
# input as ``kind`` — FigureCache has no app/ caller at all — but the
# old code let any caller string become a path component, contradicting
# this module's "no caller string ever becomes a path" docstring).
# Only the two kinds the key builders produce are valid paths.
_KNOWN_KINDS = frozenset({FIGURE_KIND, DETECTION_KIND})


def figure_key(content_sha: str, page_no: int, bbox: tuple) -> str:
    """Stable key for one figure result."""
    return f"figure:{content_sha}:{page_no}:{tuple(bbox)}"


def detection_key(content_sha: str, page_no: int) -> str:
    """Stable key for one page's detection result."""
    return f"detection:{content_sha}:{page_no}"


# P3-B19: per-kind entry cap. The old cache grew without bound on
# long-running processes; put() now prunes the oldest files (by mtime)
# back to this cap, best-effort — a prune failure never fails the put.
FIGURE_CACHE_MAX_ENTRIES = 1000


class FigureCache:
    """JSON file cache; writes go through _fsutil.atomic_write_text
    (the app's one atomic-write path — a bare os.replace here would
    trip test_every_atomic_write_in_the_app_goes_through_the_helper)."""

    def __init__(self, root: str | Path, *, max_entries: int = FIGURE_CACHE_MAX_ENTRIES) -> None:
        self.max_entries = max_entries
        self.root = Path(root)
        try:
            # P2-B37: the constructor shares the module's fail-open
            # guarantee — a root that is an existing file (or otherwise
            # uncreatable) marks the cache unusable instead of raising;
            # get/put then return None/False.
            self.root.mkdir(parents=True, exist_ok=True)
        except Exception:
            self._usable = False
        else:
            self._usable = True

    def _path(self, kind: str, key: str) -> Path:
        if not self._usable:
            raise RuntimeError("figure cache is unusable")
        if kind not in _KNOWN_KINDS:
            raise ValueError(f"unknown figure cache kind: {kind!r}")
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
        """Store a JSON dict atomically. False on any error.

        P2-B36: a non-dict value is refused (get only ever returns
        dicts, so storing anything else was a silent fake success).
        """
        if not isinstance(value, dict):
            return False
        try:
            path = self._path(kind, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, json.dumps(value, ensure_ascii=False))
            self._prune(kind)
            return True
        except Exception:
            return False

    def _prune(self, kind: str) -> None:
        """Drop oldest entries beyond max_entries. Best-effort only."""
        try:
            d = self.root / kind
            if not d.is_dir():
                return
            files = sorted(
                (p for p in d.iterdir() if p.is_file()),
                key=lambda p: p.stat().st_mtime,
            )
            for stale in files[: max(0, len(files) - self.max_entries)]:
                try:
                    stale.unlink()
                except Exception:
                    pass
        except Exception:
            pass
