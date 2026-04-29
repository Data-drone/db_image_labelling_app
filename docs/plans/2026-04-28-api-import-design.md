# `/api/import` — Design

**Status:** approved brainstorm, ready for implementation plan
**Author:** brian.law@databricks.com (Data-drone)
**Date:** 2026-04-28

## Problem

The app exposes UI-driven labeling only. To load existing datasets (3 near-term, and an open-ended set of pipeline-produced datasets longer term), callers must today write to Lakebase directly, bypassing the app's validation (class-list membership, ann_type ↔ task_type, bbox range, AnnotationHistory audit log).

Goal: an upstream REST endpoint that makes importing first-class — format-agnostic at the core, reusable by pipelines, notebooks, and future UI tooling.

## Non-goals (v1)

- Async job queue / progress polling (future PR — see "Growth path")
- Per-project ACLs (whole app has no ACLs yet)
- CSV format (adapter can be added in one file later)
- UI surface for upload (separate PR)
- Idempotency keys (documented caveat instead)

## API shape

```
POST /api/projects/{project_id}/import
Content-Type: application/json
```

**Request body**

```json
{
  "volume_path": "/Volumes/brian_gen_ai/cv_explorer/imports/coco_labels.json",
  "format": "coco",
  "on_missing_sample": "error",
  "on_existing_annotations": "replace",
  "dry_run": false
}
```

| Field | Required | Default | Values |
|---|---|---|---|
| `volume_path` | yes | — | Absolute UC Volume path readable by the app's service principal |
| `format` | yes | — | `"coco"` \| `"jsonl"` |
| `on_missing_sample` | no | `"error"` | `"error"` \| `"skip"` \| `"create"` |
| `on_existing_annotations` | no | `"replace"` | `"replace"` \| `"append"` \| `"skip"` |
| `dry_run` | no | `false` | boolean |

`"create"` requires the referenced image file actually exists under `project.source_volume`, otherwise that row becomes a validation error (no orphan sample rows).

**Response (success, 200)**

```json
{
  "dry_run": false,
  "samples_touched": 3000,
  "annotations_created": 10000,
  "annotations_replaced": 450,
  "annotations_skipped": 0,
  "samples_skipped": 12,
  "samples_created": 0,
  "warnings": []
}
```

**Response (validation failure, 422)**

```json
{
  "detail": "Import validation failed",
  "errors": [
    {"row": 7423, "filename": "img_042.jpg", "reason": "label 'truck' not in project class_list"},
    {"row": 8110, "filename": "img_099.jpg", "reason": "bbox.x=1.3 out of range [0,1]"}
  ],
  "error_count": 14
}
```

Errors capped at first 100 in the response; `error_count` is the true total.

**Other status codes**

- `400` — bad volume_path, unreadable file, unknown format
- `404` — project_id not found
- `500` — unexpected DB error, pass 2 rollback

## Format adapters

Both produce a common internal shape:

```python
class NormalizedImportItem:
    filename: str                       # relative or basename; matched against project_samples.filename
    annotations: list[AnnotationCreate] # reuses existing pydantic model
```

Signature per adapter: `parse(raw_bytes: bytes) -> (list[NormalizedImportItem], list[ImportError])`. Pure function, no DB or network.

### JSONL

One JSON object per line. Blank lines ignored. Lines that fail `json.loads` become errors.

```jsonl
{"filename": "cat_001.jpg", "annotations": [{"label": "cat", "ann_type": "classification"}]}
{"filename": "dog_042.jpg", "annotations": [{"label": "dog", "ann_type": "bbox", "bbox_json": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}}]}
```

### COCO

Standard COCO JSON. Adapter:

1. Build `image_id → (filename, width, height)` map from `images[]`
2. Build `category_id → label_name` map from `categories[]`
3. For each row in `annotations[]`:
   - Look up filename via `image_id`; missing id → error
   - Look up label via `category_id`; missing id → error
   - Convert absolute pixel bbox `[x, y, w, h]` to normalized `{x/W, y/H, w/W, h/H}`
   - No `bbox` field → classification annotation
   - `bbox` field present → bbox annotation
4. Group annotations by filename into `NormalizedImportItem`s

Errors from COCO reference the source annotation's `id` in `row` so callers can grep their file.

### Registry

```python
# backend/import_adapters/__init__.py
from . import coco, jsonl
ADAPTERS = {"coco": coco.parse, "jsonl": jsonl.parse}
```

Adding a format later is a one-file PR.

## Two-pass execution

### Pass 1 — Validate (always)

1. Read bytes from UC Volume (`volumes.read_bytes(volume_path)`). Missing / unreadable → 400.
2. Dispatch to adapter → `(items, parse_errors)`.
3. Load project + `class_list` + `task_type` (404 if missing).
4. Build `filename → sample_id` map from `project_samples` (single query, project-scoped).
5. For each item:
   - Filename in map? If not, apply `on_missing_sample` policy (error / skip / create-gate).
   - Each annotation's `label` ∈ project `class_list`.
   - Each annotation's `ann_type` ∈ `{"classification", "bbox"}`.
   - `ann_type == "bbox"` → `bbox_json` present, all four fields ∈ [0, 1].
   - `ann_type` compatible with `project.task_type` (classification project rejects bbox rows, detection project rejects classification-only rows).
