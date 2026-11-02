"""Phase C-1: process-local faiss ANN index over KB chunk embeddings.

Design notes (all verified against the current retrieval path):

- The KB hybrid path (``hybrid_search_scored``) scans at most
  ``kb_search_scan_limit`` (default 5000) chunks and scores the embedding
  half with brute-force cosine. This module removes that ceiling for the
  *vector* half: one ``faiss.IndexFlatIP`` bucket per ``embedding_model``
  (semantic spaces are never mixed — the same invariant as
  ``kb_index.comparable_embedding``), built over the full non-archived
  corpus.
- Stored vectors are L2-normalised (ingest writes normalised vectors;
  rebuild normalises defensively), so inner product == cosine similarity and
  ``IndexFlatIP`` is *exact*: top-k matches brute-force cosine, which the
  A/B correctness check asserts (``recall@k == 1.0`` on synthetic vectors).
- The index persists under ``<backend>/data/kb_ann/`` (gitignored) plus a
  ``manifest.json``. The manifest records a DB fingerprint (per-model
  count/max_created_at over the indexed population); any chunk
  insert/delete/replace/archive changes the fingerprint, so the next
  ``ensure_index`` updates instead of serving stale vectors. Stage 3
  (SR-028): that update is a delta (id-set diff + append/tombstone),
  not a full rescan — a manifest mismatch (version, dim, row count)
  still forces a rebuild, never a silent wrong-space or stale search.
- Everything here is fail-open: any faiss problem (missing package, corrupt
  files, dim mismatch) makes the entry points return ``None``/empty and the
  caller falls back to the pre-C-1 brute-force path. faiss is imported
  lazily, mirroring ``rag.py``.

Vector storage (C-1b): embeddings live in ``document_chunks.embedding``
(JSON) historically and ``document_chunks.embedding_blob`` (float32
little-endian BLOB) going forward. :func:`chunk_vector` reads BLOB first
and falls back to JSON, so a partially backfilled corpus still works.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

from ._fsutil import replace_with_retry

logger = logging.getLogger(__name__)

_INDEX_DIR_NAME = "kb_ann"
_MANIFEST_NAME = "manifest.json"
# v2: manifest carries a DB fingerprint (per-model count/max_created_at over
# the indexed population). v1 manifests are treated as stale and rebuilt.
# v3 (stage 3 / SR-028): sidecars carry tombstone positions, enabling
# incremental delete without a rebuild. v2 manifests are rebuilt, not loaded.
_MANIFEST_VERSION = 3

# A bucket whose tombstoned positions exceed this share of its id list is
# rebuilt from its live vectors only (compaction). Below it, deletes stay
# pure tombstones: no vector data moves at all.
_TOMBSTONE_COMPACT_RATIO = 0.25

_lock = threading.Lock()
_buckets: dict[str, dict[str, Any]] = {}  # model -> {"index", "ids", "dim"}
_manifest: dict[str, Any] | None = None
_last_error: str | None = None
# Stage 5 / SR-093: at most one background rebuild runs at a time
# (single-flight); _generation is bumped by invalidate() so a build
# started before an invalidation can never publish over the fresh state.
_build_thread: threading.Thread | None = None
_generation = 0


def _index_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    backend_dir = os.path.dirname(here)  # app/services -> app -> backend
    backend_dir = os.path.dirname(backend_dir)
    d = os.path.join(backend_dir, "data", _INDEX_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _faiss():  # lazy import, mirrors rag.py
    try:
        import faiss  # type: ignore

        return faiss
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb_ann faiss unavailable: %s", exc)
        return None


def _numpy():
    try:
        import numpy as np  # type: ignore

        return np
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb_ann numpy unavailable: %s", exc)
        return None


def vector_to_blob(vec: list[float]) -> bytes:
    """Encode a float vector as float32 little-endian bytes (C-1b write path)."""
    np = _numpy()
    if np is None:
        raise RuntimeError("numpy unavailable for vector_to_blob")
    return np.asarray(vec, dtype="<f4").tobytes()


def chunk_vector(chunk: Any):
    """Read a chunk's embedding as a normalised float32 array.

    BLOB-first (``embedding_blob``), JSON-fallback (``embedding``).
    Returns ``None`` when the chunk has no usable vector. Never raises.
    """
    np = _numpy()
    if np is None:
        return None
    try:
        if isinstance(chunk, dict):
            raw = chunk.get("embedding_blob")
            js = chunk.get("embedding")
        else:
            raw = getattr(chunk, "embedding_blob", None)
            js = getattr(chunk, "embedding", None)
        vec = None
        if raw:
            vec = np.frombuffer(bytes(raw), dtype="<f4").astype(np.float32)
        if vec is None or vec.size == 0:
            if not js:
                return None
            vec = np.asarray(js, dtype=np.float32)
        if vec.size == 0:
            return None
        norm = float(np.linalg.norm(vec))
        if norm <= 0:
            return None
        return (vec / norm).astype(np.float32)
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb_ann chunk_vector failed: %s", exc)
        return None


def _manifest_path() -> str:
    return os.path.join(_index_dir(), _MANIFEST_NAME)


def _read_manifest() -> dict[str, Any] | None:
    try:
        with open(_manifest_path(), encoding="utf-8") as fh:
            m = json.load(fh)
        return m if isinstance(m, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _write_manifest(m: dict[str, Any]) -> None:
    tmp = _manifest_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(m, fh)
    replace_with_retry(tmp, _manifest_path())


def _bucket_path(model: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in model)
    return os.path.join(_index_dir(), f"bucket_{safe}.faiss")


def _scan_vectors() -> dict[str, list[tuple[str, Any]]]:
    """Full-corpus vector scan (no 5000 cap): model -> [(chunk_id, vector)]."""
    from ..db.chunk_store import get_chunk_store

    out: dict[str, list[tuple[str, Any]]] = {}
    for row in get_chunk_store().embedded_vectors():
        vec = chunk_vector(row)
        if vec is None:
            continue
        model = row.get("embedding_model") or ""
        out.setdefault(model, []).append((row["id"], vec))
    return out


def _build_from_scan(faiss, np, scan: dict[str, list[tuple[str, Any]]], *, atomic: bool = False) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for model, items in scan.items():
        if not items:
            continue
        dim = int(items[0][1].shape[0])
        if any(int(v.shape[0]) != dim for _, v in items):
            logger.warning("kb_ann bucket %r has mixed dims, skipping", model)
            continue
        mat = np.stack([v for _, v in items]).astype(np.float32)
        index = faiss.IndexFlatIP(dim)
        index.add(mat)
        if atomic:
            # Background builds publish without holding _lock, so a
            # concurrent load must never observe a half-written file.
            tmp = _bucket_path(model) + ".tmp"
            faiss.write_index(index, tmp)
            replace_with_retry(tmp, _bucket_path(model))
        else:
            faiss.write_index(index, _bucket_path(model))
        buckets[model] = {
            "index": index,
            "ids": [cid for cid, _ in items],
            "dim": dim,
            "removed": set(),
        }
    return buckets


def _live_count(bucket: dict[str, Any]) -> int:
    """Indexed chunks still live: positions minus tombstones."""
    return len(bucket["ids"]) - len(bucket.get("removed") or ())


def _manifest_for(
    buckets: dict[str, dict[str, Any]], fingerprint: dict[str, Any]
) -> dict[str, Any]:
    return {
        "version": _MANIFEST_VERSION,
        "built_at": time.time(),
        "fingerprint": fingerprint,
        "buckets": {
            model: {"dim": b["dim"], "count": _live_count(b)}
            for model, b in buckets.items()
        },
    }


def _read_sidecar(path: str) -> tuple[list[str] | None, list[int]]:
    """Read a bucket id sidecar. v3 is ``{"ids": [...], "removed": [...]}``;
    a bare list is the v2 format (no tombstones). Returns ``(None, [])``
    on any malformed content (caller treats that as rebuild)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return [str(i) for i in data], []
    if isinstance(data, dict) and isinstance(data.get("ids"), list):
        removed = data.get("removed") or []
        if not isinstance(removed, list):
            return None, []
        return [str(i) for i in data["ids"]], [int(p) for p in removed]
    return None, []


