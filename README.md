# CV Explorer — Image Labeling App

A React + FastAPI image annotation tool for **Databricks Apps**. Supports
classification, bounding-box detection, and segmentation with project
versioning, model-assisted labeling, similarity search, and dataset export.

Metadata lives in **Lakebase** (managed PostgreSQL). Images remain in Unity
Catalog Volumes; projects can discover them from a Volume or from `/Volumes/...`
paths stored in a Delta table. Lakehouse Sync is compatible but must be
configured separately in the workspace.

## Features

- **Three labeling modes**: classification, bounding-box detection, and segmentation masks
- **Volume or Delta-table sources**: scan a UC Volume or query an image-path column through a SQL warehouse
- **Model-assisted labeling**: synchronous or serverless Job pre-annotation with draft review
- **Similarity tools**: image embeddings, nearest neighbors, duplicate detection, clustering, and outlier queues
- **Keyboard-driven labeling**: number keys for class selection with visual flash feedback, arrow keys for navigation
- **Sample scrubber**: navigate forward/backward through images, revisit and re-label previous samples
- **Gallery view**: thumbnail grid with status filters (all/unlabeled/labeled/skipped)
- **Project versioning**: clone projects to create new versions for iterative labeling
- **Training datasets**: lightweight Hugging Face classification snapshots referencing Volume images; optional CSV/image copies and detection COCO exports
- **Lakebase integration**: App-resource OAuth with token refresh, dedicated schema isolation, and optional SDK auto-provisioning
- **Multi-user support**: user identity via Databricks SSO, per-user labeling stats

## Demo

### Project Creation
Browse UC Volumes, select a task type, define your class list, and create a labeling project.

![Project Creation](docs/media/project-creation.gif)

### Classification Labeling
Single-click labeling with keyboard shortcuts — press a number key to assign a class and auto-advance.

![Classification Labeling](docs/media/classification-labeling.gif)

### Detection Labeling
Draw bounding boxes on images and assign classes. Navigate between samples to review and re-label.

![Detection Labeling](docs/media/detection-labeling.gif)

## Architecture

```
Browser  ──>  React SPA (Vite)  ──>  FastAPI backend  ──>  Lakebase (PostgreSQL)
                                          │
                                          ├──>  UC Volumes (images)
                                            ├──>  Databricks SDK (serving, jobs, resources)
                                            └──>  SQL Statement API (Delta sources)
```

The FastAPI backend serves the React SPA and `/api/` endpoints. Bundle
deployments attach a Lakebase postgres App resource; SDK auto-provisioning
remains available when no App resource is attached. Both modes refresh OAuth
database credentials every 20 minutes.

## Pages

| Page | Route | Description |
|------|-------|-------------|
| **Browse** | `/` or `/browse` | Browse UC Volumes and preview Delta tables |
| **Projects** | `/projects` | List projects and versions |
| **Create Project** | `/projects/new` | Pick a Volume or Delta table, task type, and classes |
| **Project Dashboard** | `/projects/:id` | Stats, gallery grid, export, start labeling |
| **Labeling View** | `/projects/:id/label` | Classify, draw boxes, or paint segmentation masks |
| **Admin** | `/admin` | Read status; authorized users may run guarded mutations |

## Quick Start

### Deploy to Databricks Apps

**Recommended:** use the Databricks Apps deployment pipeline in this repo
(`databricks apps deploy --target dev`). It validates/uploads the bundle,
updates the pre-annotation Job and App resources, and starts the App.

1. Adjust the bundle variables for the target workspace, including both
   Volumes, Lakebase branch/database/endpoint, serving endpoints, warehouse,
   jobs, and App name.
2. Run `databricks apps deploy --target dev --profile <profile>`.
3. The App runs `python start.py`. The Lakebase postgres resource injects
   `PGHOST`, `PGUSER`, and `PGDATABASE`; `LAKEBASE_ENDPOINT` is bound from the
   same resource so the App can mint renewable OAuth credentials.

Alternatively, create an app manually from Git and use the root `app.yml`.
Attach resources named `lakebase-db`, `demo-volume`, `export-volume`,
`sql-warehouse`, `serving-endpoint`, `embedding-endpoint`,
`app-preannotate-job`, and `app-finetune-job` before starting it.

### App icon (Apps overview thumbnail)

The tile on the **Databricks Apps** overview is an **app thumbnail** on the workspace object, not a static file served by FastAPI.

