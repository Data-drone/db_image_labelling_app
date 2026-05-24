"""Sample pixel dimensions, in the orientation the browser renders.

Why this exists
---------------
Masks are stored as run-length data over a specific pixel grid, so the backend
has to know that grid exactly. Two traps:

1. **Never trust the client's declared size.** A stale tab or a resized window
   would silently write a mask against the wrong grid. Dimensions are resolved
   server-side from the image bytes and the submitted ``size`` is checked
   against them.

2. **EXIF orientation.** ``PIL.Image.size`` reports the stored, pre-rotation
   size, while a browser applies the EXIF orientation tag when rendering
   ``<img>``. On a portrait phone photo those disagree by a 90-degree
   transpose, which would land every mask sideways. ``exif_transpose`` puts us
   in the browser's frame.

Dimensions are cached on ``ProjectSample.width``/``height`` and resolved
lazily. They are deliberately *not* populated at import time: the import path
only lists UC Volume paths and never downloads the files, so doing it there
would add one download per image to every import. Instead we cache
opportunistically at the points that already hold the bytes -- serving the
image, serving a thumbnail, exporting -- which means by the time a user can
draw a mask the editor has already fetched the image and warmed the cache.
"""
from __future__ import annotations

import io
import logging
from typing import Optional, Tuple

log = logging.getLogger(__name__)


def dimensions_from_bytes(data: bytes) -> Optional[Tuple[int, int]]:
    """``(width, height)`` as the browser will render it, or None."""
    try:
        from PIL import Image as PILImage, ImageOps
        with PILImage.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            return int(img.width), int(img.height)
    except Exception as e:
        log.warning("Could not read image dimensions: %s", e)
        return None


def cache_dimensions(db, sample, data: bytes) -> None:
    """Fill in a sample's dimensions from bytes already in memory.

    Best-effort and non-fatal: this is called from read paths, where failing
    to cache a size must never turn into a failed image request. The caller
    is responsible for committing.
    """
    if sample.width and sample.height:
        return
    dims = dimensions_from_bytes(data)
    if not dims:
        return
    sample.width, sample.height = dims
    try:
        db.commit()
    except Exception as e:
        log.warning("Could not persist dimensions for sample %s: %s", sample.id, e)
        db.rollback()


def resolve_dimensions(db, sample) -> Optional[Tuple[int, int]]:
    """``(width, height)`` for a sample, downloading the image if needed.

    Returns None when the image cannot be read at all, which callers that
    need a mask grid must treat as a hard error rather than a default.
    """
    if sample.width and sample.height:
        return int(sample.width), int(sample.height)
    from .volumes import read_image_bytes
    data = read_image_bytes(sample.filepath)
    if not data:
        return None
    dims = dimensions_from_bytes(data)
    if not dims:
        return None
    sample.width, sample.height = dims
    try:
        db.commit()
    except Exception as e:
        log.warning("Could not persist dimensions for sample %s: %s", sample.id, e)
        db.rollback()
    return dims


def normalize_for_export(data: bytes) -> Tuple[bytes, Optional[int], Optional[int]]:
    """Return ``(bytes, width, height)`` consistent with stored mask grids.

    Masks are stored in the EXIF-applied orientation. If we exported the
    original bytes for a rotated photo, a consumer opening it with PIL would
    get the pre-rotation size and every mask would land sideways. So when an
    image carries a non-trivial orientation tag we re-encode it upright; when
    it does not -- the overwhelmingly common case -- the original bytes are
    passed through untouched so exports stay byte-identical to the source.
    """
    try:
        from PIL import Image as PILImage, ImageOps
        with PILImage.open(io.BytesIO(data)) as img:
            raw_size = img.size
            fmt = img.format or "PNG"
            upright = ImageOps.exif_transpose(img)
            if upright.size == raw_size:
                return data, int(raw_size[0]), int(raw_size[1])
            buf = io.BytesIO()
            save_fmt = "JPEG" if fmt.upper() in ("JPEG", "JPG", "MPO") else "PNG"
            if save_fmt == "JPEG":
                upright = upright.convert("RGB")
                upright.save(buf, format="JPEG", quality=95)
            else:
                upright.save(buf, format="PNG")
            return buf.getvalue(), int(upright.width), int(upright.height)
    except Exception as e:
        log.warning("Could not normalize image for export: %s", e)
        return data, None, None