def _write_sidecar(path: str, ids: list[str], removed: set[int]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"ids": ids, "removed": sorted(removed)}, fh)
    replace_with_retry(tmp, path)


def _current_fingerprint() -> dict[str, Any] | None:
    """DB fingerprint of the indexed population.

    Returns ``None`` when it cannot be computed (fail-open: the caller then
    rebuilds instead of trusting a possibly stale index). Never raises.
    """
    try:
        from ..db.chunk_store import get_chunk_store

        return get_chunk_store().embedded_fingerprint()
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb_ann fingerprint failed: %s", exc)
        return None


def _load_buckets(faiss, manifest: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    """Load persisted v3 buckets. Returns ``None`` on any problem (missing
    files, corrupt sidecar, dim/count mismatch) — the caller then rebuilds.
    Deliberately does NOT check the manifest fingerprint: a mismatch is
    exactly what the delta path resolves, and refusing to load would force
    a full rescan for every small change."""
    loaded: dict[str, dict[str, Any]] = {}
    for model, meta in (manifest.get("buckets") or {}).items():
        path = _bucket_path(model)
        sidecar = path + ".ids.json"
        if not (os.path.exists(path) and os.path.exists(sidecar)):
            return None
        try:
            index = faiss.read_index(path)
            ids, removed = _read_sidecar(sidecar)
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb_ann load failed for %r: %s", model, exc)
            return None
        if (
            ids is None
            or int(index.ntotal) != len(ids)
            or int(meta.get("dim", -1)) != int(index.d)
            or any(p < 0 or p >= len(ids) for p in removed)
        ):
            logger.warning("kb_ann sidecar mismatch for %r, rebuilding", model)
            return None
        loaded[model] = {
            "index": index,
            "ids": ids,
            "dim": int(index.d),
            "removed": set(removed),
        }
    return loaded or None


def _vectors_for(faiss, np, dim: int, rows: list[dict]) -> tuple[Any, list[str]]:
    """Build a one-model IndexFlatIP from chunk rows (skipping unusable
    vectors, fail-open). Returns ``(index, ids)`` in row order."""
    items = []
    for row in rows:
        vec = chunk_vector(row)
        if vec is None or int(vec.shape[0]) != dim:
            logger.warning("kb_ann delta skips unusable vector for %r", row.get("id"))
            continue
        items.append((row["id"], vec))
    if not items:
        return None, []
    mat = np.stack([v for _, v in items]).astype(np.float32)
    index = faiss.IndexFlatIP(dim)
    index.add(mat)
    return index, [cid for cid, _ in items]


def _apply_delta(
    faiss, np, loaded: dict[str, dict[str, Any]], fp_before: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]] | None, dict[str, Any] | None, bool, str | None]:
    """Apply an id-set delta to loaded buckets.

    Returns ``(buckets, manifest, changed, error)``. ``changed`` is False
    when the live population already matches the buckets — the caller
    then keeps the loaded manifest untouched (``built_at`` preserved).
    On race or failure returns ``(None, None, False, error)`` and the
    caller discards in-memory state WITHOUT persisting anything, so the
    next call retries from the last good persisted state.

    Concurrency contract (same as the full build): buckets are never
    mutated in place. Appends clone the index (memcpy of the vector
    array — no DB rescan, no re-embedding) and swap pointers at the end,
    so a concurrent search holding the old bucket object stays safe.
    """
    from ..db.chunk_store import get_chunk_store

    store = get_chunk_store()
    try:
        live = store.embedded_ids()
    except Exception as exc:  # noqa: BLE001
        return None, None, False, f"live id scan failed: {exc}"
    new_buckets: dict[str, dict[str, Any]] = {}
    touched: set[str] = set()
    dropped: set[str] = set()
    for model, bucket in loaded.items():
        if model not in live:
            # Whole model population gone (deleted/archived): drop the bucket.
            dropped.add(model)
            touched.add(model)
            continue
        live_ids = live[model]
        ids = bucket["ids"]
        removed = set(bucket.get("removed") or ())
        # Un-remove: a chunk archived after tombstoning that is live again
        # (un-archived) rejoins the searchable set.
        unremoved = {p for p in removed if ids[p] in live_ids}
        removed -= unremoved
        dead = [p for p, cid in enumerate(ids) if p not in removed and cid not in live_ids]
        added = sorted(live_ids - set(ids))
        if not added and not dead and not unremoved:
            new_buckets[model] = bucket
            continue
        touched.add(model)
        new_removed = removed | set(dead)
        new_ids = list(ids)
        index = bucket["index"]
        if added:
            by_id: dict[str, Any] = {}
            for row in store.vectors_by_ids(added):
                vec = chunk_vector(row)
                if vec is not None and int(vec.shape[0]) == bucket["dim"]:
                    by_id[row["id"]] = vec
                else:
                    logger.warning("kb_ann delta skips unusable vector for %r", row.get("id"))
            vec_ids = [cid for cid in added if cid in by_id]
            if vec_ids:
                mat = np.stack([by_id[cid] for cid in vec_ids]).astype(np.float32)
                index = faiss.clone_index(bucket["index"])
                index.add(mat)
                new_ids = list(ids) + vec_ids
        if new_removed and len(new_removed) > _TOMBSTONE_COMPACT_RATIO * len(new_ids):
            # Compaction: rebuild this bucket from its live vectors only.
            # Still not a full-corpus rebuild — other models are untouched.
            live_cids = [cid for p, cid in enumerate(new_ids) if p not in new_removed]
            fresh, fresh_ids = _vectors_for(faiss, np, bucket["dim"], store.vectors_by_ids(live_cids))
            if fresh is not None:
                index, new_ids, new_removed = fresh, fresh_ids, set()
        new_buckets[model] = {
            "index": index,
            "ids": new_ids,
            "dim": bucket["dim"],
            "removed": new_removed,
        }
    for model, live_ids in live.items():
        if model in new_buckets:
            continue
        # Brand-new model bucket from its live vectors only.
        rows = store.vectors_by_ids(sorted(live_ids))
        if not rows:
            continue
        dim = None
        for row in rows:
            vec = chunk_vector(row)
            if vec is not None:
                dim = int(vec.shape[0])
                break
        if dim is None:
            continue
        fresh, fresh_ids = _vectors_for(faiss, np, dim, rows)
        if fresh is None:
            continue
        new_buckets[model] = {
            "index": fresh,
            "ids": fresh_ids,
            "dim": dim,
            "removed": set(),
        }
        touched.add(model)
    if not touched and not dropped:
        return loaded, None, False, None
    # Race guard (mirrors the full build): a write landing inside the delta
    # window changes the fingerprint, so fp_new != fp_before proves the
    # delta no longer matches the DB. Discard, persist nothing.
    fp_new = _current_fingerprint()
    if fp_new is None or fp_new != fp_before:
        return None, None, False, "delta raced with concurrent DB write, discarded"
    manifest = _manifest_for(new_buckets, fp_new)
    try:
        for model in touched:
            if model in dropped:
                for suffix in ("", ".ids.json"):
                    try:
                        os.remove(_bucket_path(model) + suffix)
                    except OSError:
                        pass
                continue
            bucket = new_buckets[model]
            faiss.write_index(bucket["index"], _bucket_path(model))
            _write_sidecar(_bucket_path(model) + ".ids.json", bucket["ids"], bucket["removed"])
        _write_manifest(manifest)
    except Exception as exc:  # noqa: BLE001
        return None, None, False, f"delta persist failed: {exc}"
    return new_buckets, manifest, True, None


