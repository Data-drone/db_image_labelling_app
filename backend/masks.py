"""COCO run-length-encoding (RLE) helpers for segmentation masks.

Wire format vs storage format
-----------------------------
The browser and the API speak **uncompressed** RLE::

    {"size": [height, width], "counts": [int, int, ...]}

``counts`` is a column-major (Fortran order) list of alternating run lengths
starting with *background*. The first run may be 0 when the top-left pixel is
foreground, and ``sum(counts) == height * width`` always holds.

The database stores the **compressed** COCO form, which is what
``pycocotools`` writes into ``annotations.json``::

    {"size": [height, width], "counts": "<ascii string>"}

Producing an uncompressed counts array in JavaScript is trivial run-length
encoding; producing the compressed string is not (see below). Keeping the
tricky codec in exactly one place -- here, pinned against golden vectors
generated from real ``pycocotools`` in
``backend/tests/fixtures/coco_rle_golden.json`` -- means the frontend can
never emit a subtly corrupt mask.

The compressed encoding is *not* plain LEB128
---------------------------------------------
It is a 5-bit-group, sign-extended, ASCII-offset variant, and each count from
index 3 onwards is delta-encoded against the count **two positions earlier**
(so foreground deltas against foreground, background against background).
A hand-rolled encoder and decoder that agree with each other will happily
round-trip while still writing files no COCO reader can parse, which is why
the tests here compare against reference output rather than themselves.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple

# Guard rails applied before anything is allocated. A mask is one byte per
# pixel when rasterised, so this caps a single decode at ~40 MB.
MAX_MASK_PIXELS = 40_000_000
MAX_MASK_DIMENSION = 20_000


class MaskValidationError(ValueError):
    """Raised when a client-supplied mask is malformed or out of bounds."""


# --------------------------------------------------------------------------
# compressed <-> uncompressed codec
# --------------------------------------------------------------------------
def rle_to_string(counts: Sequence[int]) -> str:
    """Encode uncompressed run lengths as a COCO compressed-RLE string.

    Port of ``rleToString`` from pycocotools' ``maskApi.c``.
    """
    out: List[str] = []
    for i, count in enumerate(counts):
        x = int(count)
        if i > 2:
            x -= int(counts[i - 2])
        more = True
        while more:
            c = x & 0x1F
            x >>= 5
            # A payload group whose high bit is set looks like a negative
            # continuation, so the terminator differs by sign.
            more = x != -1 if c & 0x10 else x != 0
            if more:
                c |= 0x20
            out.append(chr(c + 48))
    return "".join(out)


def rle_from_string(s: str) -> List[int]:
    """Decode a COCO compressed-RLE string into uncompressed run lengths.

    Port of ``rleFrString`` from pycocotools' ``maskApi.c``.
    """
    counts: List[int] = []
    p = 0
    n = len(s)
    while p < n:
        x = 0
        k = 0
        more = True
        while more:
            c = ord(s[p]) - 48
            x |= (c & 0x1F) << (5 * k)
            more = bool(c & 0x20)
            p += 1
            k += 1
            if not more and (c & 0x10):
                x |= -1 << (5 * k)  # sign-extend
            if p > n:
                raise MaskValidationError("truncated compressed RLE string")
        if len(counts) > 2:
            x += counts[len(counts) - 2]
        counts.append(x)
    return counts


# --------------------------------------------------------------------------
# derived quantities
# --------------------------------------------------------------------------
def rle_area(counts: Sequence[int]) -> int:
    """Number of foreground pixels: the odd-indexed runs."""
    return int(sum(counts[1::2]))


def rle_to_bbox(counts: Sequence[int], height: int, width: int) -> Tuple[int, int, int, int]:
    """Tight bounding box of the foreground as ``(x, y, w, h)`` in pixels.

    Computed straight from the runs rather than from a rasterised bitmap, so
    the cost is proportional to the number of runs and not to the pixel count.
    """
    x_min, y_min = width, height
    x_max = y_max = -1
    pos = 0
    for i, count in enumerate(counts):
        count = int(count)
        if i % 2 == 0:  # background
            pos += count
            continue
        if count == 0:
            continue
        start, end = pos, pos + count - 1  # inclusive flat indices, column-major
        pos += count
        col_start, col_end = start // height, end // height
        x_min = min(x_min, col_start)
        x_max = max(x_max, col_end)
        if col_start == col_end:
            row_lo, row_hi = start % height, end % height
        else:
            # A run crossing a column boundary wraps through the bottom of one
            # column and the top of the next, so it spans every row.
            row_lo, row_hi = 0, height - 1
        y_min = min(y_min, row_lo)
        y_max = max(y_max, row_hi)
    if x_max < 0:
        return (0, 0, 0, 0)
    return (x_min, y_min, x_max - x_min + 1, y_max - y_min + 1)


def counts_from_bitmap(bitmap: Iterable[int], height: int, width: int) -> List[int]:
    """Run-length encode a column-major flat 0/1 iterable.

    ``bitmap`` must already be in Fortran order, i.e. ``bitmap[y + x * height]``.
    """
    counts: List[int] = []
    prev = 0
    run = 0
    total = 0
    for value in bitmap:
        v = 1 if value else 0
        if v == prev:
            run += 1
        else:
            counts.append(run)
            prev = v
            run = 1
        total += 1
    counts.append(run)
    if total != height * width:
        raise MaskValidationError(
            f"bitmap has {total} pixels, expected {height * width}"
        )
    return counts


def counts_from_array(array) -> Tuple[int, int, List[int]]:
    """Vectorised RLE of a 2D array, returning ``(height, width, counts)``.

    The pure-Python ``counts_from_bitmap`` is fine for small masks but takes
    seconds on a multi-megapixel image, which matters when transcoding a
    bitmap mask handed back by a serving endpoint.
    """
    import numpy as np

    a = np.asarray(array)
    if a.ndim != 2:
        raise MaskValidationError(f"expected a 2D mask, got {a.ndim} dimensions")
    height, width = int(a.shape[0]), int(a.shape[1])
    if height <= 0 or width <= 0:
        raise MaskValidationError("mask array must be non-empty")
    if height * width > MAX_MASK_PIXELS:
        raise MaskValidationError(f"mask exceeds {MAX_MASK_PIXELS} pixel limit")
    flat = (a.ravel(order="F") != 0).astype(np.uint8)
    boundaries = np.flatnonzero(np.diff(flat)) + 1
    edges = np.concatenate(([0], boundaries, [flat.size]))
    counts = np.diff(edges).tolist()
    if flat[0]:
        # counts always starts with a background run, empty if the first
        # pixel is foreground.
        counts = [0] + counts
    return height, width, [int(c) for c in counts]


def bitmap_from_counts(counts: Sequence[int], height: int, width: int):
    """Rasterise uncompressed runs into a ``(height, width)`` uint8 array."""
    import numpy as np

    flat = np.zeros(height * width, dtype=np.uint8)
    pos = 0
    for i, count in enumerate(counts):
        count = int(count)
        if i % 2 == 1 and count:
            flat[pos:pos + count] = 1
        pos += count
    return flat.reshape((height, width), order="F")


# --------------------------------------------------------------------------
# validation and the wire <-> storage boundary
# --------------------------------------------------------------------------
def validate_uncompressed(
    mask: Any,
    expected_height: int | None = None,
    expected_width: int | None = None,
) -> Tuple[int, int, List[int]]:
    """Validate a client-supplied uncompressed mask.

    Every bound is checked before anything proportional to the pixel count is
    allocated. Returns ``(height, width, counts)``.
    """
    if not isinstance(mask, dict):
        raise MaskValidationError("mask must be an object")
    size = mask.get("size")
    if not isinstance(size, (list, tuple)) or len(size) != 2:
        raise MaskValidationError("mask.size must be [height, width]")
    try:
        height, width = int(size[0]), int(size[1])
    except (TypeError, ValueError):
        raise MaskValidationError("mask.size entries must be integers")
    if height <= 0 or width <= 0:
        raise MaskValidationError("mask.size entries must be positive")
    if height > MAX_MASK_DIMENSION or width > MAX_MASK_DIMENSION:
        raise MaskValidationError(
            f"mask dimensions exceed {MAX_MASK_DIMENSION}px limit"
        )
    if height * width > MAX_MASK_PIXELS:
        raise MaskValidationError(
            f"mask has {height * width} pixels, limit is {MAX_MASK_PIXELS}"
        )
    if expected_height is not None and expected_width is not None:
        if (height, width) != (expected_height, expected_width):
            raise MaskValidationError(
                f"mask size {height}x{width} does not match image "
                f"{expected_height}x{expected_width}"
            )

    raw = mask.get("counts")
    if isinstance(raw, str):
        # Tolerated so a mask read back from the API can be re-submitted
        # unchanged, and so SAM adapters can hand us either form.
        counts = rle_from_string(raw)
    elif isinstance(raw, (list, tuple)):
        if len(raw) > height * width + 1:
            raise MaskValidationError("mask.counts has more runs than pixels")
        counts = []
        for value in raw:
            if isinstance(value, bool) or not isinstance(value, int):
                raise MaskValidationError("mask.counts entries must be integers")
            if value < 0:
                raise MaskValidationError("mask.counts entries must be non-negative")
            counts.append(value)
    else:
        raise MaskValidationError("mask.counts must be a list of ints or a string")

    if not counts:
        raise MaskValidationError("mask.counts must not be empty")
    if sum(counts) != height * width:
        raise MaskValidationError(
            f"mask.counts sum {sum(counts)} != {height * width} pixels"
        )
    if rle_area(counts) == 0:
        raise MaskValidationError("mask is empty")
    return height, width, counts


def to_storage(counts: Sequence[int], height: int, width: int) -> Dict[str, Any]:
    """Compressed form persisted in ``Annotation.mask_json``."""
    return {"size": [height, width], "counts": rle_to_string(counts)}


def to_wire(stored: Any) -> Dict[str, Any] | None:
    """Uncompressed form handed to API clients, or ``None`` if unusable.

    Never raises: a mask that cannot be read back should degrade to "no mask"
    rather than break the whole labeling view.
    """
    if not isinstance(stored, dict):
        return None
    size = stored.get("size")
    counts = stored.get("counts")
    if not isinstance(size, (list, tuple)) or len(size) != 2:
        return None
    try:
        height, width = int(size[0]), int(size[1])
    except (TypeError, ValueError):
        return None
    if isinstance(counts, str):
        try:
            counts = rle_from_string(counts)
        except Exception:
            return None
    elif isinstance(counts, (list, tuple)):
        counts = [int(c) for c in counts]
    else:
        return None
    return {"size": [height, width], "counts": counts}


def normalized_bbox(counts: Sequence[int], height: int, width: int) -> Dict[str, float]:
    """Backend-authoritative normalized bbox derived from the mask itself."""
    x, y, w, h = rle_to_bbox(counts, height, width)
    return {
        "x": round(x / width, 6),
        "y": round(y / height, 6),
        "w": round(w / width, 6),
        "h": round(h / height, 6),
    }
