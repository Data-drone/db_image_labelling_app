"""COCO JSON adapter.

Converts absolute pixel bbox [x, y, w, h] to normalized 0-1 coordinates
using the image width/height from the COCO 'images' section.

Ignores 'iscrowd' and 'segmentation'.

Malformed-but-valid JSON (e.g. top-level arrays, non-dict images/cats/anns)
is tolerated and surfaced as per-entry errors rather than raising.
"""

import json
import math

from pydantic import ValidationError

from ..schemas import AnnotationCreate, ImportErrorItem
from . import NormalizedImportItem


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
        ann_raw: dict = {"label": label}
        if bbox is None:
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