def ensure_index() -> dict[str, Any]:
    """Build, load, or incrementally update the faiss index. Fail-open: never raises.

    Staleness rule: the index is trusted only while the DB fingerprint
    (per-model count/max_created_at over the indexed population, recorded
    in the manifest at build time) still matches the live database.

    Stage 3 / SR-028 (3-5): a fingerprint change no longer forces a full
    corpus rescan. When persisted v3 buckets load, the update is an id-set
    delta (append new chunks to a cloned index, tombstone removed ones,
    compact a bucket past the tombstone ratio, build brand-new model
    buckets from their live vectors only). The fingerprint itself is
    unchanged — it remains the cheap change detector; incrementality
    comes from the delta, and the manifest version bump (2 -> 3) marks
    the sidecar format that carries tombstones. A manifest version
    mismatch, missing/corrupt files, or a fingerprint race mid-delta
    still falls back to a full rebuild (or a not-ready discard, mirroring
    the build race guard). An uncomputable fingerprint also forces a
    rebuild attempt.

    Stage 5 / SR-093: the full rebuild no longer blocks queries. When an
    in-memory index exists, the rebuild runs in a background thread and
    this call returns the old index as ready; queries serve it until the
    new index swaps in atomically (see _rebuild_job). Only a first build
    (nothing to serve) still runs synchronously, and the delta path —
    incremental and bounded — stays synchronous so its freshness
    contract is unchanged.

    Returns ``{"ready": bool, "buckets": {...}, "error": str|None}``.
    """
    global _buckets, _manifest, _last_error, _build_thread
    faiss, np = _faiss(), _numpy()
    if faiss is None or np is None:
        return {"ready": False, "buckets": {}, "error": "faiss/numpy unavailable"}
    with _lock:
        fp = _current_fingerprint()
        fp_ok = (
            fp is not None
            and _manifest is not None
            and _manifest.get("fingerprint") == fp
        )
        if _buckets and fp_ok:
            return {"ready": True, "buckets": _manifest or {}, "error": None}
        manifest = _read_manifest()
        loaded = None
        if manifest and manifest.get("version") == _MANIFEST_VERSION:
            loaded = _load_buckets(faiss, manifest)
        if loaded is not None and fp is not None:
            buckets, new_manifest, changed, error = _apply_delta(faiss, np, loaded, fp)
            if buckets is not None:
                _buckets = buckets
                if changed:
                    _manifest = new_manifest
                else:
                    # No change: keep the loaded manifest untouched so
                    # built_at stays stable across disk reloads.
                    _manifest = manifest
                _last_error = None
                return {"ready": True, "buckets": _manifest or {}, "error": None}
            # Delta raced or failed: drop in-memory state, persist nothing.
            _buckets, _manifest = {}, None
            _last_error = error or "delta update failed"
            logger.info("kb_ann %s", _last_error)
            return {"ready": False, "buckets": {}, "error": _last_error}
        if _buckets and fp is not None:
            # Stage 5 / SR-093: a full rebuild is needed but an index is
            # already serving — rebuild in the background and keep serving
            # the old buckets until the new ones swap in atomically (see
            # _rebuild_job for the switch semantics). Single-flight: a
            # running rebuild is never duplicated. Callers that need the
            # fresh index (tests, ops) use wait_for_build().
            if _build_thread is None or not _build_thread.is_alive():
                _build_thread = threading.Thread(
                    target=_rebuild_job, args=(faiss, np, fp, _generation), daemon=True
                )
                _build_thread.start()
            return {"ready": True, "buckets": _manifest or {}, "error": None}
        if _buckets:
            logger.info(
                "kb_ann fingerprint changed, dropping in-memory index for rebuild"
            )
            _buckets, _manifest = {}, None
        elif manifest and manifest.get("version") != _MANIFEST_VERSION:
            logger.info(
                "kb_ann manifest version %r != %r, rebuilding",
                manifest.get("version"),
                _MANIFEST_VERSION,
            )
        # (Re)build from the database. ids live only in the manifest sidecar.
        try:
            scan = _scan_vectors()
        except Exception as exc:  # noqa: BLE001
            _last_error = f"vector scan failed: {exc}"
            logger.warning("kb_ann %s", _last_error)
            return {"ready": False, "buckets": {}, "error": _last_error}
        buckets = _build_from_scan(faiss, np, scan)
        if not buckets:
            _last_error = "no embeddable vectors in corpus"
            return {"ready": False, "buckets": {}, "error": _last_error}
        # Race guard: compare the pre-scan fingerprint with the post-scan
        # one. Any write landing inside the scan window changes count or
        # max_created_at, so fp != fp_new proves the scanned vectors no
        # longer match the DB. Discard the build (no manifest, no files)
        # and let the next ensure_index rebuild — never trust a raced index.
        fp_new = _current_fingerprint()
        if fp is None or fp_new is None or fp_new != fp:
            _last_error = "build raced with concurrent DB write, discarded"
            logger.info("kb_ann %s", _last_error)
            return {"ready": False, "buckets": {}, "error": _last_error}
        manifest = _manifest_for(buckets, fp_new)
        # Persist chunk-id sidecars alongside each bucket (faiss files don't
        # carry ids; the sidecar is what makes load possible).
        for model, b in buckets.items():
            _write_sidecar(_bucket_path(model) + ".ids.json", b["ids"], b["removed"])
        _write_manifest(manifest)
        _buckets, _manifest, _last_error = buckets, manifest, None
        return {"ready": True, "buckets": manifest, "error": None}


