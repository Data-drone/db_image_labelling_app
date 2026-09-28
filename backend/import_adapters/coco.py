"""COCO JSON adapter.

Converts absolute pixel bbox [x, y, w, h] to normalized 0-1 coordinates
using the image width/height from the COCO 'images' section.

When ``segmentation`` is present it is converted to an uncompressed COCO RLE
mask (the app wire format). Supported forms: uncompressed RLE
``{size, counts:[ints]}``, compressed RLE ``{size, counts:"..."}``, and
polygon lists. ``iscrowd`` is ignored. If both ``bbox`` and ``segmentation``
are present, the mask wins.

Malformed-but-valid JSON (e.g. top-level arrays, non-dict images/cats/anns)
is tolerated and surfaced as per-entry errors rather than raising.
"""

import json
import math

from pydantic import ValidationError

from .. import masks
from ..schemas import AnnotationCreate, ImportErrorItem
from . import NormalizedImportItem

# Cap polygon complexity so a hostile file cannot spend minutes in rasterise.
_MAX_POLYGON_VERTICES = 10_000
_MAX_POLYGON_RINGS = 1_000


def parse(raw_bytes: bytes) -> tuple[list[NormalizedImportItem], list[ImportErrorItem]]:
    """Parse a COCO JSON file into normalized items + adapter-level errors."""
    items: list[NormalizedImportItem] = []
    errors: list[ImportErrorItem] = []

    try:
        data = json.loads(raw_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        errors.append(ImportErrorItem(row=None, filename=None,
                                      reason=f"invalid COCO JSON: {e}"))
        return items, errors

    if not isinstance(data, dict):
        errors.append(ImportErrorItem(row=None, filename=None,
                                      reason="top-level COCO value must be a JSON object"))
        return items, errors

    raw_images = data.get("images", []) or []
    raw_cats = data.get("categories", []) or []
    raw_anns = data.get("annotations", []) or []
    if not (isinstance(raw_images, list) and isinstance(raw_cats, list)
            and isinstance(raw_anns, list)):
        errors.append(ImportErrorItem(
            row=None, filename=None,
            reason="'images', 'categories', 'annotations' must be lists",
        ))
        return items, errors

    # Build image map: image_id -> (filename, width, height).
    # Also detect duplicate file_name values across images[] entries.
    image_map: dict = {}
    seen_filenames: dict[str, int] = {}  # fname -> first image_id
    for img in raw_images:
        if not isinstance(img, dict):
            continue
        iid = img.get("id")
        fname = img.get("file_name")
        w = img.get("width")
        h = img.get("height")
        if (iid is None or not isinstance(fname, str) or not fname
                or not isinstance(w, (int, float)) or isinstance(w, bool)
                or not isinstance(h, (int, float)) or isinstance(h, bool)
                or w <= 0 or h <= 0
                or not math.isfinite(w) or not math.isfinite(h)):
            continue
        if fname in seen_filenames:
            errors.append(ImportErrorItem(
                row=None, filename=fname,
                reason=(
                    f"duplicate file_name in images[]: image_id {iid!r} "
                    f"collides with image_id {seen_filenames[fname]!r}"
                ),
            ))
            continue
        seen_filenames[fname] = iid
        image_map[iid] = (fname, float(w), float(h))

    # Build category map: category_id -> label.
    cat_map: dict = {}
    for cat in raw_cats:
        if not isinstance(cat, dict):
            continue
        cid = cat.get("id")
        name = cat.get("name")
        if cid is not None and isinstance(name, str) and name:
            cat_map[cid] = name

    # Group annotations by image.
    per_image: dict[str, list[AnnotationCreate]] = {}
    for ann_idx, ann in enumerate(raw_anns):
        row = ann_idx + 1  # 1-based sequential (Minor #5: not the raw COCO id)

        if not isinstance(ann, dict):
            errors.append(ImportErrorItem(
                row=row, filename=None,
                reason="annotation entry must be a JSON object",
            ))
            continue

        iid = ann.get("image_id")
        cid = ann.get("category_id")
        if iid not in image_map:
            errors.append(ImportErrorItem(
                row=row, filename=None,
                reason=f"image_id {iid!r} not in images[]",
            ))
            continue
        if cid not in cat_map:
            fname, _, _ = image_map[iid]
            errors.append(ImportErrorItem(
                row=row, filename=fname,
                reason=f"category_id {cid!r} not in categories[]",
            ))
            continue

        fname, width, height = image_map[iid]
        label = cat_map[cid]

        bbox = ann.get("bbox")
        seg = ann.get("segmentation")
        ann_raw: dict = {"label": label}
        if _segmentation_present(seg):
            try:
                ann_raw["ann_type"] = "mask"
                ann_raw["mask_json"] = _mask_json_from_segmentation(seg, height, width)
                ann_raw["bbox_json"] = None
            except (masks.MaskValidationError, ValueError, TypeError) as e:
                errors.append(ImportErrorItem(
                    row=row, filename=fname,
                    reason=f"invalid segmentation: {e}",
                ))
                continue
        elif bbox is None:
            ann_raw["ann_type"] = "classification"
            ann_raw["bbox_json"] = None
        else:
            if (not isinstance(bbox, list) or len(bbox) != 4
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                               and math.isfinite(v) for v in bbox)):
                errors.append(ImportErrorItem(
                    row=row, filename=fname,
                    reason=f"bbox must be [x,y,w,h] of numbers, got {bbox!r}",
                ))
                continue
            ann_raw["ann_type"] = "bbox"
            ann_raw["bbox_json"] = {
                "x": float(bbox[0]) / width,
                "y": float(bbox[1]) / height,
                "w": float(bbox[2]) / width,
                "h": float(bbox[3]) / height,
            }

        try:
            ac = AnnotationCreate(**ann_raw)
        except (ValidationError, TypeError, KeyError) as e:
            if isinstance(e, ValidationError):
                msg = e.errors()[0]["msg"]
            else:
                msg = str(e)
            errors.append(ImportErrorItem(
                row=row, filename=fname,
                reason=f"annotation invalid: {msg}",
            ))
            continue

        per_image.setdefault(fname, []).append(ac)

    for fname, anns in per_image.items():
        items.append(NormalizedImportItem(filename=fname, annotations=anns))

    return items, errors


