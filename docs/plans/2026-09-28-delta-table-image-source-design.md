# Delta table as image source — Design

**Status:** proposed, ready for implementation
**Date:** 2026-09-28
**Implementation plan:** `docs/plans/2026-09-28-delta-table-image-source.md`
**Roadmap:** Phase 5.5

## Problem

Projects can only be created from a UC Volume directory listing (`scan_volume_for_samples`). That scan is one directory level, carries no extra attributes, and is not a Unity Catalog table — so Catalog Explorer lineage never sees “this labeling project read this image catalog.”

Teams already (or will) land images as a Delta table with `image_path` plus metadata (split, site, capture time, IDs). Labeling should be able to start from **that table**, per project, without changing how pixels are served.

## Goal

Let each labeling project choose its own image source:

- **volume** (today): list files under a `/Volumes/...` folder
- **table** (new): `SELECT` rows from a caller-chosen `catalog.schema.table`

Different projects may point at different tables (or the same table with different filters). Samples are still copied into Lakebase `project_samples`; the UI still loads bytes via the Files API from each row’s `filepath`.

## Non-goals (v1)

- Serving image **bytes** from a Delta `BINARY` / `content` column (warehouse on the hot path)
- Replacing Lakebase for annotations or locks
- Recursing volume folders (table source is the nested-layout answer)
- A Lakeflow job that writes a governed `labeled_dataset` / registers a model (v1.1 — that is what draws Catalog lineage downstream)
- Per-table ACLs inside the app (app SP needs `SELECT` + `READ VOLUME`; same as today)
- Live re-query of the table on every gallery page (scan once into `project_samples`, same as volumes)
- SQLite local-dev querying of UC tables (volume + local dir remain the local path)

## Architecture

```
Create project (source_type=table)
  → SQL warehouse / statement execution
  → SELECT image_path [, extras] FROM catalog.schema.table [WHERE …]
  → ProjectSample(filepath=image_path, filename=basename)
  → (optional) copy selected metadata onto the sample later

Label / embed / pre-annotate / export
  → unchanged: read_image_bytes(sample.filepath) via Files API
```

The Delta table is a **sample catalog**. The volume (or whatever `image_path` points at) remains the **blob store**.

Per-project: `source_table` lives on `labeling_projects`, analogous to `source_volume`. The app has one shared warehouse resource; table FQNs are not global.

## Data model

`labeling_projects` additive columns (existing `source_volume` stays `NOT NULL` for back-compat):

| Column | Type | Notes |
|--------|------|--------|
| `source_type` | text | `'volume'` (default) or `'table'` |
| `source_table` | text, nullable | UC FQN `catalog.schema.table` when `source_type='table'` |
| `image_path_column` | text, nullable | Column to read paths from; default `'image_path'` |
| `source_filter` | text, nullable | Optional `WHERE` body only (no `;`, no comments). Empty = all rows |

`source_volume` for table-backed projects: set to the **4-segment volume root** of the first valid path (`/Volumes/cat/sch/vol`) as a display / default-export hint. If paths span multiple volumes, keep that hint from the first row; **do not** use it as the sole allowlist for file reads.

`project_samples` unchanged: `filepath` is the Files API path; `filename` is the basename (import matching stays basename-based unless a later change).

## API

### Create / update

`POST /api/projects` and `PATCH /api/projects/{id}` accept:

```json
{
  "name": "Cameras Q3",
  "task_type": "detection",
  "class_list": ["ok", "defect"],
  "source_type": "table",
  "source_table": "main.cv.image_catalog",
  "image_path_column": "image_path",
  "source_filter": "split = 'train'",
  "source_volume": ""
}
```

Validation:

- `source_type` omitted or `'volume'` → current behavior; `source_volume` required; table fields ignored.
- `source_type='table'` → `source_table` required (`catalog.schema.table`, three dotted parts, no injection). `source_volume` optional (filled from first path).
- Changing `source_volume` **or** `source_table` / filter / path column still requires `confirm_source_change` and wipes samples + annotations, then re-scans.