def _rebuild_job(faiss, np, fp_before: dict[str, Any], generation: int) -> None:
    """Background full rebuild (Stage 5 / SR-093). Switch semantics:

    - Queries keep serving the old buckets for the whole job: the job
      builds fresh objects and never touches ``_buckets`` until the swap.
    - The job's own race guard (pre-scan vs post-scan fingerprint) still
      applies, exactly like the synchronous build.
    - Publish order is sidecars, then manifest, all atomic renames, so
      persisted state is always a self-consistent pair.
    - The swap itself re-checks both the generation (invalidate() since
      job start discards) and the live fingerprint (a write landing
      between the race guard and the swap discards). On any discard the
      old in-memory index keeps serving and the next ensure retries.
    """
    global _build_thread, _buckets, _manifest, _last_error
    try:
        scan = _scan_vectors()
        buckets = _build_from_scan(faiss, np, scan, atomic=True)
        if not buckets:
            raise RuntimeError("no embeddable vectors in corpus")
        fp_new = _current_fingerprint()
        if fp_new is None or fp_new != fp_before:
            logger.info("kb_ann background build raced with concurrent DB write, discarded")
            return
        manifest = _manifest_for(buckets, fp_new)
        if generation != _generation:
            logger.info("kb_ann background build superseded by invalidate(), discarded")
            return
        for model, b in buckets.items():
            _write_sidecar(_bucket_path(model) + ".ids.json", b["ids"], b["removed"])
        _write_manifest(manifest)
    except Exception as exc:  # noqa: BLE001 - fail-open, like every path here
        logger.warning("kb_ann background build failed: %s", exc)
        with _lock:
            _last_error = f"background build failed: {exc}"
            _build_thread = None
        return
    with _lock:
        _build_thread = None
        if generation != _generation:
            logger.info("kb_ann background build superseded by invalidate(), discarded")
            return
        fp_now = _current_fingerprint()
        if fp_now is None or fp_now != fp_new:
            logger.info("kb_ann background build stale at swap, discarded")
            return
        _buckets, _manifest, _last_error = buckets, manifest, None


