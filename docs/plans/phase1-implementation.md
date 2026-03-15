# Phase 1 Implementation Plan

Reference: [Phase 1 Design](../phase1-design.md)

## Prerequisites

Before starting implementation:
- [ ] Get Lakebase connection string from Databricks workspace
- [ ] Confirm SQL warehouse endpoint for Delta Table exports
- [ ] Merge `react-rebuild` → `main`, remove Streamlit code

## Step 1: Branch Merge & Cleanup

**Goal:** Make `react-rebuild` the default branch, remove Streamlit artifacts.

**Tasks:**
1. Merge `react-rebuild` into `main`
2. Delete Streamlit files from `main`: `app.py`, `pages/`, `utils/`, old `requirements.txt`
3. Move React app structure to root-level (or keep as-is with `backend/` + `frontend/`)
4. Update `CLAUDE.md` with correct paths, new architecture, Phase 1 goals
5. Push to `main`

**Files touched:**
- Delete: `app.py`, `pages/`, `utils/`
- Edit: `CLAUDE.md`, `requirements.txt`

---

## Step 2: Lakebase Migration — Backend Models

**Goal:** Replace SQLite models with PostgreSQL-backed Project-centric schema.

**Tasks:**
1. Replace `backend/models.py` entirely:
   - Remove: `Dataset`, `Sample`, `Annotation`, `Tag` models
   - Remove: SQLite backup/restore mechanism
   - Remove: WAL mode pragma
   - Add: `LabelingProject`, `ProjectSample`, `Annotation`, `ExportHistory` models
   - Use `DATABASE_URL` env var pointing to Lakebase Postgres endpoint
   - Add `psycopg2-binary` to requirements
2. Update `backend/schemas.py`:
   - Remove old Pydantic schemas
   - Add: `ProjectCreate`, `ProjectOut`, `ProjectStats`
   - Add: `SampleOut`, `SamplePage`
   - Add: `AnnotationCreate`, `AnnotationOut`
   - Add: `ExportOut`
3. Update `app.yaml`:
   - Change `DATABASE_URL` to Lakebase connection string
   - Remove `DB_BACKUP_VOLUME` env var
   - Keep `DEMO_VOLUME_PATH` for backward compat (optional)

**Files touched:**
- Rewrite: `backend/models.py`, `backend/schemas.py`
- Edit: `app.yaml`, `backend/requirements.txt`

**Verification:**
- App starts without errors
- Tables created in Lakebase
- Basic CRUD via curl/httpie

---

## Step 3: Backend API — Project CRUD

**Goal:** API endpoints for creating and managing labeling projects.

**Tasks:**
1. `POST /api/projects` — create project (name, task_type, class_list, source_volume, output location)
   - Scan source volume for images, create `project_samples` rows
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

## Step 4: Backend API — Labeling Workflow

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

## Step 5: Backend API — Export to Delta Table

**Goal:** Export annotations as a versioned Delta Table.

**Tasks:**
1. `POST /api/projects/{id}/export` — trigger export
   - Query all annotations for the project
   - Determine next version number from `export_history`
   - Use `databricks-sql-connector` to write to Delta Table
   - Record in `export_history`
   - Return version number + row count
2. `GET /api/projects/{id}/exports` — list export history

**Files touched:**
- Add to: `backend/main.py`
- New: `backend/export.py` (Delta export logic, isolated for testability)

**Verification:**
- Export a project with labeled samples
- Query Delta Table in Databricks SQL to verify data
- Export again, verify version increments
- Query `WHERE version = 1` vs `WHERE version = 2`

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
   - Output location fields (catalog, schema, table) with defaults
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

**Goal:** Per-project stats, export trigger, version history.

**Tasks:**
1. New `ProjectDashboard.jsx`:
   - Project header (name, description, task type)
   - Stats cards: total, labeled, unlabeled, skipped
   - Per-user contribution table
   - "Start Labeling" button → navigates to labeling view
   - "Export to Delta Table" button → triggers export, shows progress
   - Export history table (version, rows, date, who)
2. Update `api/client.js` with export API functions

**Files touched:**
- New: `frontend/src/pages/ProjectDashboard.jsx`
- Edit: `frontend/src/api/client.js`

**Verification:**
- Dashboard shows correct stats
- Export button creates Delta Table
- Version history populates after export

---

## Step 10: Cleanup & Polish

**Goal:** Remove dead code, test end-to-end, deploy.

**Tasks:**
1. Remove unused files:
   - `frontend/src/pages/DatasetExplorer.jsx`
   - `frontend/src/pages/SearchPage.jsx`
   - `frontend/src/components/DatasetSelector.jsx`
   - `frontend/src/components/Pagination.jsx` (if not reused)
2. Remove unused backend endpoints (old dataset/sample/tag CRUD)
3. Update `start.py` if needed
4. Build frontend: `npm run build`
5. Deploy to Databricks Apps
6. End-to-end test:
   - Create project → Label images → Export Delta Table → Verify in SQL
   - Test with 2 users for lock-on-open behavior
7. Update `CLAUDE.md` with final state

**Files touched:**
- Delete unused files
- Edit: `start.py` (if needed)
- Edit: `CLAUDE.md`

---

## Estimated Order & Dependencies

```
Step 1 (branch merge) ─────────────────────────────────────────────────┐
Step 2 (models) ────────────────────────────────────────────────────────┤
Step 3 (project API) ──────────────────┬───────────────────────────────┤
Step 4 (labeling API) ─────────────────┤                               │
Step 5 (export API) ───────────────────┘                               │
                                       │                               │
Step 6 (projects list page) ───────────┼── depends on Step 3 API ──────┤
Step 7 (create project page) ──────────┤                               │
Step 8 (labeling view) ────────────────┼── depends on Step 4 API ──────┤
Step 9 (project dashboard) ────────────┼── depends on Step 5 API ──────┤
                                       │                               │
Step 10 (cleanup & deploy) ────────────┴───────────────────────────────┘
```

Backend steps (2-5) can be done before frontend steps (6-9).
Frontend steps (6-9) are mostly independent of each other once the API is ready.
Step 10 depends on everything else.

## Key Decisions to Confirm During Implementation

- Lakebase connection string (need from workspace admin or service principal)
- SQL warehouse ID for Delta Table exports
- Whether to keep the existing BrowseVolumes component or simplify it
- Image thumbnail caching strategy (currently regenerated per request)
