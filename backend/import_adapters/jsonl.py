"""JSONL adapter — one JSON object per line.

Line schema:
    {"filename": "...", "annotations": [{"label": "...", "ann_type": "...", "bbox_json": {...}}]}

Blank lines are ignored. Lines that fail json.loads become errors.
"""

import json
from pydantic import ValidationError

from ..schemas import AnnotationCreate, ImportErrorItem
from . import NormalizedImportItem


def parse(raw_bytes: bytes) -> tuple[list[NormalizedImportItem], list[ImportErrorItem]]:
    items: list[NormalizedImportItem] = []
    errors: list[ImportErrorItem] = []

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        errors.append(ImportErrorItem(reason=f"file is not valid UTF-8: {e}"))
        return items, errors

    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(ImportErrorItem(row=i, reason=f"invalid JSON: {e}"))
            continue

        filename = obj.get("filename")
        if not filename:
            errors.append(ImportErrorItem(row=i, reason="missing 'filename'"))
            continue

        raw_anns = obj.get("annotations", [])
        if not isinstance(raw_anns, list):
            errors.append(ImportErrorItem(row=i, filename=filename,
                                          reason="'annotations' must be a list"))
            continue

        parsed_anns: list[AnnotationCreate] = []
        row_failed = False
        for j, raw in enumerate(raw_anns):
            try:
                parsed_anns.append(AnnotationCreate(**raw))
            except ValidationError as e:
                errors.append(ImportErrorItem(
                    row=i, filename=filename,
                    reason=f"annotation[{j}] invalid: {e.errors()[0]['msg']}",
                ))
                row_failed = True

        if not row_failed:
            items.append(NormalizedImportItem(filename=filename, annotations=parsed_anns))

    return items, errors
