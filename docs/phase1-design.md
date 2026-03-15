# CV Explorer — Phase 1 Design

## Vision

A fast, functional image labeling tool for Databricks. Users point it at a UC Volume of images, create a labeling project, collaborate on annotations, and export versioned Delta Tables suitable for fine-tuning ML/LLM models.

## Use Cases

1. **Whole-image classification** — assign one label per image (e.g., "cat", "dog", "car")
2. **Bounding box detection** — draw rectangles around objects with labels

## Architecture

### Storage Layers

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Operational store | **Lakebase (PostgreSQL)** | Projects, samples, annotations, locks. Multi-user, ACID, persistent across deploys. |
| Image storage | **UC Volumes** | Raw images stay in place. App reads via `/Volumes/...` paths. |
| Output | **Delta Tables** | One table per project. Written via `databricks-sql-connector`. Same catalog/schema as source Volume by default, user can override. |

### Why Lakebase over SQLite

- Persists across app restarts/redeploys (no backup hacks needed)
- Native multi-user concurrency (no WAL mode workarounds)
- PostgreSQL-compatible — SQLAlchemy works with minimal changes
- Available on user's Azure workspace

## Data Model (Lakebase)

### labeling_projects

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| name | text, unique | Project display name |
| description | text | Optional |
| task_type | text | `'classification'` or `'detection'` |
| class_list | jsonb | Array of label strings, e.g. `["cat","dog","car"]` |
| source_volume | text | UC Volume path, e.g. `/Volumes/catalog/schema/volume` |
| output_catalog | text | Target catalog for Delta export |
| output_schema | text | Target schema for Delta export |
| output_table | text | Table name for Delta export |
| created_by | text | User email from Databricks Apps headers |
| created_at | timestamptz | |

### project_samples

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| project_id | int FK | → labeling_projects.id |
| filepath | text | Full `/Volumes/...` path |
| filename | text | Just the filename |
| locked_by | text, nullable | User email holding lock |
| locked_at | timestamptz, nullable | When lock was acquired |
| status | text | `'unlabeled'`, `'labeled'`, `'skipped'` |

### annotations

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| sample_id | int FK | → project_samples.id |
| label | text | The class label |
| ann_type | text | `'classification'` or `'bbox'` |
| bbox_json | jsonb, nullable | `{"x":..,"y":..,"w":..,"h":..}` for detection |
| created_by | text | User email |
| created_at | timestamptz | |
| version | int | Defaults to 1, incremented on re-export |

### export_history

| Column | Type | Notes |
|--------|------|-------|
| id | serial PK | |
| project_id | int FK | → labeling_projects.id |
| version | int | Incrementing version number |
| row_count | int | Number of annotations exported |
| delta_table_path | text | Full `catalog.schema.table` path |
| exported_by | text | User email |
| exported_at | timestamptz | |

## Lock-on-Open Collaboration

When a user opens a sample for labeling:
1. Set `locked_by = user_email`, `locked_at = now()`
2. Other users querying for next sample skip locked items:
   ```sql
   WHERE status = 'unlabeled'
     AND (locked_by IS NULL OR locked_at < now() - interval '5 minutes')
   ORDER BY id
   LIMIT 1
   ```
3. On save: set `status = 'labeled'`, clear `locked_by`/`locked_at`
4. On skip: set `status = 'skipped'`, clear lock
5. Stale locks (>5 min) are automatically available to others

## Delta Table Export

### Schema (one table per project)

| Column | Type | Notes |
|--------|------|-------|
| filepath | STRING | Full Volume path |
| filename | STRING | Just filename |
| label | STRING | The class label |
| ann_type | STRING | `classification` or `bbox` |
| bbox_x | DOUBLE | Nullable — bounding box fields |
| bbox_y | DOUBLE | Nullable |
| bbox_w | DOUBLE | Nullable |
| bbox_h | DOUBLE | Nullable |
| annotated_by | STRING | User email |
| version | INT | Export version number |
| exported_at | TIMESTAMP | When this export ran |

### Versioning Strategy

- Each export writes a **full snapshot** of all current annotations with an incrementing version number
- Query any version: `SELECT * FROM table WHERE version = N`
- Compare versions: diff version N vs N-1 to see what changed
- Re-labeling workflow: fix mistakes → re-export → new version with all corrections included

### Export Mechanism

- Use `databricks-sql-connector` to write via SQL warehouse
- `INSERT INTO catalog.schema.table SELECT ... FROM` (assembled from Lakebase query results)
- No Spark session needed from the Databricks App

## Pages

### 1. Home / Projects List
- Table/grid of all labeling projects
- Progress bars (labeled/total samples)
- Quick stats: task type, creator, date
- "Create Project" button

### 2. Create Project
- Name + description
- Browse/select UC Volume (reuse existing BrowseVolumes component)
- Choose task type: classification or detection
- Define class list (add/remove labels)
- Set output location (default: same catalog/schema as Volume, with override)
- Preview: show sample images from selected Volume

### 3. Labeling View
- Large image display area
- For classification: click-to-classify buttons for each class
- For detection: bounding box drawing canvas (reuse AnnotationCanvas)
- Navigation: Next / Previous / Skip
- Keyboard shortcuts (1-9 for class selection, arrow keys for nav)
- Progress indicator: "42 / 500 labeled"
- Lock status indicator
- Existing annotation badges (what label was applied)

### 4. Project Dashboard
- Per-project stats: total samples, labeled, skipped, unlabeled
- Per-user contribution breakdown
- Export button → triggers Delta Table write
- Version history table (from export_history)
- Re-label button → increments version, resets statuses for re-review

## Authentication

Databricks Apps provides user identity via HTTP headers. The FastAPI backend extracts user email from these headers and uses it for:
- `created_by` on projects
- `locked_by` on samples
- `annotated_by` / `created_by` on annotations
- `exported_by` on exports

## What Gets Removed from Current App

- SQLite database + UC Volume backup/restore mechanism
- Generic `Dataset` / `Sample` / `Annotation` / `Tag` models
- `DatasetContext` React context
- `DatasetSelector` component
- Browse/explore gallery flow (replaced by project-centric flow)
- Search page (not needed for Phase 1)

## Branch Strategy

1. Merge `react-rebuild` → `main` (make React the default)
2. Remove Streamlit code (`app.py`, `pages/`, `utils/`)
3. Continue Phase 1 development on `main`
4. Update `CLAUDE.md` to reflect new structure

## Dependencies

### Backend (additions)
- `psycopg2-binary` — PostgreSQL driver for Lakebase
- `databricks-sql-connector` — for Delta Table export via SQL warehouse

### Frontend
- No new dependencies — React + Vite stack stays the same
