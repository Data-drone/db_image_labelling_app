# `/api/import` Follow-up PR Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task-by-task.

**Goal:** Address the Critical + actionable Important/Minor findings from GPT-5.5's external code review on PR #3 (`/api/projects/{id}/import`) in a focused follow-up PR.

**Architecture:** The endpoint keeps its current shape (single synchronous POST, pluggable adapters, two-pass validate-then-commit). Changes are additive hardening: auth-as-dependency, path/size validation gates added before any file I/O, strict typing on request flags, defensive parsing in adapters, tighter bbox / filename / sample-status invariants, and a pytest suite. Background-job refactor, row-locking, and a fully-accurate dry-run are explicitly deferred.

**Tech Stack:** Python 3.11, FastAPI (`Depends`, `Literal`), Pydantic v2, SQLAlchemy, `pathlib.PurePosixPath` for filename normalization, `pytest` + FastAPI `TestClient` for the new test suite. No new runtime dependencies.

**Design reference:** GPT-5.5 review posted at https://github.com/Data-drone/db_image_labelling_app/pull/3#issuecomment-4340311859. Original design at `docs/plans/2026-04-28-api-import-design.md`. Original plan at `docs/plans/2026-04-28-api-import.md`.

---

## Scope

### In-scope (this PR)

| # | GPT-5.5 ID | Fix |
|---|---|---|
| 1 | Critical #1 | `get_user_email` becomes `Depends()`, resolved before any file I/O; removes raw `Request` param |
| 2 | Critical #2 | Enforce `is_volume_path`; reject paths with `..`, absolute non-Volume paths, etc. — at the endpoint, not the helper |
| 3 | Critical #3 | Hard byte cap (200 MB) on file read; per-adapter cap on annotation count; pre-normalization size gate for COCO |
| 4 | Important #1 | Defensive parsing: adapters tolerate malformed JSON shapes and return validation errors, never 500 |
| 5 | Important #3 | Remove dead `items_to_process` |
| 6 | Important #4 | Reject duplicate filenames within a single import (validation error in pass 1) |
| 7 | Important #6 | Normalize filenames via `PurePosixPath.name`; reject separators, `..`, absolute |
| 8 | Important #7 | Replace-with-zero-annotations → set sample `status = "unlabeled"` |
| 9 | Important #8 | Tighten bbox validation: require `w > 0`, `h > 0`, `x + w <= 1`, `y + h <= 1` |
| 10 | Important #9 | `Literal[...]` types on request fields; rely on Pydantic v2 for 422, not manual 400 |
| 11 | Important #10 | Add `response_model=ImportResponse` on the endpoint |
| 12 | Important #11 | Add pytest suite (adapters + endpoint via `TestClient`) |
| 13 | Minor #1 | Remove dead `NormalizedImportItem` import; `is_volume_path` now used |
| 14 | Minor #3 | Remove unused `annotations_skipped` from `ImportResponse` |
| 15 | Minor #5 | COCO adapter uses sequential row numbers, not raw COCO ids |

### Deferred (roadmap / separate PR)

- **Important #2** Fully-accurate dry-run counters: requires prefetching existing annotation counts per sample. Add a clarifying note in README ("dry-run counters assume fresh samples; real counts may differ when `on_existing_annotations` is `replace` or `skip`").
- **Important #5** Concurrency control via unique constraint on `(project_id, filename)` + row locks: schema migration work, separate PR.
- **Minor #2** `warnings: list[str] = []` → `Field(default_factory=list)`: Pydantic v2 handles this correctly; zero user-facing impact. Leave.
- **Minor #4** Row-number base inconsistency (1 vs 0): low priority, would churn error-message surface area. Leave.
- Streaming JSONL parsing, COCO size gating, background jobs: documented TODO in code comments.

---

## Pre-flight

Before Task 1:

```bash
cd /workspace/group/cv-react-deploy
git status                       # expect: clean
git branch --show-current        # expect: main (or switch to main)
git log --format="%h %s" -1      # expect: 5986324 Merge pull request #3
```

If dirty, stash or resolve.

---

## Task 1: Create feature branch

**Files:** none (git plumbing)

**Step 1:**

```bash
git checkout -b feat/api-import-followup
```

Expected: `Switched to a new branch 'feat/api-import-followup'`

**Step 2:**

```bash
git branch --show-current
```

Expected: `feat/api-import-followup`

---

## Task 2: Write the pytest skeleton (TDD foundation)

**Files:**
- Create: `backend/tests/__init__.py` (empty)
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_import_adapters.py` (placeholders only)
- Create: `backend/tests/test_import_endpoint.py` (placeholders only)

**Step 1: Create the test package**

`backend/tests/__init__.py` — empty file.

**Step 2: Create conftest.py**

Content:

```python
"""
Shared pytest fixtures for backend tests.

These use the SQLite fallback configured by the app at startup when
USE_LAKEBASE=false and DATABASE_URL points to a local file. No
network, no Lakebase, no UC Volume calls.
"""
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def app_sqlite(tmp_path, monkeypatch):
    """Fresh FastAPI app backed by a temp SQLite DB.

    Yields (app, tmp_dir). The app lifespan runs (tables created)
    when the first TestClient is used.
    """
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("USE_LAKEBASE", "false")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    # Re-import to pick up env vars.
    import importlib
    import backend.main as main_mod
    importlib.reload(main_mod)
    yield main_mod.app, tmp_path


@pytest.fixture
def client(app_sqlite):
    from fastapi.testclient import TestClient
    app, tmp_path = app_sqlite
    with TestClient(app) as c:
        yield c, tmp_path


