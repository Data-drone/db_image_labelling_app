# Phase 1 Implementation Plan

Reference: [Phase 1 Design](../phase1-design.md)

## Prerequisites

Before starting implementation:
- [ ] Merge `react-rebuild` → `main`, remove Streamlit code
- [ ] Confirm `databricks-sdk >= 0.99` is available (for Lakebase Autoscaling API)

No manual Lakebase setup required — the app self-provisions.

## Step 1: Branch Merge & Cleanup

**Goal:** Make `react-rebuild` the default branch, remove Streamlit artifacts.

**Tasks:**
1. Merge `react-rebuild` into `main`
2. Delete Streamlit files from `main`: `app.py`, `pages/`, `utils/`, old `requirements.txt`
3. Keep React app structure as-is with `backend/` + `frontend/`
4. Update `CLAUDE.md` with correct paths, new architecture, Phase 1 goals
5. Push to `main`

**Files touched:**
- Delete: `app.py`, `pages/`, `utils/`
- Edit: `CLAUDE.md`, `requirements.txt`

---

## Step 2: Lakebase Setup Module

**Goal:** Backend module that auto-provisions Lakebase and manages connections.

**Tasks:**
1. New `backend/lakebase.py`:
   - `ensure_lakebase_project()` — find or create Lakebase Autoscaling project via SDK
     ```python
     w = WorkspaceClient()  # uses DATABRICKS_CLIENT_ID/SECRET from Apps SP
     # Try to get existing project
     try:
         project = w.postgres.get_project(name=f"projects/{project_id}")
     except NotFound:
         op = w.postgres.create_project(
             project=Project(spec=ProjectSpec(display_name=display_name)),
             project_id=project_id,
         )
         project = op.wait()
     ```
   - `get_endpoint()` — find the read-write endpoint, get host
   - `generate_connection_string()` — call `generate_database_credential()`, build `postgresql://` URL
   - `refresh_token_loop()` — background thread that refreshes the token every 30 min
   - `get_engine()` — returns SQLAlchemy engine with current connection string
   - `setup_replica_identity()` — runs `ALTER TABLE ... REPLICA IDENTITY FULL` on each table after creation

2. Update `backend/models.py`:
   - Remove: SQLite backup/restore, WAL pragma, all old models
   - Add: `LabelingProject`, `ProjectSample`, `Annotation` (new schema)
   - Engine creation uses `lakebase.get_engine()` instead of direct `create_engine(DATABASE_URL)`
   - `init_db()` calls `create_all()` then `setup_replica_identity()`

3. Update `app.yaml`:
   ```yaml
   command: ['python', 'start.py']
   env:
     - name: LAKEBASE_PROJECT_ID
       valueFrom: lakebase-db
     - name: DEMO_VOLUME_PATH
       value: '/Volumes/brian_gen_ai/cv_explorer/demo_images'
   ```

4. Update `backend/requirements.txt`:
   - Add: `psycopg2-binary`
   - Ensure: `databricks-sdk>=0.99`

**Files touched:**
- New: `backend/lakebase.py`
- Rewrite: `backend/models.py`
- Edit: `app.yaml`, `backend/requirements.txt`

**Verification:**
- Run app locally with DATABRICKS_HOST + token configured
- Verify Lakebase project is created (or connected to existing)
- Verify tables are created in Lakebase
- Verify REPLICA IDENTITY FULL is set
- Token refresh works (check logs after 30 min)

---

## Step 3: Backend Models & Schemas

**Goal:** New Project-centric data models and Pydantic schemas.

**Tasks:**
1. Finalize `backend/models.py` with new models:
   - `LabelingProject` — id, name, description, task_type, class_list, source_volume, created_by, created_at
   - `ProjectSample` — id, project_id, filepath, filename, locked_by, locked_at, status
   - `Annotation` — id, sample_id, project_id, label, ann_type, bbox_json, created_by, created_at

2. Rewrite `backend/schemas.py`:
   - `ProjectCreate` — name, description, task_type, class_list, source_volume
   - `ProjectOut` — all fields + sample_count, labeled_count
   - `ProjectStats` — per-status counts, per-user breakdown
   - `SampleOut` — id, filepath, filename, status, annotations
   - `SamplePage` — items + total + page info
   - `AnnotationCreate` — label, ann_type, bbox_json (optional)
   - `AnnotationOut` — all fields

