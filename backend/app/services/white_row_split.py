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

# P2-B30: PIL / numpy are lazy, not import-time hard deps — unlike the
# chem=None degradation elsewhere, this module used to fail at import
# when either was missing. Callers now get a catchable
# WhiteRowSplitUnavailableError at call time instead.
try:
    import numpy as np
    from PIL import Image
except ImportError:  # pragma: no cover - exercised by dependency tests
    np = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]


class WhiteRowSplitUnavailableError(RuntimeError):
    """PIL / numpy are required for white-row splitting."""


def _require_deps() -> None:
    if np is None or Image is None:
        raise WhiteRowSplitUnavailableError(
            "white-row splitting needs PIL and numpy"
        )

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
    _require_deps()
    img = Image.open(io.BytesIO(image_bytes)).convert("L")
    arr = np.asarray(img)
    # P3-B15: no ndim==0 branch — convert("L") always yields a 2-D array,
    # so the old 0-d fallback was unreachable.
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
    Empty cuts mean the image came back whole (no valid first cut).

    P3-B17: unparseable bytes raise PIL's UnidentifiedImageError — this
    module does NOT fail open here. Callers must catch it (there is no
    app/ caller today; tests pass valid images).
    """
    _require_deps()
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = img.size
    white_rows = _white_rows(image_bytes)
    lo = int(height * first_cut_min_fraction)
    hi = int(height * first_cut_max_fraction)
    # P3-B16: a cut needs content on BOTH sides — a white row whose
    # upper side or lower side is entirely blank is a margin / trailing
    # gap, not a figure boundary. (white_rows is True for WHITE rows, so
    # content means ~white_rows; the first version tested the mask
    # un-negated and cut all-white images — fixed after the batch-7
    # directed run flagged test_b28_tiny_all_white_image_returns_whole.)
    # Known limitation, documented: a white gap INSIDE one figure still
    # passes this gate, since both sides have content; the band + step
    # heuristics bound that case but cannot rule it out. A rejected band
    # falls back to the whole image, the module's safe default.)
    first_cut = None
    for row in range(lo, min(hi + 1, height)):
        if white_rows[row]:
            if (~white_rows[:row]).any() and (~white_rows[row + 1:]).any():
                first_cut = row
                break
    # P2-B28: a first cut at row 0 is not a cut at all — it would yield
    # an empty first segment while cuts claims a split happened. Treat
    # it as "no valid first cut" and return the whole image.
    if first_cut is None or first_cut == 0:
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

    P2-B29: numpy booleans count as real bools — np.bool_(False) used
    to fall into "uncertain -> keep" and return True.
    """
    if isinstance(decision, bool) or (
        np is not None and isinstance(decision, np.bool_)
    ):
        return bool(decision)
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
    """Drop only no_image_content segments; everything else is kept.

    P1-B20: the old zip truncated silently when the label list was
    shorter than the segment list. A segment without a label is
    uncertain, and this module is recall-biased — it is KEPT, never
    dropped for lack of a label. Extra labels past the last segment
    refer to nothing and are ignored.
    """
    out: list[bytes] = []
    for idx, seg in enumerate(segments):
        label = qc_labels[idx] if idx < len(qc_labels) else QC_OK
        if label != QC_NO_IMAGE_CONTENT:
            out.append(seg)
    return out