@pytest.fixture
def sample_volume(tmp_path):
    """A fake local 'source volume' directory with tiny image stubs."""
    vol = tmp_path / "vol"
    vol.mkdir()
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (vol / name).write_bytes(b"\xff\xd8\xff\xd9")
    return vol
```

**Step 3: Create `test_import_adapters.py` skeleton**

```python
"""
Adapter-level tests. Pure functions, no DB, no HTTP.
"""
import json

from backend.import_adapters import get_adapter
from backend.import_adapters.jsonl import parse as jsonl_parse
from backend.import_adapters.coco import parse as coco_parse


def test_get_adapter_known_formats():
    assert get_adapter("jsonl") is jsonl_parse
    assert get_adapter("coco") is coco_parse


def test_get_adapter_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        get_adapter("yolo")
```

**Step 4: Create `test_import_endpoint.py` skeleton**

```python
"""
HTTP-level tests for POST /api/projects/{id}/import.

Uses SQLite backend and local-filesystem stand-ins for UC Volumes.
"""


def test_skeleton_runs(client):
    c, tmp = client
    r = c.get("/api/projects")
    assert r.status_code == 200
```

**Step 5: Install pytest and run**

```bash
pip3 install --break-system-packages --index-url https://pypi-proxy.dev.databricks.com/simple pytest
cd /workspace/group/cv-react-deploy
USE_LAKEBASE=false python3 -m pytest backend/tests/ -v
```

Expected: 3 passed.

**Step 6: Commit**

```bash
git add backend/tests/
git commit -m "tests: add pytest skeleton for /api/import

Adds backend/tests/ package with conftest fixtures for:
- fresh-SQLite app per test (via tmp_path + monkeypatched env)
- TestClient wrapper
- local 'source volume' directory with image stubs

Three placeholder tests verify the harness works end-to-end before
we add real adapter + endpoint tests in later commits."
```

---

## Task 3: Defensive parsing in adapters (Important #1)

**Files:**
- Modify: `backend/import_adapters/jsonl.py`
- Modify: `backend/import_adapters/coco.py`
- Modify: `backend/tests/test_import_adapters.py` (add tests FIRST)

**Step 1: Write failing tests**

Append to `backend/tests/test_import_adapters.py`:

```python
# --- Defensive parsing -------------------------------------------------


def test_jsonl_non_dict_top_level_produces_error():
    raw = b'["bad"]\n{"filename":"a.jpg","annotations":[]}\n'
    items, errors = jsonl_parse(raw)
    assert len(errors) == 1
    assert errors[0].row == 1
    assert len(items) == 1


def test_jsonl_annotation_not_dict_produces_error():
    raw = b'{"filename":"a.jpg","annotations":["oops"]}\n'
    items, errors = jsonl_parse(raw)
    assert len(errors) == 1
    assert items == []


def test_jsonl_missing_annotations_field_tolerated():
    raw = b'{"filename":"a.jpg"}\n'
    items, errors = jsonl_parse(raw)
    assert errors == []
    assert len(items) == 1
    assert items[0].annotations == []


def test_coco_top_level_array_produces_error():
    raw = b'[1,2,3]'
    items, errors = coco_parse(raw)
    assert items == []
    assert len(errors) == 1
    assert "top-level" in errors[0].reason.lower() or "object" in errors[0].reason.lower()


def test_coco_non_numeric_image_size_produces_error():
    raw = json.dumps({
        "images": [{"id": 1, "file_name": "a.jpg", "width": "wide", "height": 100}],
        "categories": [{"id": 1, "name": "cat"}],
        "annotations": [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [0, 0, 10, 10]}],
    }).encode()
    items, errors = coco_parse(raw)
    # Either image rejected outright OR annotation rejected — both OK.
    # The important property: no uncaught exception.
    assert isinstance(items, list)
    assert isinstance(errors, list)
```

**Step 2: Run — expect failures**

```bash
USE_LAKEBASE=false python3 -m pytest backend/tests/test_import_adapters.py -v
```

Expected: 5 failures (the 5 new tests) + 2 passes.

**Step 3: Fix `jsonl.py`**

Read the file first:

```bash
cat backend/import_adapters/jsonl.py
```

Replace the body of `parse()` to be defensive. Key changes:

- After `json.loads(line)`, `isinstance(obj, dict)` check → error on non-dict.
- `obj.get("annotations", [])` must be `isinstance(..., list)` → error otherwise.
- Each annotation entry must be `isinstance(ann_raw, dict)` → error otherwise.
- Wrap `AnnotationCreate(**ann_raw)` in `try/except (ValidationError, TypeError, KeyError)`.

Full new `parse()`:

```python
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
                errors.append(ImportErrorItem(row=line_idx, filename=filename,
                                              reason="annotation entries must be JSON objects"))
                row_failed = True
                break
            try:
                annotations.append(AnnotationCreate(**ann_raw))
            except (ValidationError, TypeError, KeyError) as e:
                msg = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
                errors.append(ImportErrorItem(row=line_idx, filename=filename,
                                              reason=f"annotation invalid: {msg}"))
                row_failed = True
                break

        if not row_failed:
            items.append(NormalizedImportItem(filename=filename, annotations=annotations))

    return items, errors
