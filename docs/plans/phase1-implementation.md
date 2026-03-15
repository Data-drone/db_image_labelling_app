# Phase 1 Implementation Plan

Reference: [Phase 1 Design](../phase1-design.md)

## Prerequisites

Before starting implementation:
- [ ] Get Lakebase connection string from Databricks workspace
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
   - Add: `LabelingProject`, `ProjectSample`, `Annotation` models
   - Use `DATABASE_URL` env var pointing to Lakebase Postgres endpoint
   - Add `psycopg2-binary` to requirements
2. Update `backend/schemas.py`:
   - Remove old Pydantic schemas
   - Add: `ProjectCreate`, `ProjectOut`, `ProjectStats`
   - Add: `SampleOut`, `SamplePage`
   - Add: `AnnotationCreate`, `AnnotationOut`
3. Update `app.yaml`:
   - Change `DATABASE_URL` to Lakebase connection string
   - Remove `DB_BACKUP_VOLUME` env var

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
1. `POST /api/projects` — create project (name, task_type, class_list, source_volume)
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

## Step 5: Frontend — Projects List Page

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

## Step 6: Frontend — Create Project Page

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

## Step 7: Frontend — Labeling View (Rewrite)

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

## Step 8: Frontend — Project Dashboard

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

## Step 9: Lakehouse Sync Setup & Cleanup

**Goal:** Configure sync, remove dead code, test end-to-end, deploy.

**Tasks:**
1. Configure Lakehouse Sync in Databricks workspace:
   - `ALTER TABLE labeling_projects REPLICA IDENTITY FULL;`
   - `ALTER TABLE project_samples REPLICA IDENTITY FULL;`
   - `ALTER TABLE annotations REPLICA IDENTITY FULL;`
   - Enable sync in Lakebase UI → choose destination catalog/schema
2. Remove unused files:
   - `frontend/src/pages/DatasetExplorer.jsx`
   - `frontend/src/pages/SearchPage.jsx`
   - `frontend/src/components/DatasetSelector.jsx`
   - `frontend/src/components/Pagination.jsx` (if not reused)
3. Remove unused backend endpoints (old dataset/sample/tag CRUD)
4. Update `start.py` if needed
5. Build frontend: `npm run build`
6. Deploy to Databricks Apps
7. End-to-end test:
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
Step 2 (models) ────────────────────────────────────────────────────────┤
Step 3 (project API) ──────────────────┬───────────────────────────────┤
Step 4 (labeling API) ─────────────────┘                               │
                                       │                               │
Step 5 (projects list page) ───────────┼── depends on Step 3 API ──────┤
Step 6 (create project page) ──────────┤                               │
Step 7 (labeling view) ────────────────┼── depends on Step 4 API ──────┤
Step 8 (project dashboard) ────────────┤                               │
                                       │                               │
Step 9 (sync setup & deploy) ──────────┴───────────────────────────────┘
```

Backend steps (2-4) can be done before frontend steps (5-8).
Frontend steps (5-8) are mostly independent of each other once the API is ready.
Step 9 depends on everything else.

## Key Decisions to Confirm During Implementation

- Lakebase connection string (need from workspace admin or service principal)
- Destination UC catalog/schema for Lakehouse Sync Delta tables
- Whether to keep the existing BrowseVolumes component or simplify it
- Image thumbnail caching strategy (currently regenerated per request)