6. For `on_missing_sample == "create"`: verify filename exists under `project.source_volume` via a lightweight `volumes.file_exists()` check; missing → error.
7. Collect all errors. Cap the returned list at 100; keep true count.

If any errors → return 422 immediately. If dry_run → return the counters that *would* result and exit.

### Pass 2 — Commit

Single SQLAlchemy transaction (one `db.begin()`):

1. For `on_missing_sample == "create"`: insert new `ProjectSample` rows (status=`"unlabeled"`), flush to get IDs, extend the filename → sample_id map.
2. For each sample touched, per `on_existing_annotations`:
   - `"replace"`: delete existing `Annotation` rows, emit `AnnotationHistory(action="delete")` for each
   - `"skip"`: if existing annotations, skip the whole sample (increment `samples_skipped`)
   - `"append"`: no pre-delete
3. Insert new `Annotation` rows; emit `AnnotationHistory(action="create")` with `changed_by = get_user_email(request)`.
4. For each sample that received ≥1 annotation, `status = "labeled"`, clear `locked_by` / `locked_at`.
5. Commit. Any exception → rollback, 500 with a generic message (details in server log).

Counters are accumulated during pass 2 (or pass 1 when dry_run).

## Auth, limits, observability

**Auth.** Reuse `get_user_email(request)` from `backend/deps.py` — same pattern as every other endpoint. All `AnnotationHistory` rows record the importing user.

**Authorization.** v1: anyone who can call the app can import. Matches the app's current trust model (no per-project ACLs exist). Documented as a README caveat.

**Size limit.** Soft cap: reject when parsed `items` count > 500,000. Message: *"payload too large — split into multiple imports or wait for the async import endpoint."* Prevents memory blowup during the single-transaction pass 2.

**Logging.** One INFO at start (`import_started project=X format=Y volume=...`), one at end (`import_completed project=X counters=...`). Errors at WARNING with `error_count`. No PII beyond the user email already in history rows.

**Idempotency.** None in v1. `replace` is effectively content-idempotent (re-running produces the same end state). `append` is not. Documented in README. A future `Idempotency-Key` header can deduplicate retries.

## Files changed

**New:**
- `backend/routes/import_routes.py` — the endpoint (~220 lines)
- `backend/import_adapters/__init__.py` — registry
- `backend/import_adapters/jsonl.py` — ~50 lines
- `backend/import_adapters/coco.py` — ~80 lines

**Modified:**
- `backend/schemas.py` — `ImportRequest`, `ImportError`, `ImportResponse` (~30 lines)
- `backend/main.py` — mount the new router (1 line)
- `backend/volumes.py` — add `file_exists(path)` helper if not already present
- `README.md` — new "## Importing annotations" section

## Testing

The repo currently has no test framework. Two options, **decide at plan time**:

- **A.** Add `pytest` + `conftest.py` + adapter unit tests + route integration tests (mocked DB + mocked volume). Right thing long-term, but expands PR scope.
- **B.** Match the LAKEBASE_AUTO_PROVISION PR pattern: inline verification scripts in the plan steps, manual smoke against the live `cv-explorer-react` app with a seeded import file on a UC Volume. Ship fast, defer test framework to a separate "introduce pytest" PR.

Recommendation: **B** for this PR, then a follow-up PR introduces pytest and ports the adapter tests — keeps each PR small and reviewable.

## README section outline

1. Endpoint + request / response schemas (copy from this doc)
2. JSONL format example
3. COCO format example + pixel→normalized note
4. Flag semantics (`on_missing_sample`, `on_existing_annotations`, `dry_run`)
5. Limits and caveats:
   - 500,000 item cap
   - No per-project ACL
   - `replace` is content-idempotent, `append` is not
6. Example `requests.post(...)` snippet for pipeline authors

## Growth path

If / when single-transaction imports hit scale limits:

1. Introduce an `import_jobs` table
2. Move pass 2 into a background task (`BackgroundTasks` or a Delta-backed queue)
3. POST returns 202 + `job_id`
4. Add `GET /api/import-jobs/{job_id}` for polling

The pass 1 / pass 2 split in v1 maps directly onto this — pass 2 is what moves to the background. No API break for callers who stay in the <500k bucket.

## Open questions (resolve at plan time)

1. **Test framework — A or B?** (see Testing)
2. **Filename matching — basename vs. full relative path?** Current `project_samples.filename` appears to store the basename (line in `models.py` stores `filename` as `String(512)`). Adapter output should match whatever the create_project scan produces. Verify at plan time against `scan_volume_for_samples`.
3. **COCO `iscrowd` + `segmentation`** — v1 ignores both. Document explicitly.
4. **Per-item warnings (not errors)** — e.g. "label converted from 'Dog' to 'dog' via case-insensitive match". Worth having? Simpler to reject and make the caller fix. Default: reject.
