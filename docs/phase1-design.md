# CV Explorer — Phase 1 Design

## Vision

A fast, functional image labeling tool for Databricks. Users point it at a UC Volume of images, create a labeling project, collaborate on annotations, and get automatic Delta Table replication via Lakehouse Sync — suitable for fine-tuning ML/LLM models.

## Use Cases

1. **Whole-image classification** — assign one label per image (e.g., "cat", "dog", "car")
2. **Bounding box detection** — draw rectangles around objects with labels

## Architecture

### Storage Layers

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Operational store | **Lakebase (PostgreSQL)** | Projects, samples, annotations, locks. Multi-user, ACID, persistent across deploys. |
| Image storage | **UC Volumes** | Raw images stay in place. App reads via `/Volumes/...` paths. |
| Analytics / Output | **Lakehouse Sync → Delta Tables** | Automatic CDC replication from Lakebase to Unity Catalog. No manual export needed. |

### Why Lakebase over SQLite

- Persists across app restarts/redeploys (no backup hacks needed)
- Native multi-user concurrency (no WAL mode workarounds)
- PostgreSQL-compatible — SQLAlchemy works with minimal changes
- Lakehouse Sync provides automatic Delta Table replication
- Available on user's Azure workspace

### How Lakehouse Sync Works

Lakehouse Sync is a native Lakebase feature that uses Change Data Capture (CDC) to continuously replicate Postgres tables into Unity Catalog managed Delta tables. No external compute, pipelines, or jobs required.

- Every insert, update, and delete is captured as a new row (SCD Type 2 history)
- Delta tables are named `lb_<table_name>_history` in a chosen UC catalog/schema
- Each row includes system columns: `_change_type`, `_timestamp`, `_lsn`, `_xid`
- Change types: `insert`, `delete`, `update_preimage`, `update_postimage`

**Setup required (one-time):**
1. Set `REPLICA IDENTITY FULL` on each Lakebase table
2. Enable Lakehouse Sync in the Lakebase UI (schema-level)
3. Choose destination UC catalog and schema

**What this gives us for free:**
- Full history of every annotation change over time
- Point-in-time queries via `_timestamp` (no explicit version column needed)
- Current-state mirror via deduplication query
- No export button, no export jobs, no export code

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
| project_id | int FK | → labeling_projects.id (denormalized for easier queries) |
| label | text | The class label |
| ann_type | text | `'classification'` or `'bbox'` |
| bbox_json | jsonb, nullable | `{"x":..,"y":..,"w":..,"h":..}` for detection |
| created_by | text | User email |
| created_at | timestamptz | |

### How the Delta Tables Look (via Lakehouse Sync)

Lakehouse Sync automatically creates these Delta tables in Unity Catalog:

- `lb_labeling_projects_history` — project metadata changes
- `lb_project_samples_history` — sample status changes (useful for tracking labeling progress over time)
- `lb_annotations_history` — the main output: every annotation with full CDC history

Each table includes your data columns plus:

| Column | Type | Description |
|--------|------|-------------|
| _change_type | TEXT | `insert`, `delete`, `update_preimage`, `update_postimage` |
| _timestamp | TIMESTAMP | Transaction commit time in Postgres |
| _lsn | BIGINT | Postgres Log Sequence Number |
| _xid | INTEGER | Postgres Transaction ID |

### Querying Annotations for Training

**Current state (latest annotations):**
```sql
SELECT *
FROM (
  SELECT *,
    ROW_NUMBER() OVER (PARTITION BY id ORDER BY _lsn DESC) AS rn
  FROM `catalog.schema.lb_annotations_history`
  WHERE _change_type IN ('insert', 'update_postimage', 'delete')
)
WHERE rn = 1 AND _change_type != 'delete';
```

**Annotations as of a specific time (point-in-time):**
```sql
SELECT *
FROM (
  SELECT *,
    ROW_NUMBER() OVER (PARTITION BY id ORDER BY _lsn DESC) AS rn
  FROM `catalog.schema.lb_annotations_history`
  WHERE _change_type IN ('insert', 'update_postimage', 'delete')
    AND _timestamp <= '2026-03-20 12:00:00'
)
WHERE rn = 1 AND _change_type != 'delete';
```

**Join with samples to get full training data:**
```sql
WITH current_annotations AS (
  SELECT * FROM (
    SELECT *,
      ROW_NUMBER() OVER (PARTITION BY id ORDER BY _lsn DESC) AS rn
    FROM `catalog.schema.lb_annotations_history`
    WHERE _change_type IN ('insert', 'update_postimage', 'delete')
  ) WHERE rn = 1 AND _change_type != 'delete'
),
current_samples AS (
  SELECT * FROM (
    SELECT *,
      ROW_NUMBER() OVER (PARTITION BY id ORDER BY _lsn DESC) AS rn
    FROM `catalog.schema.lb_project_samples_history`
    WHERE _change_type IN ('insert', 'update_postimage', 'delete')
  ) WHERE rn = 1 AND _change_type != 'delete'
)
SELECT
  s.filepath, s.filename,
  a.label, a.ann_type, a.bbox_json,
  a.created_by AS annotated_by
FROM current_annotations a
JOIN current_samples s ON a.sample_id = s.id
WHERE a.project_id = 1;
```

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
- "Start Labeling" button
- Lakehouse Sync status indicator (syncing / not configured)
- Link to Delta tables in Unity Catalog

## Authentication

Databricks Apps provides user identity via HTTP headers. The FastAPI backend extracts user email from these headers and uses it for:
- `created_by` on projects
- `locked_by` on samples
- `created_by` on annotations

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

### Backend (removals)
- `databricks-sql-connector` — not needed, Lakehouse Sync handles Delta output

### Frontend
- No new dependencies — React + Vite stack stays the same

## One-Time Setup (Outside the App)

These steps are done in the Databricks workspace, not in app code:

1. **Create Lakebase project** (if not already done)
2. **Set REPLICA IDENTITY FULL** on all tables:
   ```sql
   ALTER TABLE labeling_projects REPLICA IDENTITY FULL;
   ALTER TABLE project_samples REPLICA IDENTITY FULL;
   ALTER TABLE annotations REPLICA IDENTITY FULL;
   ```
3. **Enable Lakehouse Sync** in the Lakebase UI:
   - Source: `databricks_postgres` / `public` schema
   - Destination: chosen UC catalog and schema
4. Verify Delta tables appear: `lb_labeling_projects_history`, `lb_project_samples_history`, `lb_annotations_history`
