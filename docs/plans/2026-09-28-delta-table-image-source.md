# Delta table as image source — Implementation Plan

**Goal:** Projects can be created from a Unity Catalog Delta table (`image_path` + optional filter) as well as from a UC Volume. Each project stores its own table FQN. Image bytes still come from the Files API.

**Design:** `docs/plans/2026-09-28-delta-table-image-source-design.md`

**Out of scope for this plan:** BINARY-in-Delta serving; Catalog lineage training job (v1.1); recursive volume scan.

---

## Task 1: Model + schemas

**Files:** `backend/models.py`, `backend/schemas.py`

1. On `LabelingProject` add:
   - `source_type = Column(String(20), nullable=False, default="volume")`
   - `source_table = Column(Text, nullable=True)`
   - `image_path_column = Column(String(255), nullable=True)`
   - `source_filter = Column(Text, nullable=True)`
2. Extend `ProjectCreate`, `ProjectUpdate`, `ProjectOut` with the same fields (`source_type` default `"volume"`).
3. Pydantic: if `source_type == "table"`, require `source_table`; if `"volume"`, require `source_volume`. Reject unknown `source_type`.

Startup `_ensure_columns` will ALTER existing Lakebase/SQLite tables. No separate migration file.

**Verify:** new columns appear in `ProjectOut` from `_project_out`; existing tests that omit the new fields still create volume projects.

---

## Task 2: Warehouse helper + table scan

**Files (new):** `backend/uc_tables.py`  
**Files (modify):** none yet except tests

1. `parse_table_fqn(name) -> (catalog, schema, table)` — three non-empty parts, `[A-Za-z0-9_]+` (backticks allowed then stripped). Else ValueError.
2. `quote_uc_id(part)` — backtick-wrap for Spark SQL.
3. `validate_source_filter(sql)` — reject `;`, `--`, `/*`, empty statements that are not a WHERE body. Allow simple predicates only (keep the check conservative).
4. `validate_image_path(path)` — `/Volumes/` prefix, no `..`, no `\`.
5. `scan_table_for_samples(db, project_id, full_name, path_column, source_filter) -> int`:
   - Read `SQL_WAREHOUSE_ID` (or `DATABRICKS_WAREHOUSE_ID`) from env; missing → HTTPException 400 from the route, or raise a typed error the route maps.
   - `WorkspaceClient().statement_execution.execute_statement(warehouse_id=..., statement=..., wait_timeout="50s")`.
   - Paginate `result.data_array` / `next_chunk` if needed until cap.
   - Insert `ProjectSample` rows; return count.
   - Honor `TABLE_SOURCE_MAX_ROWS` (default 50000).
6. Derive volume hint: first path’s `/Volumes/a/b/c`.

Do **not** SELECT `*` or BINARY columns.

**Tests:** `backend/tests/test_uc_tables.py` — FQN parse, filter reject, path validate, scan with a mocked statement_execution returning two `/Volumes/...` rows → two samples; invalid path → error; duplicate paths → one sample.

---

## Task 3: Browse endpoints

**Files:** `backend/routes/browse.py`, `frontend/src/api/client.js`

1. `GET /api/tables?catalog=&schema=` → sorted table names via `w.tables.list`.
2. `GET /api/tables/preview?full_name=` → `{ columns: [{name, type_name}], row_count }` using `tables.get` for columns and `SELECT COUNT(*) FROM ...` with the same warehouse (COUNT may be slow; document timeout). If warehouse missing, return columns only and `row_count: null`.

**Tests:** mock SDK list/get; 400 on bad FQN.

---

## Task 4: Project create / update / clone

**Files:** `backend/routes/projects.py`

1. `_project_out` pass through new fields.
2. `create_project`:
   - `source_type = payload.source_type or "volume"`
   - volume → `scan_volume_for_samples` as today
   - table → `scan_table_for_samples`; set `source_volume` from first path if payload left it blank
   - zero samples → 400 (today volume can create an empty project; **match volume behavior**: allow empty but log warning — actually volume currently allows 0. Keep allow-0 for empty tables so dry catalogs don’t block. Document it.)
3. `update_project`: treat table identity change (`source_table`, `image_path_column`, `source_filter`) like volume change: require `confirm_source_change`, delete samples+annotations, re-scan.
4. `clone_project`: copy the four new columns.

**Tests:** extend an existing project test file or `test_projects_table_source.py` with mocked scan; PATCH without confirm → 400; clone copies FQN.

---

## Task 5: Import + export path checks

**Files:** `backend/routes/import_routes.py`, `backend/dataset_exports.py`, `backend/routes/export.py`

1. `_is_source_image_path`: `True` if `is_volume_path(path)` (and existing local-test exception). For volume projects, **also** keep prefix check when `source_type == "volume"` so behavior does not loosen for current projects.
2. Import `on_missing_sample=create` for `source_type=table`: 400 explaining samples must already exist from the table scan (no basename invent). `error` / `skip` unchanged (basename match).
3. Export metadata `lineage` includes `source_type`, `source_table_uc`, `image_path_column`, `source_filter`.

**Tests:** update `test_export.py` lineage keys for a table project; import create-on-missing fails for table source.

---

## Task 6: Create Project + dashboard UI

**Files:** `frontend/src/pages/CreateProject.jsx`, `frontend/src/pages/ProjectDashboard.jsx`, `frontend/src/api/client.js`

1. Toggle Volume / Delta table (table disabled copy: “Requires SQL warehouse on the app”).
2. Table branch: catalog, schema, table selects; path column default `image_path`; optional filter.
3. `createProject({ ..., source_type, source_table, image_path_column, source_filter })`.
4. Dashboard: display table FQN; settings edit can change table with the same confirm checkbox as volume.

Do not remove the volume browser.

**Verify:** if browser tools are available, create-project form switch; otherwise note manual smoke. Existing volume submit payload must still send `source_volume` only (or `source_type: "volume"`).

---

## Task 7: App resource + env

**Files:** `app.yml`, `resources/cv_explorer_app.yml`, `databricks.yml` if warehouse var is needed

1. Add optional `SQL_WAREHOUSE_ID` env. Bundle: `sql_warehouse` app resource with `CAN_USE` if the Apps resource type is available in this workspace’s DAB schema; otherwise document setting the id in Apps UI / `app.yml`.
2. If DAB cannot declare the warehouse, land env-only and document in the design “Resources” section (already: grants on SP).

Do not hardcode a warehouse id in git.

---

## Task 8: Tests + docs pointer

1. Run `pytest backend/tests/test_uc_tables.py backend/tests/test_export.py backend/tests/test_import_endpoint.py` (and any new project tests).
2. Roadmap 5.5 already points here. No AGENTS.md change required until the feature ships (then one bullet under Key Patterns).

---

## v1.1 (not this PR)

Lakeflow job: `SELECT` project `source_table` ⋈ Lakehouse-synced annotations → `catalog.schema.labeled_dataset`. Optional `mlflow.register_model`. That is Catalog lineage, not the Apps request path.