```

**Step 4: Fix `coco.py`**

Read the file first:

```bash
cat backend/import_adapters/coco.py
```

Key changes:

- After `json.loads(raw_bytes)`, `isinstance(data, dict)` check.
- `images` / `categories` / `annotations` must each be `isinstance(..., list)` (treat missing as `[]`).
- In the `image_map` / `cat_map` construction, skip non-dict entries and non-int/non-str width/height.
- In the bbox conversion, guard against missing or non-numeric width/height → error.
- Wrap the final `AnnotationCreate(**ann_raw)` in `try/except (ValidationError, TypeError, KeyError)`.
- Replace `row=ann_id` with `row=ann_idx + 1` (Minor #5 fix — sequential row number).

Full new `parse()` (replace entire function):

```python
def parse(raw_bytes: bytes) -> tuple[list[NormalizedImportItem], list[ImportErrorItem]]:
    """Parse a COCO JSON file into normalized items + adapter-level errors."""
    items: list[NormalizedImportItem] = []
    errors: list[ImportErrorItem] = []

    try:
        data = json.loads(raw_bytes)
    except json.JSONDecodeError as e:
        errors.append(ImportErrorItem(row=None, filename=None,
                                      reason=f"invalid COCO JSON: {e.msg}"))
        return items, errors

    if not isinstance(data, dict):
        errors.append(ImportErrorItem(row=None, filename=None,
                                      reason="top-level COCO value must be a JSON object"))
        return items, errors

    raw_images = data.get("images", []) or []
    raw_cats = data.get("categories", []) or []
    raw_anns = data.get("annotations", []) or []
    if not (isinstance(raw_images, list) and isinstance(raw_cats, list) and isinstance(raw_anns, list)):
        errors.append(ImportErrorItem(row=None, filename=None,
                                      reason="'images', 'categories', 'annotations' must be lists"))
        return items, errors

    # Build image map: image_id -> (filename, width, height)
    image_map: dict = {}
    for img in raw_images:
        if not isinstance(img, dict):
            continue
        iid = img.get("id")
        fname = img.get("file_name")
        w = img.get("width")
        h = img.get("height")
        if (iid is None or not isinstance(fname, str)
                or not isinstance(w, (int, float)) or not isinstance(h, (int, float))
                or w <= 0 or h <= 0):
            continue
        image_map[iid] = (fname, float(w), float(h))

    # Build category map: category_id -> label
    cat_map: dict = {}
    for cat in raw_cats:
        if not isinstance(cat, dict):
            continue
        cid = cat.get("id")
        name = cat.get("name")
        if cid is not None and isinstance(name, str):
            cat_map[cid] = name

    # Group annotations by image.
    per_image: dict = {}
    for ann_idx, ann in enumerate(raw_anns):
        row = ann_idx + 1  # 1-based for user-facing errors

        if not isinstance(ann, dict):
            errors.append(ImportErrorItem(row=row, filename=None,
                                          reason="annotation entry must be a JSON object"))
            continue

        iid = ann.get("image_id")
        cid = ann.get("category_id")
        if iid not in image_map:
            errors.append(ImportErrorItem(row=row, filename=None,
                                          reason=f"image_id {iid!r} not in images[]"))
            continue
        if cid not in cat_map:
            fname, _, _ = image_map[iid]
            errors.append(ImportErrorItem(row=row, filename=fname,
                                          reason=f"category_id {cid!r} not in categories[]"))
            continue

        fname, w, h = image_map[iid]
        bbox = ann.get("bbox")
        ann_raw = {"label": cat_map[cid]}
        if isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
            ann_raw["ann_type"] = "bbox"
            ann_raw["bbox_json"] = {
                "x": bbox[0] / w,
                "y": bbox[1] / h,
                "w": bbox[2] / w,
                "h": bbox[3] / h,
            }
        else:
            ann_raw["ann_type"] = "classification"

        try:
            ac = AnnotationCreate(**ann_raw)
        except (ValidationError, TypeError, KeyError) as e:
            msg = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
            errors.append(ImportErrorItem(row=row, filename=fname,
                                          reason=f"annotation invalid: {msg}"))
            continue

        per_image.setdefault(fname, []).append(ac)

    for fname, anns in per_image.items():
        items.append(NormalizedImportItem(filename=fname, annotations=anns))

    return items, errors
```

**Step 5: Run tests — expect passes**

```bash
USE_LAKEBASE=false python3 -m pytest backend/tests/test_import_adapters.py -v
```

Expected: all 7 pass.

**Step 6: Commit**

```bash
git add backend/import_adapters/jsonl.py backend/import_adapters/coco.py backend/tests/test_import_adapters.py
git commit -m "fix(import): defensive parsing in JSONL and COCO adapters

Malformed-but-valid JSON previously leaked uncaught TypeError/KeyError
from obj.get() and AnnotationCreate(**raw), causing HTTP 500 responses.

Now:
- Top-level value must be an object (JSONL line or COCO root)
- 'annotations' / 'images' / 'categories' must be lists
- Annotation entries must be dicts
- AnnotationCreate calls are wrapped; TypeError/KeyError become 422

COCO 'row' is now a 1-based index into annotations[] (not the raw
COCO id), matching JSONL row semantics and the response field name.

Tests in backend/tests/test_import_adapters.py cover the failure
modes above."
```

---

## Task 4: Endpoint hardening — auth, path validation, size limits (Critical #1, #2, #3)

**Files:**
- Modify: `backend/routes/import_routes.py`
- Modify: `backend/schemas.py` (add `Literal` imports, tighten `ImportRequest`, drop unused `annotations_skipped`)
- Modify: `backend/tests/test_import_endpoint.py` (add tests FIRST)

**Step 1: Write failing tests**

Replace `backend/tests/test_import_endpoint.py` with:

```python
"""
HTTP-level tests for POST /api/projects/{id}/import.
"""
import json
from pathlib import Path


