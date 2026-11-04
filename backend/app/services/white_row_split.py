"""Three-library C1: MERMaid white-row splitting + segment QC (pure pixels).

Grayscale binarisation; a row is "white" when at least 99.5% of its
pixels are near-white (>= 250, tolerating JPEG noise). The first cut
must fall inside the top 1/4–3/8 band of the image; later cuts step
every 120px, each snapped to the nearest white row in its window. When
no valid first cut exists the whole image is returned (fail back to
unsplit). Segments are returned as PNG bytes with their cut rows.

Segment QC taxonomy: no_image_content (blank segment) / no_caption /
multiple_captions / ok. misaligned_caption is NOT implemented: it needs
caption bounding boxes, which the pixel path does not carry.

Recall-biased gate: only CERTAINLY blank segments are dropped; anything
uncertain is kept (True). Boolean parsing uses json.loads on real JSON
booleans — never the substring ``"true" in response`` anti-pattern.
"""
from __future__ import annotations

import io
import json

import numpy as np
from PIL import Image

WHITE_THRESHOLD = 250
WHITE_ROW_RATIO = 0.995
FIRST_CUT_MIN_FRACTION = 0.25
FIRST_CUT_MAX_FRACTION = 0.375
EVEN_STEP_PX = 120

QC_OK = "ok"
QC_NO_IMAGE_CONTENT = "no_image_content"
QC_NO_CAPTION = "no_caption"
QC_MULTIPLE_CAPTIONS = "multiple_captions"


def _white_rows(image_bytes: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    arr = np.asarray(img)
    if arr.ndim == 0:
        return np.array([True])
    row_white = (arr >= WHITE_THRESHOLD).mean(axis=1)
    return row_white >= WHITE_ROW_RATIO


def _nearest_white_row(white_rows: np.ndarray, target: int) -> int | None:
    """Nearest white row to target, scanning outward; None if none."""
    n = len(white_rows)
    if n == 0:
        return None
    target = max(0, min(n - 1, target))
    for dist in range(n):
        for cand in (target - dist, target + dist):
            if 0 <= cand < n and white_rows[cand]:
                return cand
    return None


def split_white_rows(
    image_bytes: bytes,
    *,
    first_cut_min_fraction: float = FIRST_CUT_MIN_FRACTION,
    first_cut_max_fraction: float = FIRST_CUT_MAX_FRACTION,
    even_step_px: int = EVEN_STEP_PX,
) -> tuple[list[bytes], list[int]]:
    """Split an image at white rows. Returns (segment PNG bytes, cut rows).
    Empty cuts mean the image came back whole (no valid first cut)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = img.size
    white_rows = _white_rows(image_bytes)
    lo = int(height * first_cut_min_fraction)
    hi = int(height * first_cut_max_fraction)
    first_cut = None
    for row in range(lo, min(hi + 1, height)):
        if white_rows[row]:
            first_cut = row
            break
    if first_cut is None:
        return [_png_bytes(img)], []
    cuts = [first_cut]
    target = first_cut + even_step_px
    while target < height:
        snapped = _nearest_white_row(white_rows, target)
        if snapped is not None and snapped > cuts[-1]:
            cuts.append(snapped)
        target += even_step_px
    segments: list[bytes] = []
    bounds = [0, *cuts, height]
    for top, bottom in zip(bounds, bounds[1:]):
        if bottom > top:
            segments.append(_png_bytes(img.crop((0, top, width, bottom))))
    return segments, cuts


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def qc_segment(
    segment_bytes: bytes, *, captions: list[str] | None = None
) -> str:
    """QC label for one segment. captions=None means captions were not
    checked (recall-biased: not a failure)."""
    white_rows = _white_rows(segment_bytes)
    if len(white_rows) and bool(white_rows.all()):
        return QC_NO_IMAGE_CONTENT
    if captions is None:
        return QC_OK
    if len(captions) == 0:
        return QC_NO_CAPTION
    if len(captions) > 1:
        return QC_MULTIPLE_CAPTIONS
    return QC_OK


def recall_gate(decision) -> bool:
    """Keep (True) unless the decision is certainly False.

    Real bools pass through; strings must parse as JSON booleans;
    anything else (unparsable, wrong type, None) is uncertain -> keep.
    """
    if isinstance(decision, bool):
        return decision
    if isinstance(decision, str):
        try:
            parsed = json.loads(decision)
        except Exception:
            return True
        return parsed if isinstance(parsed, bool) else True
    return True


def filter_segments(
    segments: list[bytes], qc_labels: list[str]
) -> list[bytes]:
    """Drop only no_image_content segments; everything else is kept."""
    return [
        seg for seg, label in zip(segments, qc_labels)
        if label != QC_NO_IMAGE_CONTENT
    ]