def wait_for_build(timeout_s: float = 30.0) -> bool:
    """Join a running background rebuild, if any.

    For tests and ops that need the fresh index rather than the served
    old one. Returns True when an index is in memory afterwards.
    """
    with _lock:
        thread = _build_thread
    if thread is not None:
        thread.join(timeout=timeout_s)
    with _lock:
        return bool(_buckets)


def search(model: str, query_vec: list[float], k: int) -> list[tuple[str, float]] | None:
    """Top-k ANN search in one model bucket.

    Returns ``[(chunk_id, cosine_score)]`` or ``None`` when the index is not
    usable (caller falls back to brute force). Never raises.

    Stage 5 / SR-093: no lock here. Buckets are never mutated in place
    (deltas clone, builds create fresh dicts) and the publish step is a
    single reference assignment, so the bucket grabbed here stays valid
    for the whole search even while a rebuild runs — this is what
    unblocks queries during builds, together with the background job.
    """
    np = _numpy()
    if np is None:
        return None
    bucket = _buckets.get(model or "")
    if not bucket:
        return None
    try:
        qv = np.asarray(query_vec, dtype=np.float32)
        if int(qv.shape[0]) != int(bucket["dim"]):
            return None
        norm = float(np.linalg.norm(qv))
        if norm <= 0:
            return None
        qv = (qv / norm).astype(np.float32).reshape(1, -1)
        index = bucket["index"]
        removed = bucket.get("removed") or set()
        # Over-fetch past tombstones: at most len(removed) of the top
        # kk results can be dead, so kk = k + len(removed) guarantees k
        # live hits whenever k live chunks exist in the bucket.
        kk = max(1, min(int(k) + len(removed), int(index.ntotal)))
        scores, idx = index.search(qv, kk)
        ids = bucket["ids"]
        out: list[tuple[str, float]] = []
        for s, i in zip(scores[0].tolist(), idx[0].tolist()):
            if 0 <= int(i) < len(ids) and int(i) not in removed:
                out.append((ids[int(i)], float(s)))
            if len(out) >= int(k):
                break
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("kb_ann search failed: %s", exc)
        return None


def invalidate() -> None:
    """Drop the in-memory index (forces rebuild on next ensure_index)."""
    global _buckets, _manifest, _last_error, _generation
    with _lock:
        _buckets, _manifest, _last_error = {}, None, None
        # A running background job keeps its generation and therefore
        # cannot publish over this fresh state (see _rebuild_job).
        _generation += 1


def index_stats() -> dict[str, Any]:
    """Observability snapshot for /api/ops/kb-health (fail-open)."""
    with _lock:
        buckets = {
            model: {
                "dim": b["dim"],
                "count": _live_count(b),
                "removed": len(b.get("removed") or ()),
            }
            for model, b in _buckets.items()
        }
        err = _last_error
    files = []
    try:
        d = _index_dir()
        files = sorted(f for f in os.listdir(d) if f.endswith(".faiss"))
    except Exception:  # noqa: BLE001
        pass
    return {
        "loaded": bool(buckets),
        "buckets": buckets,
        "persisted_files": files,
        "last_error": err,
    }