def _segmentation_present(seg) -> bool:
    """True when the annotation actually carries a mask/polygon payload."""
    if seg is None:
        return False
    if isinstance(seg, (list, dict, str)) and len(seg) == 0:
        return False
    return True


def _image_hw(height: float, width: float) -> tuple[int, int]:
    ih, iw = int(height), int(width)
    if ih != height or iw != width:
        raise masks.MaskValidationError(
            f"image size {height}x{width} is not integer; cannot import a mask"
        )
    if ih <= 0 or iw <= 0:
        raise masks.MaskValidationError("image size must be positive")
    if ih > masks.MAX_MASK_DIMENSION or iw > masks.MAX_MASK_DIMENSION:
        raise masks.MaskValidationError(
            f"mask dimensions exceed {masks.MAX_MASK_DIMENSION}px limit"
        )
    if ih * iw > masks.MAX_MASK_PIXELS:
        raise masks.MaskValidationError(
            f"mask has {ih * iw} pixels, limit is {masks.MAX_MASK_PIXELS}"
        )
    return ih, iw


def _mask_json_from_segmentation(seg, height: float, width: float) -> dict:
    """Convert a COCO ``segmentation`` field to uncompressed wire RLE."""
    ih, iw = _image_hw(height, width)

    if isinstance(seg, dict):
        payload = dict(seg)
        if payload.get("size") is None:
            payload["size"] = [ih, iw]
        h, w, counts = masks.validate_uncompressed(payload, ih, iw)
        return {"size": [h, w], "counts": counts}

    if isinstance(seg, str):
        h, w, counts = masks.validate_uncompressed(
            {"size": [ih, iw], "counts": seg}, ih, iw,
        )
        return {"size": [h, w], "counts": counts}

    if isinstance(seg, list):
        rings = _normalize_polygons(seg)
        bitmap = _rasterize_polygons(rings, ih, iw)
        h, w, counts = masks.counts_from_array(bitmap)
        # Re-run validation so empty / malformed rasters share the same errors.
        h, w, counts = masks.validate_uncompressed(
            {"size": [h, w], "counts": counts}, ih, iw,
        )
        return {"size": [h, w], "counts": counts}

    raise masks.MaskValidationError(
        "segmentation must be an RLE object or a polygon list"
    )


def _normalize_polygons(seg: list) -> list[list[float]]:
    if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in seg):
        rings = [seg]
    elif all(isinstance(x, (list, tuple)) for x in seg):
        rings = list(seg)
    else:
        raise masks.MaskValidationError(
            "segmentation polygons must be [x,y,...] or a list of those rings"
        )
    if len(rings) > _MAX_POLYGON_RINGS:
        raise masks.MaskValidationError(
            f"segmentation has {len(rings)} polygons, limit is {_MAX_POLYGON_RINGS}"
        )

    out: list[list[float]] = []
    vertices = 0
    for ring in rings:
        if not isinstance(ring, (list, tuple)) or len(ring) < 6 or len(ring) % 2:
            raise masks.MaskValidationError(
                "each polygon must be an even-length [x,y,...] list of at least 3 points"
            )
        coords: list[float] = []
        for v in ring:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise masks.MaskValidationError(
                    "polygon coordinates must be finite numbers"
                )
            coords.append(float(v))
        vertices += len(coords) // 2
        if vertices > _MAX_POLYGON_VERTICES:
            raise masks.MaskValidationError(
                f"polygon has more than {_MAX_POLYGON_VERTICES} vertices"
            )
        out.append(coords)
    return out


def _rasterize_polygons(rings: list[list[float]], height: int, width: int):
    """Fill polygons onto a (height, width) uint8 bitmap, then RLE via masks.py."""
    import numpy as np
    from PIL import Image, ImageDraw

    im = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(im)
    for ring in rings:
        pts = [(ring[i], ring[i + 1]) for i in range(0, len(ring), 2)]
        draw.polygon(pts, outline=1, fill=1)
    return np.asarray(im, dtype=np.uint8)