**Files touched:**
- Edit: `backend/models.py`, `backend/schemas.py`

**Verification:**
- Models create tables correctly
- Schemas serialize/deserialize without errors

---

## Step 4: Backend API — Project CRUD

**Goal:** API endpoints for creating and managing labeling projects.

**Tasks:**
1. `POST /api/projects` — create project (name, task_type, class_list, source_volume)
   - Scan source volume for images, create `project_samples` rows
   - Extract user email from Databricks Apps headers
2. `GET /api/projects` — list all projects with stats (sample counts by status)
3. `GET /api/projects/{id}` — single project detail
4. `DELETE /api/projects/{id}` — delete project and all associated data
5. `GET /api/projects/{id}/stats` — detailed stats (per-user, per-status counts)

**Files touched:**
- Rewrite most of: `backend/main.py`

**Verification:**
- Create a project pointing at demo_images volume
- Verify samples are scanned and stored
- List projects returns correct counts

---

## Step 5: Backend API — Labeling Workflow

**Goal:** Endpoints for the labeling flow with lock-on-open.

**Tasks:**
1. `GET /api/projects/{id}/next` — get next unlabeled sample (with lock-on-open)
   - Query: `status = 'unlabeled' AND (locked_by IS NULL OR locked_at < 5min ago)`
   - Set `locked_by`, `locked_at` on the returned sample
   - Return sample data + image URL
2. `POST /api/projects/{id}/samples/{sample_id}/annotate` — save annotation
   - Create annotation row
   - Set sample `status = 'labeled'`, clear lock
3. `POST /api/projects/{id}/samples/{sample_id}/skip` — skip sample
   - Set `status = 'skipped'`, clear lock
4. `GET /api/projects/{id}/samples/{sample_id}/image` — serve image file
   - Read from `/Volumes/...` path, return as response
5. `GET /api/projects/{id}/samples` — paginated sample list with status filters

**Files touched:**
- Add to: `backend/main.py`
- Add to: `backend/schemas.py`

**Verification:**
- Get next sample, verify lock is set
- Annotate sample, verify status changes
- Get next again, verify it returns a different sample
- Wait 5+ min, verify stale lock is skipped

---

## Step 6: Frontend — Projects List Page

**Goal:** Replace the home page with a projects-centric view.

**Tasks:**
1. New `ProjectsPage.jsx` — replaces `HomePage.jsx`
   - Table/grid of all projects
   - Progress bars (labeled / total)
   - Task type badge, creator, date
   - "Create Project" button → navigates to create form
   - Click project → navigates to project dashboard
2. New `ProjectContext.jsx` — replaces `DatasetContext.jsx`
   - Stores current project, provides to child components
3. Update `App.jsx` routing:
   - `/` → ProjectsPage
   - `/projects/new` → CreateProject
   - `/projects/:id` → ProjectDashboard
   - `/projects/:id/label` → LabelingView
4. Update `Layout.jsx` sidebar navigation

**Files touched:**
- New: `frontend/src/pages/ProjectsPage.jsx`
- New: `frontend/src/contexts/ProjectContext.jsx`
- Edit: `frontend/src/App.jsx`
- Edit: `frontend/src/components/Layout.jsx`
- Delete: `frontend/src/pages/HomePage.jsx`
- Delete: `frontend/src/contexts/DatasetContext.jsx`

**Verification:**
- Page loads, shows projects list
- Create button navigates correctly
- Progress bars render

---

## Step 7: Frontend — Create Project Page

**Goal:** Form to create a new labeling project.

**Tasks:**
1. New `CreateProject.jsx`:
   - Project name + description inputs
   - Volume browser (reuse/adapt `BrowseVolumes.jsx`)
   - Task type selector (classification / detection)
   - Class list editor (add/remove labels dynamically)
   - Submit → POST /api/projects → redirect to project dashboard
2. Update `api/client.js` with project API functions

**Files touched:**
- New: `frontend/src/pages/CreateProject.jsx`
- Edit: `frontend/src/api/client.js`
- Possibly edit: `frontend/src/pages/BrowseVolumes.jsx` (make reusable)

**Verification:**
- Create a project via the UI
- Verify it appears in the projects list
- Verify samples were scanned from Volume

---

## Step 8: Frontend — Labeling View (Rewrite)

**Goal:** Rewrite labeling view for project-centric workflow.

