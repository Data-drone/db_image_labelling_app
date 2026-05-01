# AGENTS.md — CV Explorer

## What This Is

Image labelling app deployed as a **Databricks App**. FastAPI backend + React SPA frontend. Stores metadata in **Lakebase** (managed Postgres) with SQLite fallback. Images live on **UC Volumes** — the app reads bytes on demand, never stores blobs in the database.

## Architecture

```
Browser (React SPA)
  → FastAPI (/api/*)
    → SQLAlchemy (Lakebase or SQLite)
    → Databricks SDK (UC Volumes for images, Lakebase for provisioning)
```

- `start.py` — entry point, runs Uvicorn on `DATABRICKS_APP_PORT` (default 8000)
- `backend/main.py` — app assembly: routers, CORS, lifespan startup, SPA static serving
- `backend/models.py` — SQLAlchemy models (`labeling_projects`, `project_samples`, `annotations`, `preannotate_runs`)
- `backend/schemas.py` — Pydantic request/response schemas
- `backend/deps.py` — shared dependencies (`get_db`, `get_user_email`)
- `backend/lakebase.py` — Lakebase: SDK auto-provision + token refresh, or **App postgres resource** (`PG*` env, no refresh)
- `backend/volumes.py` — UC Volume I/O (`read_image_bytes`, `scan_volume_for_samples`)
- `backend/routes/` — one file per domain: `projects`, `labeling`, `inference`, `preannotate_runs`, `export`, `browse`, `admin`
- `databricks.yml` + `resources/*.yml` — DAB bundle: app (`cv_explorer_app.yml` with UC/Lakebase/serving/job resources), pre-annotate job
- `scripts/preannotate_job.py` — cluster entrypoint for that job (`argv[1]` = `preannotate_runs.id`)
- `frontend/src/App.jsx` — React Router with routes: `/`, `/projects/new`, `/projects/:id`, `/projects/:id/label`, `/browse`, `/admin`
- `frontend/src/api/client.js` — all API calls (single source of truth for endpoints)

## Databricks Apps Constraints

### app.yml

Git-backed Databricks Apps expect **`app.yml`** at the repo root (not only `app.yaml`).

```yaml
command: ['python', 'start.py']
env:
  - name: DEMO_VOLUME_PATH
    valueFrom: demo-volume
```

- `command` is a sequence, not a string. No shell expansion — env vars besides `DATABRICKS_APP_PORT` won't interpolate.
- Use `env` for all configuration. Never hardcode secrets — use `valueFrom` referencing app resource **names** from the bundle (`resources/cv_explorer_app.yml`) or the Apps UI.
- App name must be ≤30 chars, lowercase letters/numbers/hyphens only (no underscores).

### Port Binding

The app **must** listen on `DATABRICKS_APP_PORT`. The runtime also auto-sets `UVICORN_PORT` and `UVICORN_HOST=0.0.0.0` for FastAPI/Uvicorn apps. Our `start.py` reads the port explicitly.

### Authentication & Identity

- The app's **service principal** credentials are injected as `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET`. The `WorkspaceClient()` picks these up automatically — never hardcode tokens.
- **User identity** comes from reverse-proxy headers: `X-Forwarded-Email`, `X-Forwarded-User`. See `deps.get_user_email()`. These headers do NOT exist in local dev — the app falls back to `"anonymous"`.
- There are **no in-app roles or permissions** — anyone with access to the Databricks App can do everything.

### Resources (UC Volumes, Lakebase)

Prefer **declarative resources** in `resources/cv_explorer_app.yml` (bundle deploy) so bindings show in the Apps UI. `app.yml` uses `valueFrom: <resource-name>` matching each resource’s `name` field.

Without a bundle, resources can be added in the Apps UI instead; keep the same `valueFrom` keys in `app.yml`.

**Lakebase:** either attach a **postgres** database App resource (platform injects `PG*`; app uses `init_lakebase_from_app_resource`) or omit it and use SDK auto-provision in `lakebase.py` (`LAKEBASE_AUTO_PROVISION=true`, no `PGHOST`).

### Runtime Environment

- Python 3.11, Ubuntu 22.04, 2 vCPUs / 6 GB RAM by default
- `uv` is available for dependency management
- No pre-installed pip packages — everything comes from `requirements.txt`

## How to Add Features

### New Backend Route

1. Create or edit a file in `backend/routes/`
2. Add Pydantic schemas to `backend/schemas.py`
3. Use `Depends(get_db)` for database sessions and `get_user_email(request)` for identity
4. Register the router in `backend/main.py` if it's a new file
5. All routes must be under `/api/` — the SPA catch-all at `/{path:path}` will swallow anything else

### New Frontend Page

1. Create a component in `frontend/src/pages/`
2. Add the route in `frontend/src/App.jsx`
3. Add API calls to `frontend/src/api/client.js` — this is the single source of truth for all endpoints
4. Build: `npm run build` in `frontend/` — output goes to `frontend/dist/` which FastAPI serves as static files

### Database Changes

- Add/modify models in `backend/models.py` using SQLAlchemy declarative style
- `Base.metadata.create_all()` runs on startup — new tables are created automatically
- For Lakehouse Sync compatibility, add `REPLICA IDENTITY FULL` (see `models.init_db`)
- Lakebase is Postgres — avoid SQLite-only syntax. Use `JSON` column type (works on both)
- Bounding boxes are stored as normalized `{"x", "y", "w", "h"}` in `[0, 1]` range in `bbox_json`

## Key Patterns

- **Images are never uploaded** — projects point at an existing UC Volume path. `scan_volume_for_samples` lists one directory level and creates `ProjectSample` rows.
- **Image serving** goes through the backend: `/api/projects/{id}/samples/{id}/image` reads bytes via SDK and streams them. Thumbnails are resized with Pillow.
- **Lakebase tokens expire** — a background thread in `lakebase.py` refreshes every 20 minutes with exponential backoff on failure.
- **Annotation model**: classification = one `Annotation` row per sample; detection = multiple rows with `bbox_json`. The `annotate-batch` endpoint replaces all annotations for a sample (delete + insert).
- **Project cloning** copies sample rows (same file paths) with empty annotations and bumps the version number.

## Common Pitfalls

- **SPA catch-all ordering**: The `/{path:path}` route in `main.py` must be registered last. New `/api/` routes in a router with `prefix="/api/..."` are fine, but bare routes will be masked.
- **Volume scanning is not recursive**: Only top-level files in the volume path are found. Nested directories are ignored.
- **Local dev without Databricks**: Set `USE_LAKEBASE=false` to fall back to SQLite. Image paths must be local (e.g., `/tmp/images/`). User identity will be `"anonymous"`.
- **CORS**: Defaults to `*` if `CORS_ORIGINS` env var is not set.
- **Lakebase Postgres username**: Derived from the JWT `sub` claim of the generated credential, not from listing roles. See `_get_pg_username` in `lakebase.py`.

## Existing Docs

- `docs/phase1-design.md` — original vision and data model
- `docs/plans/` — implementation plans and feature designs
- `docs/plans/2026-04-27-roadmap.md` — full roadmap (Phases 2–6)