1. **Store the image in this repo** as **`assets/databricks-app-thumbnail.jpg`** (or `.jpeg` / `.png` with that basename). Any reasonable resolution and modest file size is fine.
2. **Upload it once per app** (after the app exists in the workspace):

   ```bash
   python scripts/upload_app_thumbnail.py <your-app-name>
   ```

   Or with a custom path: `python scripts/upload_app_thumbnail.py <your-app-name> --image /path/to/icon.png`

   This wraps `databricks apps update-app-thumbnail` with the JSON shape `{"app_thumbnail":{"thumbnail":"<base64>"}}` required by the [Apps API](https://docs.databricks.com/api/workspace/apps/updateappthumbnail).

### App resources (Databricks Apps UI)

With bundle deploy, **resources are declared in YAML** and appear on the app’s Resources tab. The service principal is the app identity; permissions are set on each resource (e.g. **READ_VOLUME** on the demo volume, **CAN_QUERY** on the serving endpoint, **CAN_MANAGE_RUN** on the pre-annotate job).

Export to additional volumes still requires **WRITE_VOLUME** on those paths (add another `uc_securable` resource or grant the app SP in Unity Catalog).

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABRICKS_APP_PORT` | `8000` | Port for the FastAPI server |
| `DEMO_VOLUME_PATH` | _(from `valueFrom: demo-volume`)_ | UC volume path injected from the **demo-volume** App resource (`app.yml`). |
| `SERVING_ENDPOINT` | _(from `valueFrom: serving-endpoint`)_ | Serving endpoint name from the **serving-endpoint** resource. |
| `EMBEDDING_ENDPOINT` | _(from `valueFrom: embedding-endpoint`)_ | Image embedding endpoint from the **embedding-endpoint** resource. There is no workspace-specific code fallback. |
| `PRE_ANNOTATE_DATABRICKS_JOB_ID` | _(from `valueFrom: app-preannotate-job`)_ | Numeric job id from the **app-preannotate-job** resource (bundle-bound job). |
| `LAKEBASE_AUTO_PROVISION` | `false` (bundle app) | With a **postgres** App resource, use `false` and connect via injected `PG*` variables (no SDK project creation on app startup). |
| `LAKEBASE_PROJECT_ID` | `cv-explorer` | Still used by **async pre-annotate job** clusters (SDK Lakebase path on the job). |
| `LAKEBASE_ENDPOINT` | _(from `valueFrom: lakebase-db`)_ | Postgres endpoint resource path. **Not auto-injected** — must be bound in the bundle / `app.yml`. |
| `LAKEBASE_SCHEMA` | `cv_explorer_app` (bundle) | PostgreSQL schema used for CV Explorer tables. The code fallback is `public` when the variable is absent. |
| `LAKEBASE_MIGRATE_PUBLIC` | `false` | One-time safety gate. When `true` with a non-`public` schema, moves managed tables transactionally and verifies row counts. Startup otherwise refuses to hide existing `public` data. |
| `PGHOST`, `PGUSER`, `PGDATABASE`, `PGPORT`, `PGSSLMODE` | _(platform)_ | Injected when a Lakebase **postgres** resource is attached. Password is an OAuth token, not `PGPASSWORD`. |
| `ADMIN_EMAILS` | `__disabled__` (bundle) | Comma-separated Databricks IdP emails allowed to provision, reconnect, reset, or test Admin resources. The Apps proxy supplies `X-Forwarded-Email`; the operations still execute with the App service principal and its declared resource permissions. The non-email default disables all Admin mutations. |

## Using with Databricks Asset Bundles

When deploying this app as part of a larger Databricks Asset Bundle (DAB), the bundle typically owns the Lakebase project lifecycle (create / grant roles / enable Lakehouse Sync / destroy). To prevent the app from trying to create a duplicate project at startup, set:

```yaml
# app.yml
env:
  - name: LAKEBASE_AUTO_PROVISION
    value: "false"
  - name: LAKEBASE_PROJECT_ID
    value: "<project-id-created-by-your-bundle>"
```

With `LAKEBASE_AUTO_PROVISION=false`, the app:

1. Looks up the Lakebase project named by `LAKEBASE_PROJECT_ID`
2. Connects if it exists
3. Exits with a clear error if it does not (instead of creating one)

See the [Databricks Apps → Lakebase resources documentation](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/resources#lakebase) for how DAB declares Postgres project ownership and permissions.

To move an existing deployment from `public` to a dedicated schema, deploy
once with `LAKEBASE_SCHEMA=<schema>` and `LAKEBASE_MIGRATE_PUBLIC=true`.
Startup creates the schema, moves all managed tables in one transaction, and
compares row counts before and after. Remove the migration flag after the
successful deployment. If managed tables exist in both schemas, startup stops
instead of choosing one.

### Async pre-label (Databricks Job)

This repo includes a **bundle job** (`resources/preannotate_job.job.yml`) and API routes so large pre-label batches run on a cluster instead of inside the app HTTP worker.

1. From the repo root: `databricks apps deploy --target <target> --profile <profile>`.
2. With **`resources/cv_explorer_app.yml`**, the app receives **`PRE_ANNOTATE_DATABRICKS_JOB_ID`** via `valueFrom: app-preannotate-job` (no manual copy). Bundle job parameters inject the exact Lakebase project, endpoint, PostgreSQL database, and inference adapter before the worker imports the backend.

The dashboard shows **Pre-label (job)** when the job id resolves. The job runs `scripts/preannotate_job.py` with the `preannotate_runs` row id; it reuses the same Lakebase/SQLite and model-serving code paths as the app.

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
- `dry_run` — runs pass 1 only, returns the counters that *would* result.
  Note: dry-run counters are conservative — they assume every import
  succeeds as `annotations_created`. Actual `annotations_replaced` or
  `samples_skipped` may differ when `on_existing_annotations` is
  `replace` or `skip`. A future PR will prefetch existing-annotation
  counts per sample for exact dry-run parity.

### Responses

- `200` — success, body has counters (`samples_touched`, `annotations_created`, `annotations_replaced`, `samples_skipped`, `samples_created`)
- `400` — bad format, unreadable volume_path, invalid flag value
- `404` — project not found
- `422` — validation failed, body has `errors[]` (capped at 100) and `error_count`
- `500` — commit failed, transaction rolled back

### Limits and caveats

- Soft cap: 500,000 items per request. Split larger imports.
- Hard cap: 200 MB per file. Requests larger than this return `400`
  before any parsing.
- Hard cap: 2,000,000 annotations per import (protects against
  pathological COCO files with one image and millions of annotations).
- Filenames must be basenames — no path separators, no `..`, no empty
  segments. COCO `file_name` values that include subdirectories are
  rejected; this keeps `project_samples.filename` consistent with
  `scan_volume_for_samples` which stores basenames.
- `volume_path` must start with `/Volumes/` and contain no `..`
  segments. Local-filesystem paths are rejected (except in tests via a
  dedicated `X-Test-Allow-Local-Path` header that production never sets).
- No per-project ACLs — anyone who can call the app can import.
- `replace` is content-idempotent; re-running produces the same state.
  Replacing with zero annotations transitions the sample back to
  `status='unlabeled'`.
- `append` is not idempotent — re-running duplicates annotations.
- Duplicate filenames within a single import are rejected in
  pass 1 (no partial inserts).

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

## Project Structure

```
cv-explorer/
├── app.yml                         # Databricks App manifest (Git deploy expects app.yml)
├── start.py                        # Uvicorn entrypoint
├── requirements.txt                # Python dependencies
├── backend/
│   ├── main.py                     # FastAPI app entry point + startup
│   ├── models.py                   # SQLAlchemy models (Project, Sample, Annotation)
│   ├── schemas.py                  # Pydantic request/response schemas
│   ├── deps.py                     # Shared dependencies (DB session, workspace client)
│   ├── lakebase.py                 # Lakebase auto-provisioning + token refresh
│   ├── volumes.py                  # UC Volume helper functions
│   └── routes/
│       ├── projects.py             # Project CRUD endpoints
│       ├── labeling.py             # Annotation + sample endpoints
│       ├── export.py               # Dataset export to UC Volumes
│       ├── browse.py               # Volume browsing endpoints
│       └── admin.py                # Admin + Lakebase status endpoints
├── frontend/
│   ├── src/
│   │   ├── api/client.js           # Axios API client
│   │   ├── components/
│   │   │   ├── BBoxCanvas.jsx      # Canvas for drawing bounding boxes
│   │   │   ├── AnnotationCanvas.jsx# Display-only annotation overlay
│   │   │   ├── Layout.jsx          # App shell with sidebar navigation
│   │   │   ├── FilterableSelect.jsx# Searchable dropdown component
│   │   │   └── Spinner.jsx         # Loading indicator
│   │   └── pages/
│   │       ├── ProjectsPage.jsx    # Project listing
│   │       ├── CreateProject.jsx   # New project form
│   │       ├── ProjectDashboard.jsx# Stats, gallery, export
│   │       ├── LabelingView.jsx    # Annotation interface
│   │       ├── BrowseVolumes.jsx   # UC Volume browser
│   │       └── AdminPage.jsx       # Lakebase admin panel
│   └── vite.config.js              # Vite config with /api proxy
└── docs/
    ├── phase1-design.md            # Phase 1 architecture design
    └── plans/                      # Implementation plans and design docs
```

## Database Schema (Lakebase)

Seven tables are auto-created on startup with `REPLICA IDENTITY FULL` for
optional Lakehouse Sync:

- **labeling_projects** — task/source configuration, classes, version lineage, and serving settings
- **project_samples** — filepath, filename, status (unlabeled/labeled/skipped), locked_by/locked_at
- **annotations** — classification, bbox, or mask labels with draft/model provenance
- **annotation_history** — prior annotation states
- **preannotate_runs**, **embedding_runs**, **finetune_runs** — asynchronous workflow state

Bounding boxes use normalized `[0, 1]` coordinates. Segmentation masks use
COCO RLE in `mask_json`.

## Dataset Export

For classification, choose **Prepare training dataset** from the Project Dashboard Actions menu or the **Datasets & training** tab. Confirm the labeled-image and class counts, then click **Prepare dataset**. The default destination comes from `EXPORT_VOLUME_PATH` (or `DEMO_VOLUME_PATH/exports`, falling back to the project's source path plus `/exports`). Advanced settings allow an override.

The default classification export writes three files into a unique snapshot directory:

- `train.jsonl`: one record per labeled sample, with `sample_id`, absolute `/Volumes/...` image path in `image`, and numeric `label`.
- `classes.json`: `names` in project class order and the matching `label2id` mapping.
- `metadata.json`: project/version, snapshot ID, counts, timestamp, author and sample/annotation lineage. Written last as the completion marker; incomplete snapshots are not listed as ready.

Only confirmed classification annotations on labeled samples are included. Missing, ambiguous or unknown labels fail validation before any files are written. Draft predictions and unlabeled images are excluded. No image bytes are read, decoded or copied, so preparation performs three uploads regardless of image count. This snapshots labels, not image contents: keep source images unchanged and available. Existence and decoding are checked when training reads them.

The ready screen and dataset history provide copyable Hugging Face loading code. On Databricks compute with `datasets` and `Pillow` installed:

```python
import json
from pathlib import Path
from datasets import ClassLabel, Image, load_dataset

export_dir = Path("/Volumes/catalog/schema/output/snapshot_directory")
classes = json.loads((export_dir / "classes.json").read_text())
dataset = load_dataset("json", data_files=str(export_dir / "train.jsonl"), split="train")
dataset = dataset.cast_column("label", ClassLabel(names=classes["names"]))
dataset = dataset.cast_column("image", Image())
```

The training identity needs `USE CATALOG`, `USE SCHEMA` and `READ VOLUME` on both source and export Volumes. Images are decoded when accessed; model-specific transforms/collation and train/validation splitting belong in the training code. Vision-language `SFTTrainer` additionally requires the selected model's message/image format; the manifest is not itself an SFT conversation dataset.

The **Datasets & training** tab remains available without a configured training job. It lists snapshots in the default destination (including legacy snapshots under its `exports/` child). Custom destinations must be saved separately; they are not discovered after refresh.

Existing external finetuning jobs may expect CSV or COCO. Update the job's loader to consume `train.jsonl` and `classes.json`, then set `FINETUNE_SUPPORTS_REFERENCE_DATASETS=true` in the App environment to enable launching reference datasets. Until then, loading code is available but reference datasets cannot be submitted to that job. The existing `export_path` job parameter is unchanged.

Detection uses the same lightweight approach by default. It writes `annotations.json` with absolute source Volume paths and `bbox_normalized` values, followed by `metadata.json` as the completion marker. It does not open or copy any image, so the amount of export I/O is independent of the number and size of the images. The ready screen supplies loading code that opens each image during training, adds its dimensions, and converts the normalized boxes to standard pixel COCO in memory.

The lightweight detection format is named `coco_reference` because its on-disk manifest is not standard portable COCO: image dimensions are deliberately deferred and boxes use normalized coordinates. After running the supplied loader, the in-memory object has standard `width`, `height`, `bbox`, and `area` fields and can be indexed with `pycocotools` or adapted to a training framework.

For a portable classification or detection export, select **Advanced settings → Include image copies**. Detection then produces standard COCO `annotations.json` plus `images/`. Copied exports retain the existing synchronous image-transfer behavior and can still time out for large datasets.

API clients keep their existing copied-export behavior by default. To prepare a reference snapshot, use `POST /api/projects/{id}/export` with `{"mode": "reference"}` and optionally `export_volume`. Its response includes the snapshot path, counts, timestamp and loading code. Dataset history uses `GET /api/projects/{id}/exports`.

For copied COCO exports, bounding boxes are converted from normalised (0-1) to absolute pixels during export. For `coco_reference`, that conversion happens when the supplied loader reads image dimensions during training.

## Tech Stack

- **Frontend**: React 19, Vite, React Router
- **Backend**: FastAPI, SQLAlchemy 2, Pydantic 2
- **Database**: Lakebase (managed PostgreSQL on Databricks)
- **Image storage**: Unity Catalog Volumes
- **Auth**: Databricks SSO (via Databricks Apps)
- **SDK**: databricks-sdk for Lakebase, Volumes, and workspace APIs
