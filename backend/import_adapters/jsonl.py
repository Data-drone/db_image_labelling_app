"""JSONL adapter — one JSON object per line.

Line schema:
    {"filename": "...", "annotations": [{"label": "...", "ann_type": "...", "bbox_json": {...}}]}

Blank lines are ignored. Lines that fail json.loads become errors. Malformed
but valid JSON (e.g. top-level arrays, non-dict annotation entries) are
tolerated and surfaced as per-row errors rather than raising.
"""

import json

from pydantic import ValidationError

from ..schemas import AnnotationCreate, ImportErrorItem
from . import NormalizedImportItem


def parse(raw_bytes: bytes) -> tuple[list[NormalizedImportItem], list[ImportErrorItem]]:
    """Parse a JSONL file into normalized items + adapter-level errors."""
    items: list[NormalizedImportItem] = []
    errors: list[ImportErrorItem] = []

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        errors.append(ImportErrorItem(row=None, filename=None,
                                      reason=f"file is not valid UTF-8: {e}"))
        return items, errors

    for line_idx, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(ImportErrorItem(row=line_idx, filename=None,
                                          reason=f"invalid JSON: {e.msg}"))
            continue

        if not isinstance(obj, dict):
            errors.append(ImportErrorItem(row=line_idx, filename=None,
                                          reason="top-level value must be a JSON object"))
            continue

        filename = obj.get("filename")
        if not isinstance(filename, str) or not filename:
            errors.append(ImportErrorItem(row=line_idx, filename=None,
                                          reason="missing or non-string 'filename'"))
            continue

        raw_anns = obj.get("annotations", [])
        if not isinstance(raw_anns, list):
            errors.append(ImportErrorItem(row=line_idx, filename=filename,
                                          reason="'annotations' must be a list"))
            continue

        annotations: list[AnnotationCreate] = []
        row_failed = False
        for ann_raw in raw_anns:
            if not isinstance(ann_raw, dict):
                errors.append(ImportErrorItem(
                    row=line_idx, filename=filename,
                    reason="annotation entries must be JSON objects",
                ))
                row_failed = True
                break
            try:
                annotations.append(AnnotationCreate(**ann_raw))
            except (ValidationError, TypeError, KeyError) as e:
                if isinstance(e, ValidationError):
                    msg = e.errors()[0]["msg"]
                else:
                    msg = str(e)
                errors.append(ImportErrorItem(
                    row=line_idx, filename=filename,
                    reason=f"annotation invalid: {msg}",
                ))
                row_failed = True
                break

        if not row_failed:
            items.append(NormalizedImportItem(filename=filename, annotations=annotations))

    return items, errors
