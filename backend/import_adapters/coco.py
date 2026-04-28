"""COCO JSON adapter.

Converts absolute pixel bbox [x, y, w, h] to normalized 0-1 coordinates
using the image width/height from the COCO 'images' section.

Ignores 'iscrowd' and 'segmentation'.
"""

import json

from ..schemas import AnnotationCreate, ImportErrorItem
from . import NormalizedImportItem


def parse(raw_bytes: bytes) -> tuple[list[NormalizedImportItem], list[ImportErrorItem]]:
    errors: list[ImportErrorItem] = []

    try:
        doc = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return [], [ImportErrorItem(reason=f"invalid COCO JSON: {e}")]

    images = doc.get("images", [])
    categories = doc.get("categories", [])
    annotations = doc.get("annotations", [])

    img_map = {}  # id -> (filename, width, height)
    for img in images:
        img_id = img.get("id")
        fname = img.get("file_name")
        w = img.get("width")
        h = img.get("height")
        if img_id is None or not fname or not w or not h:
            errors.append(ImportErrorItem(
                row=img_id,
                reason=f"image row missing id/file_name/width/height: {img}",
            ))
            continue
        img_map[img_id] = (fname, w, h)

    cat_map = {}  # id -> label name
    for cat in categories:
        cat_id = cat.get("id")
        name = cat.get("name")
        if cat_id is None or not name:
            errors.append(ImportErrorItem(
                row=cat_id,
                reason=f"category row missing id/name: {cat}",
            ))
            continue
        cat_map[cat_id] = name

    grouped: dict[str, list[AnnotationCreate]] = {}

    for ann in annotations:
        ann_id = ann.get("id")
        img_id = ann.get("image_id")
        cat_id = ann.get("category_id")

        if img_id not in img_map:
            errors.append(ImportErrorItem(row=ann_id,
                                          reason=f"unknown image_id {img_id}"))
            continue
        if cat_id not in cat_map:
            errors.append(ImportErrorItem(row=ann_id,
                                          reason=f"unknown category_id {cat_id}"))
            continue

        filename, width, height = img_map[img_id]
        label = cat_map[cat_id]

        bbox = ann.get("bbox")
        if bbox is None:
            ac = AnnotationCreate(label=label, ann_type="classification", bbox_json=None)
        else:
            if not isinstance(bbox, list) or len(bbox) != 4:
                errors.append(ImportErrorItem(
                    row=ann_id, filename=filename,
                    reason=f"bbox must be [x,y,w,h], got {bbox}",
                ))
                continue
            try:
                x, y, w, h = (float(v) for v in bbox)
            except (TypeError, ValueError):
                errors.append(ImportErrorItem(
                    row=ann_id, filename=filename,
                    reason=f"bbox values must be numbers: {bbox}",
                ))
                continue
            ac = AnnotationCreate(
                label=label,
                ann_type="bbox",
                bbox_json={
                    "x": x / width,
                    "y": y / height,
                    "w": w / width,
                    "h": h / height,
                },
            )

        grouped.setdefault(filename, []).append(ac)

    items = [NormalizedImportItem(filename=fn, annotations=anns)
             for fn, anns in grouped.items()]
    return items, errors
