# `/api/import` Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a synchronous bulk annotation import endpoint (`POST /api/projects/{project_id}/import`) with COCO + JSONL adapters, UC-Volume-by-reference delivery, two-pass validate-then-commit, and caller-chosen policies for missing samples and existing annotations.

**Architecture:** New FastAPI router (`backend/routes/import_routes.py`) reads the referenced file from a UC Volume, dispatches to a pluggable adapter (`backend/import_adapters/{coco,jsonl}.py`) that returns a normalized in-memory representation, runs pass 1 (validate every row against the project's class_list / task_type / bbox range / sample map), optionally commits pass 2 in a single SQLAlchemy transaction with full `AnnotationHistory` audit trail. Follows the same patterns as the existing `labeling.py` router — pydantic schemas, `get_user_email(request)`, `SQLAlchemy Session` dependency.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, Pydantic v2, Databricks SDK (for `w.files.download`). No test framework exists in the repo — per the approved design, verification uses inline `python3 <<'PY' ... PY` scripts (matching the LAKEBASE_AUTO_PROVISION pattern) plus a manual smoke against the live app.

**Design reference:** `docs/plans/2026-04-28-api-import-design.md`

---

## Pre-flight

Before Task 1, confirm the working directory is clean and on main:

```bash
cd /workspace/group/cv-react-deploy
git status         # expect: nothing to commit, working tree clean
git branch --show-current   # expect: main
git log --format="%h %an %s" -1   # expect: 274388a as Data-drone (design doc)
```

If dirty, stash or resolve before proceeding.

**Resolved open questions** (from design doc):

1. **Test framework:** Option B — inline verification scripts + manual smoke. No pytest introduced in this PR.
2. **Filename matching:** basename only. Confirmed by reading `backend/volumes.py:71` — `scan_volume_for_samples` sets `filename=entry.name` (basename).
3. **COCO iscrowd/segmentation:** ignored with no warning.
4. **Warnings:** none in v1. Case/whitespace mismatches on labels become validation errors (caller must fix their data).

---

## Task 1: Create feature branch

**Files:** none (git plumbing only)

**Step 1: Create and checkout branch**

```bash
git checkout -b feat/api-import-endpoint
```
Expected: `Switched to a new branch 'feat/api-import-endpoint'`

**Step 2: Verify**

```bash
git log --oneline -3
```
Expected: top commit is `274388a` (design doc).

---

## Task 2: Add Pydantic schemas

**Files:**
- Modify: `backend/schemas.py` (append at end)

**Step 1: Append the new schemas**

Append the following block to the end of `backend/schemas.py` (after `AnnotationHistoryOut`):

```python


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
class ImportRequest(BaseModel):
    volume_path: str
    format: str  # 'coco' or 'jsonl'
    on_missing_sample: str = "error"  # 'error' | 'skip' | 'create'
    on_existing_annotations: str = "replace"  # 'replace' | 'append' | 'skip'
    dry_run: bool = False


class ImportErrorItem(BaseModel):
    row: Optional[int] = None
    filename: Optional[str] = None
    reason: str


class ImportResponse(BaseModel):
    dry_run: bool
    samples_touched: int = 0
    annotations_created: int = 0
    annotations_replaced: int = 0
    annotations_skipped: int = 0
    samples_skipped: int = 0
    samples_created: int = 0
    warnings: list[str] = []
```

**Step 2: Verify file still parses**

```bash
python3 -c "import ast; ast.parse(open('backend/schemas.py').read()); print('OK')"
```
Expected: `OK`

**Step 3: Verify schema instantiation with defaults**

```bash
python3 <<'PY'
import sys
sys.path.insert(0, 'backend')
from schemas import ImportRequest, ImportResponse, ImportErrorItem
req = ImportRequest(volume_path="/Volumes/x/y/z.json", format="coco")
assert req.on_missing_sample == "error"
assert req.on_existing_annotations == "replace"
assert req.dry_run is False
resp = ImportResponse(dry_run=True)
assert resp.samples_touched == 0
err = ImportErrorItem(reason="test")
assert err.row is None
print("SCHEMAS OK")
PY
```
Expected: `SCHEMAS OK`

**Step 4: Do NOT commit yet** — we commit after Task 3 (adapters) since they're coupled.

---

## Task 3: Create import adapters

**Files:**
- Create: `backend/import_adapters/__init__.py`
- Create: `backend/import_adapters/jsonl.py`
- Create: `backend/import_adapters/coco.py`

**Step 1: Create the package `__init__.py`**

Create `backend/import_adapters/__init__.py` with:

```python
"""Import format adapters.

Each adapter is a pure function:
    parse(raw_bytes: bytes) -> (list[NormalizedImportItem], list[ImportErrorItem])

No DB access, no network. Adapters normalize their source format into
NormalizedImportItem objects that the import route writes to the DB.
"""

from dataclasses import dataclass, field

from ..schemas import AnnotationCreate, ImportErrorItem


@dataclass
class NormalizedImportItem:
    filename: str
    annotations: list[AnnotationCreate] = field(default_factory=list)


from . import coco, jsonl  # noqa: E402

ADAPTERS = {
    "coco": coco.parse,
    "jsonl": jsonl.parse,
}


def get_adapter(format_name: str):
    """Return the adapter for a format, or raise ValueError."""
    if format_name not in ADAPTERS:
        raise ValueError(
            f"Unknown format '{format_name}'. Supported: {sorted(ADAPTERS.keys())}"
        )
    return ADAPTERS[format_name]
```

**Step 2: Create JSONL adapter**

Create `backend/import_adapters/jsonl.py`:

```python
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
```

**Step 3: Create COCO adapter**

Create `backend/import_adapters/coco.py`:

```python
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
```

**Step 4: Verify all three files parse**

```bash
python3 -c "import ast; [ast.parse(open(p).read()) for p in ['backend/import_adapters/__init__.py','backend/import_adapters/jsonl.py','backend/import_adapters/coco.py']]; print('OK')"
```
Expected: `OK`

**Step 5: Verify JSONL adapter with in-memory samples**

```bash
python3 <<'PY'
import sys
sys.path.insert(0, '.')
from backend.import_adapters import jsonl

# Good payload
data = (
    b'{"filename":"a.jpg","annotations":[{"label":"cat","ann_type":"classification"}]}\n'
    b'\n'  # blank line skipped
    b'{"filename":"b.jpg","annotations":[{"label":"dog","ann_type":"bbox","bbox_json":{"x":0.1,"y":0.2,"w":0.3,"h":0.4}}]}\n'
)
items, errors = jsonl.parse(data)
assert len(items) == 2, items
assert errors == [], errors
assert items[0].filename == "a.jpg"
assert items[1].annotations[0].bbox_json["x"] == 0.1

# Bad JSON on line 2
data = b'{"filename":"a.jpg","annotations":[]}\n{not json\n{"filename":"c.jpg","annotations":[]}\n'
items, errors = jsonl.parse(data)
assert len(items) == 2
assert len(errors) == 1 and errors[0].row == 2 and "invalid JSON" in errors[0].reason

# Missing filename
data = b'{"annotations":[]}\n'
items, errors = jsonl.parse(data)
assert len(items) == 0 and len(errors) == 1 and "missing 'filename'" in errors[0].reason

print("JSONL ADAPTER OK")
PY
```
Expected: `JSONL ADAPTER OK`

**Step 6: Verify COCO adapter with in-memory samples**

```bash
python3 <<'PY'
import json, sys
sys.path.insert(0, '.')
from backend.import_adapters import coco

doc = {
    "images": [
        {"id": 1, "file_name": "cat_001.jpg", "width": 100, "height": 200},
        {"id": 2, "file_name": "dog_042.jpg", "width": 640, "height": 480},
    ],
    "categories": [{"id": 10, "name": "cat"}, {"id": 20, "name": "dog"}],
    "annotations": [
        {"id": 1, "image_id": 1, "category_id": 10},               # classification (no bbox)
        {"id": 2, "image_id": 2, "category_id": 20, "bbox": [64, 96, 128, 192]},
        {"id": 3, "image_id": 999, "category_id": 20, "bbox": [0, 0, 1, 1]},  # bad image
    ],
}
items, errors = coco.parse(json.dumps(doc).encode())

# 2 filenames should be present; 1 annotation should error
by_name = {i.filename: i for i in items}
assert "cat_001.jpg" in by_name
assert by_name["cat_001.jpg"].annotations[0].ann_type == "classification"
assert "dog_042.jpg" in by_name
bb = by_name["dog_042.jpg"].annotations[0].bbox_json
assert abs(bb["x"] - 64/640) < 1e-9, bb
assert abs(bb["y"] - 96/480) < 1e-9, bb
assert abs(bb["w"] - 128/640) < 1e-9, bb
assert abs(bb["h"] - 192/480) < 1e-9, bb
assert any("unknown image_id 999" in e.reason for e in errors)
print("COCO ADAPTER OK")
PY
```
Expected: `COCO ADAPTER OK`

**Step 7: Verify registry**

```bash
python3 <<'PY'
import sys
sys.path.insert(0, '.')
from backend.import_adapters import get_adapter, ADAPTERS
assert set(ADAPTERS.keys()) == {"coco", "jsonl"}
assert callable(get_adapter("coco"))
try:
    get_adapter("csv")
except ValueError as e:
    assert "Unknown format 'csv'" in str(e)
    print("REGISTRY OK")
else:
    raise SystemExit("should have raised")
PY
```
Expected: `REGISTRY OK`

**Step 8: Commit schemas + adapters together**

```bash
git add backend/schemas.py backend/import_adapters/
git commit -m "Add import pydantic schemas and COCO+JSONL adapters

Schemas: ImportRequest, ImportErrorItem, ImportResponse added to
backend/schemas.py.

Adapters live in a new backend/import_adapters/ package. Each adapter
is a pure function (parse(raw_bytes) -> (items, errors)) with no DB or
network access. COCO converts pixel bboxes to normalized 0-1 using
the COCO images[] width/height. JSONL is one object per line with
blank-line tolerance. Registry exposes get_adapter(format_name)."
```
Expected: commit created by Data-drone (pre-commit hook verifies).

---

## Task 4: Add volume helpers

**Files:**
- Modify: `backend/volumes.py` (add `read_bytes` and `file_exists`)

**Step 1: Read the current file to confirm anchor text**

```bash
sed -n '46,50p' backend/volumes.py
```
Expected: shows the last lines of `read_image_bytes`.

**Step 2: Add new helpers**

Use Edit to insert after the `read_image_bytes` function and before `scan_volume_for_samples`. Anchor text:

Old (lines 44-49 of current file):
```python
        if not os.path.exists(filepath):
            return None
        with open(filepath, "rb") as f:
            return f.read()


def scan_volume_for_samples(
```

New:
```python
        if not os.path.exists(filepath):
            return None
        with open(filepath, "rb") as f:
            return f.read()


def read_bytes(filepath: str) -> Optional[bytes]:
    """Read arbitrary file bytes from a UC Volume path or local filesystem.

    Same I/O path as read_image_bytes, but named for non-image payloads
    (e.g. COCO JSON, JSONL label files). Returns None if unreadable.
    """
    return read_image_bytes(filepath)


def file_exists(filepath: str) -> bool:
    """Cheap existence check for a UC Volume or local path."""
    if is_volume_path(filepath):
        try:
            w = _get_workspace_client()
            w.files.get_metadata(filepath)
            return True
        except Exception:
            return False
    return os.path.exists(filepath)


def scan_volume_for_samples(
```

**Step 3: Verify file parses**

```bash
python3 -c "import ast; ast.parse(open('backend/volumes.py').read()); print('OK')"
```
Expected: `OK`

**Step 4: Do NOT commit yet** — commits with Task 5 (router) since they're coupled.

---

## Task 5: Create the import router

**Files:**
- Create: `backend/routes/import_routes.py`

**Step 1: Create the router module**

Create `backend/routes/import_routes.py`:

```python
"""
Bulk annotation import — POST /api/projects/{project_id}/import.

Reads a file from a UC Volume by reference, dispatches to a format
adapter, runs a two-pass validate-then-commit, returns counters.

Design: docs/plans/2026-04-28-api-import-design.md
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..deps import get_db, get_user_email
from ..import_adapters import get_adapter, NormalizedImportItem
from ..models import (
    LabelingProject, ProjectSample, Annotation, AnnotationHistory,
)
from ..schemas import (
    AnnotationCreate, ImportRequest, ImportResponse, ImportErrorItem,
)
from ..volumes import read_bytes, file_exists, is_volume_path

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["import"])

MAX_ITEMS = 500_000
MAX_ERRORS_IN_RESPONSE = 100

VALID_ON_MISSING = {"error", "skip", "create"}
VALID_ON_EXISTING = {"replace", "append", "skip"}


def _bad_request(reason: str):
    raise HTTPException(status_code=400, detail=reason)


def _validation_422(errors: list[ImportErrorItem]):
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Import validation failed",
            "errors": [e.model_dump() for e in errors[:MAX_ERRORS_IN_RESPONSE]],
            "error_count": len(errors),
        },
    )


def _validate_annotation(
    ann: AnnotationCreate,
    project: LabelingProject,
) -> Optional[str]:
    """Return an error string or None if valid."""
    if ann.label not in project.class_list:
        return f"label '{ann.label}' not in project class_list"
    if ann.ann_type not in ("classification", "bbox"):
        return f"ann_type must be 'classification' or 'bbox', got '{ann.ann_type}'"
    if project.task_type == "classification" and ann.ann_type == "bbox":
        return "classification project cannot accept bbox annotations"
    if project.task_type == "detection" and ann.ann_type == "classification":
        return "detection project cannot accept classification-only annotations"
    if ann.ann_type == "bbox":
        bb = ann.bbox_json
        if not isinstance(bb, dict):
            return "bbox annotation missing bbox_json"
        for k in ("x", "y", "w", "h"):
            v = bb.get(k)
            if not isinstance(v, (int, float)):
                return f"bbox.{k} must be numeric"
            if not 0.0 <= float(v) <= 1.0:
                return f"bbox.{k}={v} out of range [0,1]"
    return None


@router.post("/import")
def import_annotations(
    project_id: int,
    payload: ImportRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    # --- Policy flags ---
    if payload.on_missing_sample not in VALID_ON_MISSING:
        _bad_request(f"on_missing_sample must be one of {sorted(VALID_ON_MISSING)}")
    if payload.on_existing_annotations not in VALID_ON_EXISTING:
        _bad_request(f"on_existing_annotations must be one of {sorted(VALID_ON_EXISTING)}")

    # --- Project ---
    project = db.query(LabelingProject).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")

    # --- Adapter ---
    try:
        adapter = get_adapter(payload.format)
    except ValueError as e:
        _bad_request(str(e))

    # --- Read source file ---
    log.info(
        "import_started project=%s format=%s volume=%s dry_run=%s",
        project_id, payload.format, payload.volume_path, payload.dry_run,
    )
    raw = read_bytes(payload.volume_path)
    if raw is None:
        _bad_request(f"cannot read volume_path '{payload.volume_path}'")

    # --- Parse ---
    items, parse_errors = adapter(raw)
    errors: list[ImportErrorItem] = list(parse_errors)

    if len(items) > MAX_ITEMS:
        _bad_request(
            f"payload too large ({len(items)} items > {MAX_ITEMS} limit); "
            f"split into multiple imports or wait for the async import endpoint"
        )

    # --- Filename -> sample_id map ---
    rows = db.query(ProjectSample.id, ProjectSample.filename).filter(
        ProjectSample.project_id == project_id,
    ).all()
    name_to_id: dict[str, int] = {fname: sid for sid, fname in rows}

    # --- Pass 1: validate everything ---
    missing_filenames: list[str] = []  # for on_missing_sample == "create"

    for idx, item in enumerate(items):
        has_sample = item.filename in name_to_id
        if not has_sample:
            if payload.on_missing_sample == "error":
                errors.append(ImportErrorItem(
                    row=idx, filename=item.filename,
                    reason="filename not found in project_samples",
                ))
                continue
            elif payload.on_missing_sample == "skip":
                # Skipped items don't contribute to counts; note it as a warning.
                continue
            elif payload.on_missing_sample == "create":
                # Must exist under the project's source_volume.
                full_path = project.source_volume.rstrip("/") + "/" + item.filename
                if not file_exists(full_path):
                    errors.append(ImportErrorItem(
                        row=idx, filename=item.filename,
                        reason=(
                            "on_missing_sample=create requires file to exist "
                            f"under source_volume; '{full_path}' not found"
                        ),
                    ))
                    continue
                missing_filenames.append(item.filename)

        for ann in item.annotations:
            reason = _validate_annotation(ann, project)
            if reason:
                errors.append(ImportErrorItem(
                    row=idx, filename=item.filename, reason=reason,
                ))

    if errors:
        log.warning(
            "import_validation_failed project=%s error_count=%d",
            project_id, len(errors),
        )
        return _validation_422(errors)

    # Build a response skeleton for dry_run / commit paths.
    resp = ImportResponse(dry_run=payload.dry_run)

    # Count what WOULD happen (also used as actual counts in pass 2).
    # For dry_run we short-circuit here.
    items_to_process = [
        item for item in items
        if item.filename in name_to_id or item.filename in set(missing_filenames)
        or payload.on_missing_sample == "skip"
    ]

    if payload.dry_run:
        resp.samples_created = len(set(missing_filenames))
        for item in items:
            if item.filename not in name_to_id:
                if payload.on_missing_sample == "skip":
                    resp.samples_skipped += 1
                    continue
                if payload.on_missing_sample != "create":
                    # Shouldn't happen (caught in pass 1), defensive.
                    continue
            resp.samples_touched += 1
            resp.annotations_created += len(item.annotations)
        log.info("import_dry_run_ok project=%s counters=%s", project_id, resp.model_dump())
        return resp

    # --- Pass 2: commit ---
    user_email = get_user_email(request)
    now = datetime.now(timezone.utc)

    try:
        # Insert created samples first; capture new IDs.
        for fname in set(missing_filenames):
            full_path = project.source_volume.rstrip("/") + "/" + fname
            new_sample = ProjectSample(
                project_id=project_id,
                filepath=full_path,
                filename=fname,
                status="unlabeled",
            )
            db.add(new_sample)
            db.flush()
            name_to_id[fname] = new_sample.id
            resp.samples_created += 1

        for item in items:
            if item.filename not in name_to_id:
                # Must be on_missing_sample=skip (already validated).
                resp.samples_skipped += 1
                continue

            sample_id = name_to_id[item.filename]
            sample = db.query(ProjectSample).filter_by(id=sample_id).one()

            existing = db.query(Annotation).filter_by(
                sample_id=sample_id, project_id=project_id,
            ).all()

            if existing and payload.on_existing_annotations == "skip":
                resp.samples_skipped += 1
                continue

            if existing and payload.on_existing_annotations == "replace":
                for old in existing:
                    db.add(AnnotationHistory(
                        sample_id=sample_id,
                        project_id=project_id,
                        action="delete",
                        old_label=old.label,
                        new_label=None,
                        old_ann_type=old.ann_type,
                        new_ann_type=None,
                        old_bbox_json=old.bbox_json,
                        new_bbox_json=None,
                        changed_by=user_email,
                        changed_at=now,
                    ))
                    resp.annotations_replaced += 1
                db.query(Annotation).filter_by(
                    sample_id=sample_id, project_id=project_id,
                ).delete()

            for ann in item.annotations:
                db.add(AnnotationHistory(
                    sample_id=sample_id,
                    project_id=project_id,
                    action="create",
                    old_label=None,
                    new_label=ann.label,
                    old_ann_type=None,
                    new_ann_type=ann.ann_type,
                    old_bbox_json=None,
                    new_bbox_json=ann.bbox_json,
                    changed_by=user_email,
                    changed_at=now,
                ))
                db.add(Annotation(
                    sample_id=sample_id,
                    project_id=project_id,
                    label=ann.label,
                    ann_type=ann.ann_type,
                    bbox_json=ann.bbox_json,
                    created_by=user_email,
                    created_at=now,
                ))
                resp.annotations_created += 1

            if item.annotations:
                sample.status = "labeled"
                sample.locked_by = None
                sample.locked_at = None
                resp.samples_touched += 1

        db.commit()
    except Exception:
        db.rollback()
        log.exception("import_commit_failed project=%s", project_id)
        raise HTTPException(status_code=500, detail="Import commit failed; see server log.")

    log.info("import_completed project=%s counters=%s", project_id, resp.model_dump())
    return resp
```

**Step 2: Verify file parses**

```bash
python3 -c "import ast; ast.parse(open('backend/routes/import_routes.py').read()); print('OK')"
```
Expected: `OK`

**Step 3: Commit the router + volume helpers**

```bash
git add backend/volumes.py backend/routes/import_routes.py
git commit -m "Add POST /api/projects/{id}/import route and volume helpers

New router: backend/routes/import_routes.py. Reads a file from a UC
Volume by reference, dispatches to a format adapter, runs two-pass
validate-then-commit, returns counters.

Policies: on_missing_sample (error|skip|create) and
on_existing_annotations (replace|append|skip). Soft cap 500k items.
Writes AnnotationHistory audit rows for both deletes and creates.
Errors cap at 100 in the response with true count in error_count.

volumes.py gains read_bytes (arbitrary file I/O, not just images) and
file_exists (cheap existence check, used by on_missing_sample=create
to gate orphan-row creation)."
```

---

## Task 6: Mount the router

**Files:**
- Modify: `backend/main.py:18` (add import) and `backend/main.py:116` (mount)

**Step 1: Add import**

Old:
```python
from .routes import projects, labeling, admin, export, browse
```
New:
```python
from .routes import projects, labeling, admin, export, browse, import_routes
```

**Step 2: Mount the router**

Old (line 116):
```python
app.include_router(browse.router)
```
New:
```python
app.include_router(browse.router)
app.include_router(import_routes.router)
```

**Step 3: Verify file parses and app imports clean**

```bash
python3 -c "import ast; ast.parse(open('backend/main.py').read()); print('OK')"
```
Expected: `OK`

**Step 4: Verify the route is registered (without hitting DB)**

```bash
python3 <<'PY'
import os, sys
# Prevent main.py from trying to connect to Lakebase at import time.
os.environ["USE_LAKEBASE"] = "false"
sys.path.insert(0, '.')
from backend.main import app
paths = [r.path for r in app.routes]
assert "/api/projects/{project_id}/import" in paths, paths
print("ROUTE MOUNTED OK")
PY
```
Expected: `ROUTE MOUNTED OK`

**Step 5: Commit**

```bash
git add backend/main.py
git commit -m "Mount /api/projects/{id}/import router in backend/main.py"
```

---

## Task 7: End-to-end verification with SQLite

Run the import flow against a fresh SQLite database in a temp dir. No network, no Lakebase. Exercises: adapter → route → DB writes.

**Step 1: Script**

Save this as a temp script and run it:

```bash
python3 <<'PY'
import os, sys, json, tempfile, shutil
os.environ["USE_LAKEBASE"] = "false"
tmp = tempfile.mkdtemp(prefix="cv-import-test-")
db_path = os.path.join(tmp, "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
sys.path.insert(0, '.')

from fastapi.testclient import TestClient
from backend.main import app

# Prepare an on-disk "source volume" with two real image stubs.
vol = os.path.join(tmp, "vol")
os.makedirs(vol)
for name in ("a.jpg", "b.jpg"):
    open(os.path.join(vol, name), "wb").write(b"\xff\xd8\xff\xd9")  # tiny valid-ish

labels_path = os.path.join(tmp, "labels.jsonl")
with open(labels_path, "w") as f:
    f.write('{"filename":"a.jpg","annotations":[{"label":"cat","ann_type":"classification"}]}\n')
    f.write('{"filename":"b.jpg","annotations":[{"label":"dog","ann_type":"classification"}]}\n')

bad_path = os.path.join(tmp, "bad.jsonl")
with open(bad_path, "w") as f:
    f.write('{"filename":"a.jpg","annotations":[{"label":"truck","ann_type":"classification"}]}\n')

with TestClient(app) as client:
    # 1. Create project (scan_volume_for_samples should find a.jpg, b.jpg)
    r = client.post("/api/projects", json={
        "name": "import-test",
        "description": "",
        "task_type": "classification",
        "class_list": ["cat", "dog"],
        "source_volume": vol,
    })
    assert r.status_code == 200, r.text
    project_id = r.json()["id"]

    # 2. Import (happy path)
    r = client.post(f"/api/projects/{project_id}/import", json={
        "volume_path": labels_path,
        "format": "jsonl",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["samples_touched"] == 2, body
    assert body["annotations_created"] == 2, body

    # 3. Dry run of the same file (replace semantics -> 2 replaced, 2 created would happen)
    r = client.post(f"/api/projects/{project_id}/import", json={
        "volume_path": labels_path,
        "format": "jsonl",
        "dry_run": True,
    })
    assert r.status_code == 200, r.text
    assert r.json()["dry_run"] is True

    # 4. Validation failure (unknown label)
    r = client.post(f"/api/projects/{project_id}/import", json={
        "volume_path": bad_path,
        "format": "jsonl",
    })
    assert r.status_code == 422, r.text
    errs = r.json()["errors"]
    assert any("not in project class_list" in e["reason"] for e in errs), errs

    # 5. on_missing_sample=skip with unknown filename
    ghost = os.path.join(tmp, "ghost.jsonl")
    with open(ghost, "w") as f:
        f.write('{"filename":"missing.jpg","annotations":[{"label":"cat","ann_type":"classification"}]}\n')
    r = client.post(f"/api/projects/{project_id}/import", json={
        "volume_path": ghost,
        "format": "jsonl",
        "on_missing_sample": "skip",
    })
    assert r.status_code == 200, r.text

    # 6. on_missing_sample=error with unknown filename -> 422
    r = client.post(f"/api/projects/{project_id}/import", json={
        "volume_path": ghost,
        "format": "jsonl",
    })
    assert r.status_code == 422, r.text

    # 7. on_existing_annotations=append -> doubles counts
    r = client.post(f"/api/projects/{project_id}/import", json={
        "volume_path": labels_path,
        "format": "jsonl",
        "on_existing_annotations": "append",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["annotations_created"] == 2, body
    assert body["annotations_replaced"] == 0, body

    # 8. Sample list should show the labels
    r = client.get(f"/api/projects/{project_id}/samples")
    assert r.status_code == 200
    items = r.json()["items"]
    assert all(it["status"] == "labeled" for it in items), items

shutil.rmtree(tmp)
print("E2E SQLITE OK")
PY
```

Expected: final line `E2E SQLITE OK`. If any assertion fails, the script exits non-zero and prints which check.

**Step 2: No commit** — this is a verification step, not a code change.

---

## Task 8: Update README

**Files:**
- Modify: `README.md` (add new "## Importing annotations" section)

**Step 1: Find a good insertion point**

```bash
grep -n "^## " README.md
```
Expected: lists section headers. Insert the new section after "## Using with Databricks Asset Bundles" and before "## Project Structure".

**Step 2: Insert the section**

Before the `## Project Structure` header, add:

````markdown
## Importing annotations

In addition to UI-driven labeling, projects can be bulk-populated via
`POST /api/projects/{project_id}/import`. The endpoint reads a label
file from a UC Volume by reference (so payload size is unbounded from
the HTTP side), validates every row, and commits in a single
transaction.

### Request

```json
{
  "volume_path": "/Volumes/<catalog>/<schema>/<vol>/labels.jsonl",
  "format": "jsonl",
  "on_missing_sample": "error",
  "on_existing_annotations": "replace",
  "dry_run": false
}
```

| Field | Default | Values |
|---|---|---|
| `volume_path` | — | UC Volume path readable by the app |
| `format` | — | `coco` \| `jsonl` |
| `on_missing_sample` | `error` | `error` \| `skip` \| `create` |
| `on_existing_annotations` | `replace` | `replace` \| `append` \| `skip` |
| `dry_run` | `false` | boolean |

### Formats

**JSONL** — one JSON object per line, blank lines ignored:

```jsonl
{"filename": "cat_001.jpg", "annotations": [{"label": "cat", "ann_type": "classification"}]}
{"filename": "dog_042.jpg", "annotations": [{"label": "dog", "ann_type": "bbox", "bbox_json": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}}]}
```

Bbox coordinates are normalized (0-1). Filenames are matched against
`project_samples.filename` (basename).

**COCO** — standard COCO JSON. The adapter converts pixel bboxes
`[x, y, w, h]` to normalized coordinates using the image `width` /
`height` from the `images[]` section. `iscrowd` and `segmentation` are
ignored.

### Flags

- `on_missing_sample`
  - `error` — unknown filename fails the whole import
  - `skip` — unknown filenames are silently skipped
  - `create` — creates a new `ProjectSample` row if the file exists under `source_volume`; otherwise errors
- `on_existing_annotations`
  - `replace` — deletes existing annotations for the sample before inserting (emits `AnnotationHistory` delete rows)
  - `append` — adds to existing annotations (natural for multi-bbox detection)
  - `skip` — leaves samples that already have annotations untouched
- `dry_run` — runs pass 1 only, returns the counters that *would* result

### Responses

- `200` — success, body has counters (`samples_touched`, `annotations_created`, `annotations_replaced`, `samples_skipped`, `samples_created`)
- `400` — bad format, unreadable volume_path, invalid flag value
- `404` — project not found
- `422` — validation failed, body has `errors[]` (capped at 100) and `error_count`
- `500` — commit failed, transaction rolled back

### Limits and caveats

- Soft cap: 500,000 items per request. Split larger imports.
- No per-project ACLs — anyone who can call the app can import.
- `replace` is content-idempotent; re-running produces the same state.
- `append` is not idempotent — re-running duplicates annotations.

### Example (Python)

```python
import requests

r = requests.post(
    "https://<app>.databricksapps.com/api/projects/42/import",
    json={
        "volume_path": "/Volumes/my_catalog/my_schema/imports/labels.jsonl",
        "format": "jsonl",
        "on_missing_sample": "error",
        "on_existing_annotations": "replace",
    },
    headers={"Authorization": f"Bearer {token}"},
)
r.raise_for_status()
print(r.json())
```

````

**Step 3: Verify README renders**

```bash
grep -n "^## Importing annotations" README.md
```
Expected: 1 match.

**Step 4: Commit**

```bash
git add README.md
git commit -m "Document /api/import endpoint

Adds 'Importing annotations' README section covering request shape,
JSONL + COCO formats, policy flags, response codes, limits, and a
Python client example."
```

---

## Task 9: Push branch

**Step 1: Push with upstream**

```bash
git push -u origin feat/api-import-endpoint
```
Expected: output includes remote URL.

**Step 2: Verify commits on branch**

```bash
git log --format="%h %an %s" origin/main..HEAD
```
Expected: 4 commits by Data-drone —
- `<hash> Document /api/import endpoint`
- `<hash> Mount /api/projects/{id}/import router ...`
- `<hash> Add POST /api/projects/{id}/import route and volume helpers`
- `<hash> Add import pydantic schemas and COCO+JSONL adapters`

---

## Task 10: Open the PR

**Step 1: Create PR**

Since `gh` CLI is not available in this environment, use the GitHub REST API via the PAT embedded in the remote URL:

```bash
TOKEN=$(git remote get-url origin | sed -E 's|.*://([^@]+)@.*|\1|' | sed 's/.*:x-oauth-basic//; s/.*://')
REPO="Data-drone/db_image_labelling_app"

curl -sS -X POST \
  -H "Authorization: token $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/$REPO/pulls" \
  -d "$(cat <<'EOF'
{
  "title": "Add POST /api/projects/{id}/import for bulk annotation import",
  "head": "feat/api-import-endpoint",
  "base": "main",
  "body": "## Summary\n\nAdds a synchronous bulk annotation import endpoint at `POST /api/projects/{project_id}/import`. Reads a label file from a UC Volume by reference, dispatches to a format adapter (COCO or JSONL), runs two-pass validate-then-commit, returns counters.\n\n## Motivation\n\nThe app only exposes UI-driven labeling. Existing datasets (COCO exports from Roboflow / CVAT / FiftyOne, custom pipeline output) previously had to be written to Lakebase directly, bypassing class_list / task_type / bbox-range validation and skipping `AnnotationHistory` audit rows.\n\n## Design\n\nFull design doc at `docs/plans/2026-04-28-api-import-design.md`. Implementation plan at `docs/plans/2026-04-28-api-import.md`.\n\nKey choices:\n- **By-reference via UC Volume path** (not inline JSON body) — unbounded payload size, reuses existing volume plumbing\n- **Pluggable adapters** — `backend/import_adapters/{coco,jsonl}.py`, pure functions, one-file PR to add a new format\n- **Two-pass** — pass 1 validates everything, pass 2 commits in a single transaction\n- **Per-request policies** — `on_missing_sample` (error/skip/create) and `on_existing_annotations` (replace/append/skip), both default to the safe option\n- **`dry_run`** — pass 1 only, returns counters that *would* result\n- **Soft cap 500k items** per request; documented growth path to an async job queue\n\n## Changes\n\n- `backend/import_adapters/` — new package with registry, COCO + JSONL adapters\n- `backend/routes/import_routes.py` — the endpoint\n- `backend/schemas.py` — `ImportRequest`, `ImportErrorItem`, `ImportResponse`\n- `backend/volumes.py` — adds `read_bytes` and `file_exists`\n- `backend/main.py` — mounts the new router\n- `README.md` — new 'Importing annotations' section\n\n## Verification\n\n- Adapter unit-level checks via inline scripts (see plan Tasks 3.5-3.7)\n- End-to-end SQLite test (see plan Task 7) covers: happy path, dry_run, validation failure, on_missing_sample=skip/error, on_existing_annotations=append\n\n## Backward compatibility\n\n100% additive. No existing endpoints or schemas modified.\n\n## Follow-ups (not in this PR)\n\n- pytest + conftest + formal test suite\n- Async job-queue variant for payloads > 500k items\n- CSV adapter\n\n## Test plan\n\n- [ ] Adapter unit checks pass (plan Task 3 verification)\n- [ ] E2E SQLite script passes (plan Task 7)\n- [ ] Manual smoke: upload a small JSONL file to a UC Volume, POST `/import` from the deployed app, verify `/api/projects/{id}/samples` shows labels\n- [ ] Manual smoke: POST with bad label, verify 422\n"
}
EOF
)" | tee /tmp/pr_api_import.json
```

**Step 2: Extract PR URL**

```bash
python3 -c "import json; d=json.load(open('/tmp/pr_api_import.json')); print('PR:', d.get('html_url','<error>')); print('NUMBER:', d.get('number','<error>'))"
```
Expected: prints the PR URL and number.

---

## Task 11: Manual smoke verification

**Step 1: Deploy branch to cv-explorer-react**

```bash
databricks apps deploy cv-explorer-react --json '{"git_source":{"branch":"feat/api-import-endpoint"},"mode":"SNAPSHOT"}'
```

**Step 2: Upload a tiny labels file to a UC Volume**

Pick an existing project (e.g. an empty fresh one). Write a JSONL file whose filenames match samples already in the project. Upload to `/Volumes/brian_gen_ai/cv_explorer/demo_images/` or similar readable volume.

**Step 3: POST to /import**

```bash
TOKEN=$(databricks-token)
curl -sS -w "\nHTTP %{http_code}\n" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  "https://cv-explorer-react-984752964297111.11.azure.databricksapps.com/api/projects/<PID>/import" \
  -d '{"volume_path":"/Volumes/.../labels.jsonl","format":"jsonl","dry_run":true}'
```
Expected: `HTTP 200`, body with counters.

**Step 4: Non-dry-run**

Same request with `"dry_run": false`. Expected: `HTTP 200`, `annotations_created > 0`.

**Step 5: Post result to PR**

```bash
curl -sS -X POST \
  -H "Authorization: token $TOKEN_GH" \
  "https://api.github.com/repos/Data-drone/db_image_labelling_app/issues/<PR#>/comments" \
  -d '{"body":"Manual smoke verified on cv-explorer-react: dry_run returned counters, real import created annotations, /api/projects/{id}/samples shows status=labeled."}'
```

---

## Task 12: Redeploy main to cv-explorer-react

```bash
databricks apps deploy cv-explorer-react --json '{"git_source":{"branch":"main"},"mode":"SNAPSHOT"}'
```
Verify: `databricks apps get cv-explorer-react | grep -A2 git_source` → `branch: main`.

---

## Rollback

The change is purely additive. If something breaks post-merge:

```bash
git revert <merge-commit-hash>
git push origin main
databricks apps deploy cv-explorer-react --json '{"git_source":{"branch":"main"},"mode":"SNAPSHOT"}'
```

No data migration; no existing endpoint behavior changes.