**Tasks:**
1. Rewrite `LabelingView.jsx`:
   - Fetch next unlabeled sample via `GET /api/projects/{id}/next`
   - For classification: show class buttons from project's `class_list`
   - For detection: show bounding box canvas (reuse `AnnotationCanvas.jsx`)
   - Next / Skip / Previous navigation
   - Keyboard shortcuts: 1-9 for class labels, arrow keys for navigation
   - Progress bar: "42 / 500 labeled"
   - Lock indicator (show who has which sample locked)
   - Show existing annotation if sample already labeled
2. Update `api/client.js` with labeling API functions

**Files touched:**
- Rewrite: `frontend/src/pages/LabelingView.jsx`
- Edit: `frontend/src/api/client.js`
- Possibly edit: `frontend/src/components/AnnotationCanvas.jsx`

**Verification:**
- Open labeling view, image loads
- Classify an image, verify it advances to next
- Skip an image, verify it advances
- Check DB: annotation created, sample status updated
- Open in second browser: verify different sample is served (lock working)

---

## Step 9: Frontend — Project Dashboard

**Goal:** Per-project stats and Lakehouse Sync status.

**Tasks:**
1. New `ProjectDashboard.jsx`:
   - Project header (name, description, task type)
   - Stats cards: total, labeled, unlabeled, skipped
   - Per-user contribution table
   - "Start Labeling" button → navigates to labeling view
   - Lakehouse Sync status indicator
   - Info panel: where the Delta tables live in UC, example queries

**Files touched:**
- New: `frontend/src/pages/ProjectDashboard.jsx`
- Edit: `frontend/src/api/client.js`

**Verification:**
- Dashboard shows correct stats
- Sync status displays correctly
- Links to UC catalog work

---

## Step 10: Lakehouse Sync, Cleanup & Deploy

**Goal:** Enable sync, remove dead code, test end-to-end, deploy.

**Tasks:**
1. Configure Lakehouse Sync in Databricks workspace:
   - Verify `REPLICA IDENTITY FULL` was set by the app
   - Enable sync in Lakebase UI → choose destination catalog/schema
   - Verify Delta tables appear in UC
2. Remove unused files:
   - `frontend/src/pages/DatasetExplorer.jsx`
   - `frontend/src/pages/SearchPage.jsx`
   - `frontend/src/components/DatasetSelector.jsx`
   - `frontend/src/components/Pagination.jsx` (if not reused)
3. Remove unused backend endpoints (old dataset/sample/tag CRUD)
4. Update `start.py` if needed
5. Build frontend: `npm run build`
6. Deploy to Databricks Apps with resources configured:
   - Lakebase database resource (key: `lakebase-db`)
   - UC Volume resource (key: `source-volume`)
7. End-to-end test:
   - App starts → auto-provisions/connects to Lakebase
   - Create project → Label images → Check Delta tables in UC
   - Test with 2 users for lock-on-open behavior
   - Verify Lakehouse Sync is replicating changes
8. Update `CLAUDE.md` with final state

**Files touched:**
- Delete unused files
- Edit: `start.py` (if needed)
- Edit: `CLAUDE.md`

---

## Estimated Order & Dependencies

```
Step 1 (branch merge) ─────────────────────────────────────────────────┐
Step 2 (lakebase module) ──────────────────────────────────────────────┤
Step 3 (models & schemas) ─────────────────────────────────────────────┤
Step 4 (project API) ──────────────────┬───────────────────────────────┤
Step 5 (labeling API) ─────────────────┘                               │
                                       │                               │
Step 6 (projects list page) ───────────┼── depends on Step 4 API ──────┤
Step 7 (create project page) ──────────┤                               │
Step 8 (labeling view) ────────────────┼── depends on Step 5 API ──────┤
Step 9 (project dashboard) ────────────┤                               │
                                       │                               │
Step 10 (sync, cleanup & deploy) ──────┴───────────────────────────────┘
```

Backend steps (2-5) must be done sequentially (each builds on the previous).
Frontend steps (6-9) are mostly independent of each other once the API is ready.
Step 10 depends on everything else.

## Key Decisions to Confirm During Implementation

- Lakebase project ID naming convention (e.g., `cv-explorer`)
- Destination UC catalog/schema for Lakehouse Sync Delta tables
- Whether to keep the existing BrowseVolumes component or simplify it
- Image thumbnail caching strategy (currently regenerated per request)
- Token refresh interval (30 min default — may need tuning based on token TTL)