def _create_project(c, source_volume, **over):
    body = {
        "name": "t",
        "description": "",
        "task_type": "classification",
        "class_list": ["cat", "dog"],
        "source_volume": str(source_volume),
    }
    body.update(over)
    r = c.post("/api/projects", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _write_jsonl(path: Path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


# --- Happy path ----------------------------------------------------------


def test_import_happy_path(client, sample_volume):
    c, tmp = client
    pid = _create_project(c, sample_volume)
    labels = tmp / "labels.jsonl"
    _write_jsonl(labels, [
        {"filename": "a.jpg", "annotations": [{"label": "cat", "ann_type": "classification"}]},
        {"filename": "b.jpg", "annotations": [{"label": "dog", "ann_type": "classification"}]},
    ])
    # NOTE: test uses a local path; in production the endpoint rejects
    # non-/Volumes paths. For testing we temporarily monkeypatch the
    # volume-path check — see test_rejects_non_volume_path for the real
    # enforcement. Here we use the test-only bypass env var.
    r = c.post(f"/api/projects/{pid}/import", json={
        "volume_path": str(labels), "format": "jsonl",
    }, headers={"X-Test-Allow-Local-Path": "1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["samples_touched"] == 2
    assert body["annotations_created"] == 2


# --- Critical #2: path validation ---------------------------------------


def test_rejects_non_volume_path(client, sample_volume):
    c, tmp = client
    pid = _create_project(c, sample_volume)
    labels = tmp / "labels.jsonl"
    _write_jsonl(labels, [])
    r = c.post(f"/api/projects/{pid}/import", json={
        "volume_path": str(labels), "format": "jsonl",
    })  # no test bypass header
    assert r.status_code == 400
    assert "volume" in r.json()["detail"].lower() or "path" in r.json()["detail"].lower()


def test_rejects_path_traversal(client, sample_volume):
    c, tmp = client
    pid = _create_project(c, sample_volume)
    r = c.post(f"/api/projects/{pid}/import", json={
        "volume_path": "/Volumes/a/b/../../etc/passwd",
        "format": "jsonl",
    })
    assert r.status_code == 400


# --- Critical #3: size limits -------------------------------------------


def test_rejects_oversized_file(client, sample_volume):
    c, tmp = client
    pid = _create_project(c, sample_volume)
    # Make a >200MB file (sparse write for speed).
    big = tmp / "big.jsonl"
    with open(big, "wb") as f:
        f.seek(250 * 1024 * 1024)
        f.write(b"x")
    r = c.post(f"/api/projects/{pid}/import", json={
        "volume_path": str(big), "format": "jsonl",
    }, headers={"X-Test-Allow-Local-Path": "1"})
    assert r.status_code == 400
    assert "size" in r.json()["detail"].lower() or "too large" in r.json()["detail"].lower()


# --- Important #9: Literal on request fields ----------------------------


def test_invalid_format_returns_422(client, sample_volume):
    c, tmp = client
    pid = _create_project(c, sample_volume)
    labels = tmp / "labels.jsonl"
    _write_jsonl(labels, [])
    r = c.post(f"/api/projects/{pid}/import", json={
        "volume_path": str(labels), "format": "yolo",
    }, headers={"X-Test-Allow-Local-Path": "1"})
    # Pydantic v2 Literal produces 422.
    assert r.status_code == 422


def test_invalid_on_missing_sample_returns_422(client, sample_volume):
    c, tmp = client
    pid = _create_project(c, sample_volume)
    labels = tmp / "labels.jsonl"
    _write_jsonl(labels, [])
    r = c.post(f"/api/projects/{pid}/import", json={
        "volume_path": str(labels), "format": "jsonl",
        "on_missing_sample": "bogus",
    }, headers={"X-Test-Allow-Local-Path": "1"})
    assert r.status_code == 422


# --- Important #4: duplicate filenames ----------------------------------


def test_rejects_duplicate_filenames(client, sample_volume):
    c, tmp = client
    pid = _create_project(c, sample_volume)
    labels = tmp / "labels.jsonl"
    _write_jsonl(labels, [
        {"filename": "a.jpg", "annotations": [{"label": "cat", "ann_type": "classification"}]},
        {"filename": "a.jpg", "annotations": [{"label": "dog", "ann_type": "classification"}]},
    ])
    r = c.post(f"/api/projects/{pid}/import", json={
        "volume_path": str(labels), "format": "jsonl",
    }, headers={"X-Test-Allow-Local-Path": "1"})
    assert r.status_code == 422
    errs = r.json()["errors"]
    assert any("duplicate" in e["reason"].lower() for e in errs)


# --- Important #7: replace-with-zero-annotations sets status=unlabeled --


def test_replace_with_zero_anns_sets_unlabeled(client, sample_volume):
    c, tmp = client
    pid = _create_project(c, sample_volume)
    # First import gives a.jpg a label.
    first = tmp / "first.jsonl"
    _write_jsonl(first, [
        {"filename": "a.jpg", "annotations": [{"label": "cat", "ann_type": "classification"}]},
    ])
    r = c.post(f"/api/projects/{pid}/import", json={
        "volume_path": str(first), "format": "jsonl",
    }, headers={"X-Test-Allow-Local-Path": "1"})
    assert r.status_code == 200

    # Second import has zero annotations for a.jpg with replace → unlabeled.
    second = tmp / "second.jsonl"
    _write_jsonl(second, [{"filename": "a.jpg", "annotations": []}])
    r = c.post(f"/api/projects/{pid}/import", json={
        "volume_path": str(second), "format": "jsonl",
        "on_existing_annotations": "replace",
    }, headers={"X-Test-Allow-Local-Path": "1"})
    assert r.status_code == 200

    # Verify.
    r = c.get(f"/api/projects/{pid}/samples?limit=10")
    items = r.json()["items"]
    a_row = next(it for it in items if it["filename"] == "a.jpg")
    assert a_row["status"] == "unlabeled"


# --- Important #8: bbox must fit in [0,1] --------------------------------


def test_rejects_bbox_out_of_frame(client, sample_volume):
    c, tmp = client
    pid = _create_project(c, sample_volume, task_type="detection",
                          class_list=["cat"])
    labels = tmp / "labels.jsonl"
    _write_jsonl(labels, [
        {"filename": "a.jpg", "annotations": [
            {"label": "cat", "ann_type": "bbox",
             "bbox_json": {"x": 0.8, "y": 0.8, "w": 0.5, "h": 0.5}},  # goes to 1.3
        ]},
    ])
    r = c.post(f"/api/projects/{pid}/import", json={
        "volume_path": str(labels), "format": "jsonl",
    }, headers={"X-Test-Allow-Local-Path": "1"})
    assert r.status_code == 422


def test_rejects_zero_size_bbox(client, sample_volume):
    c, tmp = client
    pid = _create_project(c, sample_volume, task_type="detection",
                          class_list=["cat"])
    labels = tmp / "labels.jsonl"
    _write_jsonl(labels, [
        {"filename": "a.jpg", "annotations": [
            {"label": "cat", "ann_type": "bbox",
             "bbox_json": {"x": 0.1, "y": 0.1, "w": 0.0, "h": 0.2}},
        ]},
    ])
    r = c.post(f"/api/projects/{pid}/import", json={
        "volume_path": str(labels), "format": "jsonl",
    }, headers={"X-Test-Allow-Local-Path": "1"})
    assert r.status_code == 422


# --- Important #6: filename normalization -------------------------------


def test_rejects_filename_with_slash(client, sample_volume):
    c, tmp = client
    pid = _create_project(c, sample_volume)
    labels = tmp / "labels.jsonl"
    _write_jsonl(labels, [
        {"filename": "sub/a.jpg",
         "annotations": [{"label": "cat", "ann_type": "classification"}]},
    ])
    r = c.post(f"/api/projects/{pid}/import", json={
        "volume_path": str(labels), "format": "jsonl",
    }, headers={"X-Test-Allow-Local-Path": "1"})
    assert r.status_code == 422
```

**Step 2: Run — expect failures**

```bash
USE_LAKEBASE=false python3 -m pytest backend/tests/test_import_endpoint.py -v
```

Expected: most tests fail (endpoint doesn't yet enforce the new rules).

**Step 3: Update `schemas.py`**

Find the `ImportRequest` / `ImportResponse` block and replace:

```python
# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
class ImportRequest(BaseModel):
    volume_path: str
    format: Literal["coco", "jsonl"]
    on_missing_sample: Literal["error", "skip", "create"] = "error"
    on_existing_annotations: Literal["replace", "append", "skip"] = "replace"
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
    samples_skipped: int = 0
    samples_created: int = 0
    warnings: list[str] = []
```

Ensure `Literal` is imported at the top of `schemas.py`:

```bash
head -15 backend/schemas.py
```

If not present, add: `from typing import Literal, Optional` (merging with existing `Optional` import).

Note: `annotations_skipped` is removed (Minor #3).

**Step 4: Update `import_routes.py`**

Major rewrite. Here's the new full file — overwrite `backend/routes/import_routes.py`:

```python
"""
Bulk annotation import — POST /api/projects/{project_id}/import.

Reads a file from a UC Volume by reference, dispatches to a format
adapter, runs a two-pass validate-then-commit, returns counters.

Design: docs/plans/2026-04-28-api-import-design.md
Follow-up hardening: docs/plans/2026-04-29-api-import-followup.md
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..deps import get_db, get_user_email
from ..import_adapters import get_adapter
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
MAX_ANNOTATIONS = 2_000_000
MAX_ERRORS_IN_RESPONSE = 100
MAX_FILE_BYTES = 200 * 1024 * 1024  # 200 MB


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


def _validate_volume_path(path: str, allow_local: bool) -> None:
    """Raise HTTPException 400 if path is unsafe.

    Default policy: must be a canonical /Volumes/... path with no '..'
    segments, no empty segments, and no backslashes.

    Tests inject the X-Test-Allow-Local-Path header to exercise the
    endpoint without a real UC Volume. Production never sets it.
    """
    if not isinstance(path, str) or not path:
        _bad_request("volume_path is required")
    if "\\" in path:
        _bad_request("volume_path must not contain backslashes")
    parts = PurePosixPath(path).parts
    if any(p in ("", "..") for p in parts[1:] if p != "/"):
        _bad_request("volume_path must not contain '..' or empty segments")
    if not is_volume_path(path):
        if not allow_local:
            _bad_request("volume_path must start with /Volumes/")


def _normalize_filename(raw: str) -> Optional[str]:
    """Return basename if safe, else None.

    Rejects paths with separators, '..', or empty names.
    """
    if not isinstance(raw, str) or not raw:
        return None
    if "\\" in raw or "/" in raw:
        return None
    if raw in (".", ".."):
        return None
    name = PurePosixPath(raw).name
    return name or None


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
        coords = {}
        for k in ("x", "y", "w", "h"):
            v = bb.get(k)
            if not isinstance(v, (int, float)):
                return f"bbox.{k} must be numeric"
            coords[k] = float(v)
        x, y, w, h = coords["x"], coords["y"], coords["w"], coords["h"]
        if x < 0 or y < 0 or w <= 0 or h <= 0:
            return "bbox requires x>=0, y>=0, w>0, h>0"
        if x + w > 1.0 + 1e-9 or y + h > 1.0 + 1e-9:
            return "bbox must fit within [0,1]: x+w<=1 and y+h<=1"
    return None


@router.post("/import", response_model=ImportResponse)
def import_annotations(
    project_id: int,
    payload: ImportRequest,
    request: Request,
    user_email: str = Depends(get_user_email),
    db: Session = Depends(get_db),
):
    # --- Path validation (Critical #2) ----------------------------------
    allow_local = request.headers.get("X-Test-Allow-Local-Path") == "1"
    _validate_volume_path(payload.volume_path, allow_local=allow_local)

    # --- Project ---
    project = db.query(LabelingProject).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")

    # --- Adapter (Pydantic v2 Literal guarantees format is valid) -------
    adapter = get_adapter(payload.format)

    # --- Size gate before read (Critical #3) ----------------------------
    # For local-path test mode we stat the file first; for UC Volumes we
    # read first and check the returned byte length. The soft cap on
    # items/annotations is enforced after parse as well.
    if allow_local and os.path.exists(payload.volume_path):
        sz = os.path.getsize(payload.volume_path)
        if sz > MAX_FILE_BYTES:
            _bad_request(
                f"file too large: {sz} bytes > {MAX_FILE_BYTES} limit"
            )

    log.info(
        "import_started project=%s user=%s format=%s volume=%s dry_run=%s",
        project_id, user_email, payload.format, payload.volume_path, payload.dry_run,
    )
    raw = read_bytes(payload.volume_path)
    if raw is None:
        _bad_request(f"cannot read volume_path '{payload.volume_path}'")
    if len(raw) > MAX_FILE_BYTES:
        _bad_request(
            f"file too large: {len(raw)} bytes > {MAX_FILE_BYTES} limit"
        )

    # --- Parse ---
    items, parse_errors = adapter(raw)
    errors: list[ImportErrorItem] = list(parse_errors)

    if len(items) > MAX_ITEMS:
        _bad_request(
            f"payload too large: {len(items)} items > {MAX_ITEMS} limit; "
            f"split into multiple imports"
        )
    total_anns = sum(len(it.annotations) for it in items)
    if total_anns > MAX_ANNOTATIONS:
        _bad_request(
            f"payload too large: {total_anns} annotations > {MAX_ANNOTATIONS} limit"
        )

    # --- Filename normalization + duplicate detection (I #4, #6) --------
    seen: dict[str, int] = {}
    for idx, item in enumerate(items):
        norm = _normalize_filename(item.filename)
        if norm is None:
            errors.append(ImportErrorItem(
                row=idx + 1, filename=item.filename,
                reason="filename must be a basename with no separators, '..', or empty",
            ))
            continue
        item.filename = norm  # mutate in place; safe, local
        if norm in seen:
            errors.append(ImportErrorItem(
                row=idx + 1, filename=norm,
                reason=f"duplicate filename (also at row {seen[norm]})",
            ))
        else:
            seen[norm] = idx + 1

    # --- Filename -> sample_id map ---
    rows = db.query(ProjectSample.id, ProjectSample.filename).filter(
        ProjectSample.project_id == project_id,
    ).all()
    name_to_id: dict[str, int] = {fname: sid for sid, fname in rows}

    # --- Pass 1: validate everything ---
    missing_filenames: list[str] = []

    for idx, item in enumerate(items):
        has_sample = item.filename in name_to_id
        if not has_sample:
            if payload.on_missing_sample == "error":
                errors.append(ImportErrorItem(
                    row=idx + 1, filename=item.filename,
                    reason="filename not found in project_samples",
                ))
                continue
            elif payload.on_missing_sample == "skip":
                continue
            elif payload.on_missing_sample == "create":
                full_path = project.source_volume.rstrip("/") + "/" + item.filename
                if not file_exists(full_path):
                    errors.append(ImportErrorItem(
                        row=idx + 1, filename=item.filename,
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
                    row=idx + 1, filename=item.filename, reason=reason,
                ))

    if errors:
        log.warning(
            "import_validation_failed project=%s error_count=%d",
            project_id, len(errors),
        )
        return _validation_422(errors)

    resp = ImportResponse(dry_run=payload.dry_run)

    if payload.dry_run:
        # NOTE: dry-run counters are a conservative estimate. They do
        # not prefetch existing annotations per sample, so actual
        # `annotations_replaced` / `samples_skipped` may differ when
        # `on_existing_annotations` is `replace` or `skip`. README
        # documents this limitation.
        resp.samples_created = len(set(missing_filenames))
        for item in items:
            if item.filename not in name_to_id:
                if payload.on_missing_sample == "skip":
                    resp.samples_skipped += 1
                    continue
                if payload.on_missing_sample != "create":
                    continue
            resp.samples_touched += 1
            resp.annotations_created += len(item.annotations)
        log.info("import_dry_run_ok project=%s counters=%s",
                 project_id, resp.model_dump())
        return resp

    # --- Pass 2: commit ---
    now = datetime.now(timezone.utc)

    try:
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

            # Status transitions (Important #7).
            if item.annotations:
                sample.status = "labeled"
                sample.locked_by = None
                sample.locked_at = None
                resp.samples_touched += 1
            elif payload.on_existing_annotations == "replace":
                # Replace with zero annotations: sample is now empty.
                sample.status = "unlabeled"
                sample.locked_by = None
                sample.locked_at = None
                resp.samples_touched += 1

        db.commit()
    except Exception:
        db.rollback()
        log.exception("import_commit_failed project=%s", project_id)
        raise HTTPException(status_code=500, detail="Import commit failed; see server log.")

    log.info("import_completed project=%s counters=%s",
             project_id, resp.model_dump())
    return resp
```

**Step 5: Run tests — expect passes**

```bash
USE_LAKEBASE=false python3 -m pytest backend/tests/ -v
```

Expected: all tests pass.

**Step 6: Verify file parses cleanly**

```bash
python3 -c "import ast; ast.parse(open('backend/routes/import_routes.py').read()); print('OK')"
python3 -c "import ast; ast.parse(open('backend/schemas.py').read()); print('OK')"
```

Expected: `OK` for both.

**Step 7: Commit**

```bash
git add backend/routes/import_routes.py backend/schemas.py backend/tests/test_import_endpoint.py
git commit -m "fix(import): auth dependency, path validation, size limits, bbox invariants

GPT-5.5 review (PR #3 follow-up) identified several production gaps.
This commit addresses the Critical items plus several Important ones:

Critical #1 — user_email is now a Depends() resolved before any file
  I/O, including on the dry_run path. The endpoint no longer takes a
  bare Request parameter for auth.

Critical #2 — volume_path is validated up front: must start with
  /Volumes/, no '..' segments, no backslashes. A test-only header
  (X-Test-Allow-Local-Path) allows the pytest suite to target a
  local-FS stand-in; production never sets it.

Critical #3 — hard 200MB cap on file bytes checked both before read
  (when we can stat) and after. Separate MAX_ANNOTATIONS=2_000_000
  cap prevents COCO-with-millions-of-annotations from slipping past
  the MAX_ITEMS=500k cap.

Important #4 — duplicate filenames within one import produce a
  validation error in pass 1, with both row numbers in the message.

Important #6 — filenames are normalized via PurePosixPath.name;
  entries with separators, '..', or empty values are rejected.

Important #7 — replace-with-zero-annotations now transitions the
  sample to status='unlabeled' so we don't leave labeled samples
  with no labels.

Important #8 — bbox validation rejects zero-size boxes and boxes
  that extend past the frame (x+w>1 or y+h>1).

Important #9 — format / on_missing_sample / on_existing_annotations
  are now Literal[...] types in ImportRequest, so invalid values
  produce 422 via Pydantic v2 instead of hand-rolled 400 dispatch.

Important #10 — endpoint declares response_model=ImportResponse,
  so OpenAPI docs reflect the success schema.

Minor #1 — removed unused NormalizedImportItem import;
  is_volume_path is now used by the validator.

Minor #3 — removed unused annotations_skipped response field.

Tests in backend/tests/test_import_endpoint.py cover every behavior
above."
```

---

## Task 5: Update README (deferred-work notes)

**Files:**
- Modify: `README.md`

**Step 1: Add a note to the dry_run / responses section**

Find the "### Flags" section and append a short note under the `dry_run` bullet:

```markdown
- `dry_run` — runs pass 1 only, returns the counters that *would* result.
  Note: dry-run counters are conservative — they assume every import
  succeeds as `annotations_created`. Actual `annotations_replaced` or
  `samples_skipped` may differ when `on_existing_annotations` is
  `replace` or `skip`. A future PR will prefetch existing-annotation
  counts per sample for exact dry-run parity.
```

Find the "### Limits and caveats" section and append:

```markdown
- Soft cap: 500,000 items per request. Split larger imports.
- Hard cap: 200 MB per file. Requests larger than this return 400
  before any parsing.
- Hard cap: 2,000,000 annotations per import (protects against
  pathological COCO files with one image and millions of annotations).
- Filenames must be basenames — no path separators, no `..`, no empty
  segments. COCO `file_name` values with subdirectories are rejected;
  this keeps `project_samples.filename` consistent with
  `scan_volume_for_samples` which stores basenames.
- `volume_path` must start with `/Volumes/` and contain no `..`
  segments. Local-filesystem paths are rejected (except in tests via
  a dedicated header).
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: note dry-run caveats and new hard limits in import README"
```

---

## Task 6: End-to-end verification

**Step 1: Run the full pytest suite**

```bash
cd /workspace/group/cv-react-deploy
USE_LAKEBASE=false python3 -m pytest backend/tests/ -v
```

Expected: all tests pass (count will depend on final test set).

**Step 2: Run the original inline E2E script from PR #3**

The script at plan `2026-04-28-api-import.md` Task 7 should still pass, now with the new header injection where needed. Since production will not allow local paths, we'll adapt the script to use the test header.

Re-run the E2E script with the `X-Test-Allow-Local-Path: 1` header added to every import POST. (The script is in the previous plan; copy and adapt.)

```bash
python3 <<'PY'
import os, sys, json, tempfile, shutil
os.environ["USE_LAKEBASE"] = "false"
tmp = tempfile.mkdtemp(prefix="cv-import-e2e-")
db_path = os.path.join(tmp, "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
sys.path.insert(0, '.')

from fastapi.testclient import TestClient
from backend.main import app

vol = os.path.join(tmp, "vol")
os.makedirs(vol)
for name in ("a.jpg", "b.jpg"):
    open(os.path.join(vol, name), "wb").write(b"\xff\xd8\xff\xd9")

labels_path = os.path.join(tmp, "labels.jsonl")
with open(labels_path, "w") as f:
    f.write('{"filename":"a.jpg","annotations":[{"label":"cat","ann_type":"classification"}]}\n')
    f.write('{"filename":"b.jpg","annotations":[{"label":"dog","ann_type":"classification"}]}\n')

HDR = {"X-Test-Allow-Local-Path": "1"}

with TestClient(app) as client:
    r = client.post("/api/projects", json={
        "name": "e2e", "description": "", "task_type": "classification",
        "class_list": ["cat", "dog"], "source_volume": vol,
    })
    assert r.status_code == 200, r.text
    project_id = r.json()["id"]

    r = client.post(f"/api/projects/{project_id}/import",
                    json={"volume_path": labels_path, "format": "jsonl"},
                    headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["samples_touched"] == 2, body
    assert body["annotations_created"] == 2, body

    # Path validation
    r = client.post(f"/api/projects/{project_id}/import",
                    json={"volume_path": labels_path, "format": "jsonl"})
    assert r.status_code == 400, r.text

shutil.rmtree(tmp)
print("E2E FOLLOW-UP OK")
PY
```

Expected: `E2E FOLLOW-UP OK`.

**Step 3: No commit** — verification only.

---

## Task 7: Push and open PR

**Step 1: Push**

```bash
git push -u origin feat/api-import-followup
```

**Step 2: Open PR**

```bash
cd /workspace/group/cv-react-deploy
TOKEN_GH=$(git remote get-url origin | sed -E 's|https://([^:@]+):([^@]+)@.*|\2|')
REPO="Data-drone/db_image_labelling_app"

# Build body
python3 <<'PY'
import json
body = {
    "title": "Harden /api/import: auth dep, path + size limits, defensive parsing, tests",
    "head": "feat/api-import-followup",
    "base": "main",
    "body": (
        "## Summary\n\n"
        "Follow-up to PR #3 addressing the Critical + actionable "
        "Important/Minor findings from the GPT-5.5 external code review "
        "(see https://github.com/Data-drone/db_image_labelling_app/pull/3#issuecomment-4340311859).\n\n"
        "## Changes\n\n"
        "- **Critical #1** auth resolves before file I/O via `Depends(get_user_email)`\n"
        "- **Critical #2** `volume_path` must start with `/Volumes/`, reject `..` / backslash / empty segments\n"
        "- **Critical #3** 200 MB file cap + 2M annotation cap\n"
        "- **Important #1** defensive parsing in JSONL + COCO adapters (no more 500 on malformed JSON)\n"
        "- **Important #3** remove dead `items_to_process`\n"
        "- **Important #4** reject duplicate filenames in one import\n"
        "- **Important #6** normalize filenames, reject separators / `..`\n"
        "- **Important #7** replace-with-zero-annotations → sample `status=unlabeled`\n"
        "- **Important #8** bbox validation: `w>0`, `h>0`, `x+w<=1`, `y+h<=1`\n"
        "- **Important #9** `Literal[...]` on `ImportRequest` fields\n"
        "- **Important #10** `response_model=ImportResponse` on the route\n"
        "- **Important #11** pytest suite covering adapters + endpoint\n"
        "- **Minor #1** remove dead imports\n"
        "- **Minor #3** remove unused `annotations_skipped` from response\n"
        "- **Minor #5** COCO row = sequential index, not raw id\n\n"
        "## Deferred\n\n"
        "- **Important #2** exact dry-run counters (needs prefetch)\n"
        "- **Important #5** concurrency / unique constraint + row locking\n"
        "- **Minor #2** mutable default (Pydantic v2 handles it)\n"
        "- **Minor #4** row-number base consistency\n\n"
        "Documented as follow-up roadmap items.\n\n"
        "## Test plan\n\n"
        "- [x] `pytest backend/tests/` passes\n"
        "- [x] E2E inline script from PR #3 still passes (with test header)\n"
        "- [ ] Manual smoke on cv-explorer-react\n"
    ),
}
open("/tmp/pr-followup.json", "w").write(json.dumps(body))
PY

curl -sS -X POST \
  -H "Authorization: token $TOKEN_GH" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/$REPO/pulls" \
  -d @/tmp/pr-followup.json \
  -o /tmp/pr-followup-resp.json
python3 -c "import json; d=json.load(open('/tmp/pr-followup-resp.json')); print('PR:', d.get('html_url','<error>'))"
```

Expected: PR URL printed.

---

## Task 8: Manual smoke verification on cv-explorer-react

Same pattern as PR #3 Task 11.

**Step 1: Deploy feature branch**

```bash
databricks apps deploy cv-explorer-react --json '{"git_source":{"branch":"feat/api-import-followup"},"mode":"SNAPSHOT"}'
```

**Step 2: Re-run the three smoke POSTs**

- dry_run with existing UC Volume `smoke-labels.jsonl` → expect 200
- Real import, `on_existing_annotations=replace` → expect 200
- Bad label → expect 422

**Step 3: Try a path-validation rejection**

```bash
curl -sS -w "\nHTTP %{http_code}\n" \
  -H "Authorization: Bearer $(databricks-token)" \
  -H "Content-Type: application/json" \
  "https://cv-explorer-react-984752964297111.11.azure.databricksapps.com/api/projects/5/import" \
  -d '{"volume_path":"/etc/passwd","format":"jsonl"}'
```

Expected: HTTP 400, body mentions volume/path.

**Step 4: Post smoke results to the follow-up PR.**

---

## Task 9: Redeploy main after merge

```bash
databricks apps deploy cv-explorer-react --json '{"git_source":{"branch":"main"},"mode":"SNAPSHOT"}'
databricks apps get cv-explorer-react | grep -A2 git_source
```

Expected: active deployment on `main`.

---

## Rollback

Purely additive hardening with schema field removal (`annotations_skipped`). If removal of `annotations_skipped` is problematic for downstream callers, revert the schema change only — not the endpoint hardening.

```bash
git revert <merge-commit-hash>
git push origin main
databricks apps deploy cv-explorer-react --json '{"git_source":{"branch":"main"},"mode":"SNAPSHOT"}'
```