`ProjectOut` includes the new fields so the dashboard can show the FQN.

Clone copies `source_type`, `source_table`, `image_path_column`, `source_filter`, and `source_volume`.

### Browse

| Endpoint | Purpose |
|----------|---------|
| `GET /api/tables?catalog=&schema=` | List table names (SDK `tables.list`), same pattern as `/volumes` |
| `GET /api/tables/preview?full_name=` | Columns + `COUNT(*)` (capped) so the form can pick the path column |

No recursive “browse table rows” UI in v1 beyond preview + create.

## Table scan rules

`scan_table_for_samples(db, project_id, full_name, path_column, where_sql)`:

1. Quote catalog/schema/table as three identifiers; never concatenate raw FQN into SQL without splitting/quoting.
2. `SELECT {path_column} FROM {fqn} [WHERE {source_filter}]`.
3. Cap rows (env `TABLE_SOURCE_MAX_ROWS`, default 50_000). Over cap → 400 with a clear error.
4. Each path must be a non-empty string starting with `/Volumes/`, no `..`, no backslashes. Skip or fail: **fail the whole create** if any row is invalid (no silent drop) so the user sees bad catalog data.
5. Duplicate paths in one scan → one `ProjectSample`.
6. `filename = os.path.basename(path)`.
7. Warehouse errors (missing table, no SELECT, no warehouse id) → 400/502 with the Databricks message, not a zero-sample project that looks empty.

Local pytest: inject a fake row iterator; do not call a real warehouse.

## Image I/O and callers

Unchanged once `filepath` is a volume path:

- `backend/volumes.py` `read_image_bytes`
- labeling image routes, embeddings, pre-annotate, inference, import file_exists

**Must change** (they assume every file is `source_volume + "/" + basename`):

- `backend/routes/import_routes.py` — resolve existing samples by basename as today; `on_missing_sample=create` must `file_exists` on the **full path from the table** if known, or reject with “table-backed projects cannot invent paths from basename only”
- `backend/dataset_exports.py` `_is_source_image_path` — allow any `/Volumes/` path on the sample row, not only prefix-of-`source_volume`

Export `metadata.json` `lineage` adds:

```json
"source_type": "table",
"source_table_uc": "main.cv.image_catalog",
"image_path_column": "image_path",
"source_filter": "split = 'train'"
```

Keep `source_volume_uc` as the hint prefix.

## Frontend

`CreateProject.jsx`: source toggle **Volume | Delta table**.

Table mode: catalog → schema → table dropdowns (reuse `fetchCatalogs` / `fetchSchemas`), then table list; optional path-column select from preview; optional filter textarea. Submit sends `source_type` + table fields.

`ProjectDashboard.jsx`: show source type + FQN; edit/rescan uses the same confirm wipe as volume change.

Volume mode and Browse Volumes stay as they are.

## Databricks resources

App needs a **SQL warehouse** (or DBSQL statement execution warehouse id):

- `app.yml` / `resources/cv_explorer_app.yml`: resource + `SQL_WAREHOUSE_ID` via `valueFrom`
- Grants: app SP `SELECT` on each project’s table; `READ VOLUME` on volumes those paths use (cannot be declared as one static volume if projects use many tables)

v1 does not attach every possible image table as an app `uc_securable`; workspace grants on the SP cover it, same as listing arbitrary volumes today via the SDK.

## Lineage (honest)

v1 gives:

- A per-project UC table FQN stored on the project and in export JSON
- Ability for **downstream jobs** to join that table to Lakehouse-synced annotations

v1 does **not** automatically draw Catalog Explorer edges for Files API reads during labeling.

v1.1 (separate): Lakeflow job `image_catalog ⋈ annotations_history → labeled_dataset` (+ optional model register). That write is the UC table lineage.

## Compatibility

- Existing projects: `source_type` default `'volume'`; no behavior change.
- `create_all` + `_ensure_columns` already ADD COLUMN for new SQLAlchemy fields.
- Tests that POST `source_volume` only keep working.
