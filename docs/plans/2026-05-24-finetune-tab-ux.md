# Finetuning Tab UX — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a dedicated Finetuning tab to the project dashboard that lets data scientists select an export, configure training parameters, launch a Databricks finetuning job, and monitor runs with key metrics — linking to MLflow for full details.

**Architecture:** The project dashboard gets a new "Finetuning" tab (alongside the existing single-page layout which becomes the "Overview" tab). The backend gains: (1) a new API to list available exports by scanning the export volume for `metadata.json` files, (2) an expanded `FinetuneRun` model with config columns (base_model, adapter_type, epochs, learning_rate) and metrics columns (mlflow_run_id, metrics_json), (3) a new list-runs endpoint, and (4) a metrics-sync endpoint. The frontend adds a `FinetuneTab` component with export picker, config form, run history table, and active run card.

**Tech Stack:** FastAPI (Python), SQLAlchemy, React (JSX), Databricks SDK (workspace client), MLflow (for metrics retrieval)

---

## Task 1: Backend — Extend FinetuneRun model with config + metrics columns

**Files:**
- Modify: `backend/models.py:172-192` (FinetuneRun class)
- Modify: `backend/models.py:220-228` (TABLE_NAMES — no change needed, finetune_runs already listed)

**Step 1: Add new columns to FinetuneRun model**

Add these columns after `export_path`:

```python
class FinetuneRun(Base):
    """Tracks async (Databricks Job) finetuning runs triggered after dataset export."""

    __tablename__ = "finetune_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("labeling_projects.id"), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    export_path = Column(Text, nullable=False)
    # --- NEW: training config ---
    base_model = Column(String(255), nullable=True)  # e.g. "facebook/sam-vit-large"
    adapter_type = Column(String(50), nullable=True)  # "lora", "full"
    epochs = Column(Integer, nullable=True)
    learning_rate = Column(Float, nullable=True)
    # --- NEW: results / metrics ---
    mlflow_run_id = Column(String(255), nullable=True)  # MLflow run ID for deep-link
    mlflow_experiment_id = Column(String(255), nullable=True)
    metrics_json = Column(JSON, nullable=True)  # {"loss": 0.12, "mAP": 0.85, ...}
    # --- existing ---
    databricks_run_id = Column(BigInteger, nullable=True)
    databricks_run_url = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_finetune_runs_project", "project_id"),
        Index("ix_finetune_runs_status", "status"),
    )
```

**Step 2: Verify the `_ensure_missing_columns` migration handles new columns**

The existing `_ensure_missing_columns()` in `models.py` automatically adds columns defined in models but missing from the DB. No changes needed — it will pick up the new columns on next deploy.

**Step 3: Commit**

```bash
git add backend/models.py
git commit -m "feat(finetune): add config and metrics columns to FinetuneRun model"
```

---

## Task 2: Backend — Update schemas for expanded finetune request/response

**Files:**
- Modify: `backend/schemas.py:270-287` (FinetuneTriggerRequest and FinetuneRunOut)

**Step 1: Expand FinetuneTriggerRequest with config fields**

```python
# ---------------------------------------------------------------------------
# Finetuning
# ---------------------------------------------------------------------------
class FinetuneTriggerRequest(BaseModel):
    export_path: str
    base_model: Optional[str] = None  # e.g. "facebook/sam-vit-large"
    adapter_type: Optional[str] = None  # "lora" | "full"
    epochs: Optional[int] = None
    learning_rate: Optional[float] = None


class FinetuneRunOut(BaseModel):
    id: int
    project_id: int
    status: str
    export_path: str
    base_model: Optional[str] = None
    adapter_type: Optional[str] = None
    epochs: Optional[int] = None
    learning_rate: Optional[float] = None
    mlflow_run_id: Optional[str] = None
    mlflow_experiment_id: Optional[str] = None
    metrics_json: Optional[dict] = None
    databricks_run_id: Optional[int] = None
    databricks_run_url: Optional[str] = None
    error_message: Optional[str] = None
    created_by: str = ""
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
```

**Step 2: Add ExportInfo schema for the list-exports endpoint**

Add after FinetuneRunOut:

```python
class ExportInfo(BaseModel):
    """Metadata about an available export in the export volume."""
    export_path: str
    project_name: str
    version: int
    task_type: str
    class_list: list[str]
    image_count: int
    annotation_count: int
    exported_at: str
    exported_by: str
    format: str
```

**Step 3: Commit**

```bash
git add backend/schemas.py
git commit -m "feat(finetune): expand schemas with config fields and ExportInfo"
```

---

## Task 3: Backend — List available exports API

**Files:**
- Create: `backend/routes/exports_list.py`
- Modify: `backend/main.py:18` (add import)
- Modify: `backend/main.py:132` (register router)

**Step 1: Create the list-exports route**

Create `backend/routes/exports_list.py`:

```python
"""List available exports for a project (scans the export volume)."""

import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..deps import get_db
from ..job_utils import get_project_or_404
from ..models import LabelingProject
from ..schemas import ExportInfo
from ..volumes import _get_workspace_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["exports"])


@router.get("/exports", response_model=list[ExportInfo])
def list_exports(project_id: int, db: Session = Depends(get_db)):
    """List exports available for this project by scanning the export volume."""
    project = get_project_or_404(project_id, db, LabelingProject)

    export_volume = os.environ.get("EXPORT_VOLUME_PATH", "").strip().rstrip("/")
    if not export_volume:
        return []

    w = _get_workspace_client()
    results = []

    try:
        entries = list(w.files.list_directory_contents(export_volume + "/"))
    except Exception as e:
        log.warning("Could not list export volume %s: %s", export_volume, e)
        return []

    for entry in entries:
        if not entry.is_directory:
            continue
        meta_path = f"{export_volume}/{entry.name}/metadata.json"
        try:
            resp = w.files.download(meta_path)
            content = resp.contents.read()
            meta = json.loads(content)
        except Exception:
            continue

        # Only include exports belonging to this project
        if meta.get("project_id") != project_id:
            continue

        results.append(ExportInfo(
            export_path=f"{export_volume}/{entry.name}",
            project_name=meta.get("project_name", ""),
            version=meta.get("version", 1),
            task_type=meta.get("task_type", ""),
            class_list=meta.get("class_list", []),
            image_count=meta.get("image_count", 0),
            annotation_count=meta.get("annotation_count", 0),
            exported_at=meta.get("exported_at", ""),
            exported_by=meta.get("exported_by", ""),
            format=meta.get("format", ""),
        ))

    # Sort newest first
    results.sort(key=lambda x: x.exported_at, reverse=True)
    return results
```

**Step 2: Register the router in main.py**

In `backend/main.py`, add to the imports line (line 18):

```python
from .routes import projects, labeling, admin, export, browse, import_routes, inference, preannotate_runs, finetune_runs, similarity, exports_list
```

And after `app.include_router(similarity.router)` (line 133):

```python
app.include_router(exports_list.router)
```

**Step 3: Commit**

```bash
git add backend/routes/exports_list.py backend/main.py
git commit -m "feat(finetune): add list-exports API endpoint"
```

---

## Task 4: Backend — Expand finetune route to accept config params + add list-runs

**Files:**
- Modify: `backend/routes/finetune_runs.py`
- Modify: `backend/finetune_triggers.py`

**Step 1: Update the trigger route to store config params**

In `backend/routes/finetune_runs.py`, update the `trigger_finetune` function to save config from the request body:

After line 78 (`created_by=user_email,`), add:

```python
    run_row = FinetuneRun(
        project_id=project_id,
        status="submitting",
        export_path=export_path,
        base_model=body.base_model,
        adapter_type=body.adapter_type,
        epochs=body.epochs,
        learning_rate=body.learning_rate,
        created_by=user_email,
    )
```

**Step 2: Pass config params to the Databricks job**

In `backend/finetune_triggers.py`, update `trigger_finetune_job` to accept and forward config:

```python
def trigger_finetune_job(
    run_db_id: int,
    export_path: str,
    base_model: str | None = None,
    adapter_type: str | None = None,
    epochs: int | None = None,
    learning_rate: float | None = None,
) -> int:
    """Kick off the configured finetuning job. Returns the Databricks run id."""
    job_id = resolve_finetune_job_id()
    if job_id is None:
        raise RuntimeError(
            "Finetuning job is not configured. Set FINETUNE_DATABRICKS_JOB_ID "
            "to the numeric Databricks job id."
        )

    job_params = {
        "run_id": str(run_db_id),
        "export_path": export_path,
    }
    if base_model:
        job_params["base_model"] = base_model
    if adapter_type:
        job_params["adapter_type"] = adapter_type
    if epochs is not None:
        job_params["epochs"] = str(epochs)
    if learning_rate is not None:
        job_params["learning_rate"] = str(learning_rate)

    drid = trigger_databricks_job(
        job_id, job_params, idempotency_token=f"finetune-{run_db_id}"
    )
    log.info("Submitted finetune job_id=%s run_id=%s for db_run_id=%s", job_id, drid, run_db_id)
    return drid
```

**Step 3: Update the call site in finetune_runs.py**

Change line 87:

```python
        drid = trigger_finetune_job(
            run_row.id,
            export_path,
            base_model=body.base_model,
            adapter_type=body.adapter_type,
            epochs=body.epochs,
            learning_rate=body.learning_rate,
        )
```

**Step 4: Add a list-all-runs endpoint**

Add to `backend/routes/finetune_runs.py`:

```python
@router.get("/finetune-runs", response_model=list[FinetuneRunOut])
def list_finetune_runs(project_id: int, db: Session = Depends(get_db)):
    """List all finetune runs for a project, newest first."""
    get_project_or_404(project_id, db, LabelingProject)
    rows = (
        db.query(FinetuneRun)
        .filter_by(project_id=project_id)
        .order_by(FinetuneRun.id.desc())
        .limit(50)
        .all()
    )
    # Sync active runs
    for row in rows:
        sync_run_status(row, db)
    return [FinetuneRunOut.model_validate(row) for row in rows]
```

**Step 5: Commit**

```bash
git add backend/routes/finetune_runs.py backend/finetune_triggers.py
git commit -m "feat(finetune): pass config params to job, add list-runs endpoint"
```

---

## Task 5: Backend — Add /api/config fields for finetune defaults

**Files:**
- Modify: `backend/main.py:144-157` (app_config endpoint)

**Step 1: Extend /api/config with finetune defaults**

Update the `app_config()` endpoint to include available base models and default config:

```python
@app.get("/api/config")
def app_config():
    """Public app configuration exposed to the frontend."""
    from .deps import is_lakebase
    from .finetune_triggers import resolve_finetune_job_id
    export_vol = os.environ.get("EXPORT_VOLUME_PATH", "")
    if not export_vol:
        export_vol = os.environ.get("DEMO_VOLUME_PATH", "")

    # Finetune defaults from env (or hardcoded sensible defaults)
    finetune_base_models = os.environ.get(
        "FINETUNE_BASE_MODELS",
        "facebook/sam-vit-large,facebook/sam-vit-base,facebook/sam-vit-huge"
    ).split(",")

    return {
        "demo_volume_path": os.environ.get("DEMO_VOLUME_PATH", ""),
        "export_volume_path": export_vol,
        "finetune_job_configured": resolve_finetune_job_id() is not None,
        "finetune_base_models": [m.strip() for m in finetune_base_models if m.strip()],
        "finetune_default_epochs": int(os.environ.get("FINETUNE_DEFAULT_EPOCHS", "10")),
        "finetune_default_lr": float(os.environ.get("FINETUNE_DEFAULT_LR", "0.0001")),
        "db_backend": "lakebase" if is_lakebase() else "sqlite",
    }
```

**Step 2: Commit**

```bash
git add backend/main.py
git commit -m "feat(finetune): expose finetune defaults in /api/config"
```

---

## Task 6: Frontend — Add API client functions for new endpoints

**Files:**
- Modify: `frontend/src/api/client.js`

**Step 1: Add new API functions**

After the existing finetune functions (line 194), add:

```javascript
export const listExports = (projectId) =>
  api.get(`/projects/${projectId}/exports`).then(r => r.data);

export const listFinetuneRuns = (projectId) =>
  api.get(`/projects/${projectId}/finetune-runs`).then(r => r.data);

export const triggerFinetuneWithConfig = (projectId, payload) =>
  api.post(`/projects/${projectId}/finetune`, payload, { timeout: 60000 }).then(r => r.data);
```

**Step 2: Commit**

```bash
git add frontend/src/api/client.js
git commit -m "feat(finetune): add frontend API functions for exports and finetune runs"
```

---

## Task 7: Frontend — Create FinetuneTab component

**Files:**
- Create: `frontend/src/components/FinetuneTab.jsx`

**Step 1: Build the FinetuneTab component**

This component contains:
1. Export picker (select from available exports)
2. Configuration form (base model dropdown, adapter type toggle, epochs, learning rate)
3. Launch button
4. Active run status card with polling
5. Run history table

```jsx
import { useState, useEffect, useRef } from 'react';
import {
  listExports,
  listFinetuneRuns,
  triggerFinetuneWithConfig,
  fetchFinetuneRun,
} from '../api/client';
import { humanizeApiError } from '../api/errors';
import Spinner from './Spinner';

export default function FinetuneTab({ projectId, appConfig }) {
  // Export list
  const [exports, setExports] = useState([]);
  const [exportsLoading, setExportsLoading] = useState(true);
  const [selectedExport, setSelectedExport] = useState('');

  // Config
  const baseModels = appConfig?.finetune_base_models || ['facebook/sam-vit-large'];
  const [baseModel, setBaseModel] = useState(baseModels[0] || '');
  const [adapterType, setAdapterType] = useState('lora');
  const [epochs, setEpochs] = useState(appConfig?.finetune_default_epochs || 10);
  const [learningRate, setLearningRate] = useState(appConfig?.finetune_default_lr || 0.0001);

  // Run state
  const [runs, setRuns] = useState([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const pollRef = useRef(null);

  // Load exports and runs on mount
  useEffect(() => {
    loadExports();
    loadRuns();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [projectId]);

  const loadExports = async () => {
    setExportsLoading(true);
    try {
      const data = await listExports(projectId);
      setExports(data);
      if (data.length > 0) setSelectedExport(data[0].export_path);
    } catch (e) {
      console.error('Failed to load exports', e);
    }
    setExportsLoading(false);
  };

  const loadRuns = async () => {
    setRunsLoading(true);
    try {
      const data = await listFinetuneRuns(projectId);
      setRuns(data);
      // Start polling if any run is active
      const active = data.find(r => ['submitting', 'queued', 'running'].includes(r.status));
      if (active) startPolling();
    } catch (e) {
      console.error('Failed to load runs', e);
    }
    setRunsLoading(false);
  };

  const startPolling = () => {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      try {
        const data = await listFinetuneRuns(projectId);
        setRuns(data);
        const active = data.find(r => ['submitting', 'queued', 'running'].includes(r.status));
        if (!active) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch (e) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }, 5000);
  };

  const handleLaunch = async () => {
    if (!selectedExport) return;
    setSubmitting(true);
    setError('');
    try {
      const payload = {
        export_path: selectedExport,
        base_model: baseModel || null,
        adapter_type: adapterType,
        epochs: epochs || null,
        learning_rate: learningRate || null,
      };
      await triggerFinetuneWithConfig(projectId, payload);
      await loadRuns();
      startPolling();
    } catch (e) {
      setError(humanizeApiError(e));
    }
    setSubmitting(false);
  };

  const activeRun = runs.find(r => ['submitting', 'queued', 'running'].includes(r.status));
  const databricksHost = appConfig?.databricks_host || '';

  const statusColor = (status) => {
    if (status === 'succeeded') return '#10b981';
    if (status === 'failed' || status === 'cancelled') return '#ef4444';
    if (['queued', 'running', 'submitting'].includes(status)) return '#f59e0b';
    return 'var(--text-muted)';
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {/* Active Run Banner */}
      {activeRun && (
        <div className="card" style={{
          borderLeft: '4px solid #f59e0b',
          background: 'rgba(245,158,11,0.05)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
            <Spinner size={14} />
            <span style={{ fontWeight: 600 }}>Active Run #{activeRun.id}</span>
            <span style={{ color: statusColor(activeRun.status), fontWeight: 500, fontSize: '0.85rem' }}>
              {activeRun.status}
            </span>
          </div>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
            {activeRun.base_model && <span>Model: {activeRun.base_model} · </span>}
            {activeRun.adapter_type && <span>Adapter: {activeRun.adapter_type} · </span>}
            {activeRun.epochs && <span>Epochs: {activeRun.epochs}</span>}
          </div>
          {activeRun.databricks_run_url && (
            <a href={activeRun.databricks_run_url} target="_blank" rel="noreferrer"
              style={{ fontSize: '0.8rem', marginTop: '0.25rem', display: 'inline-block' }}>
              View in Databricks ↗
            </a>
          )}
        </div>
      )}

      {/* Launch Section */}
      <div className="card">
        <h3 style={{ fontWeight: 600, fontSize: '1rem', margin: '0 0 1rem 0' }}>
          Launch Finetuning Run
        </h3>

        {/* Export Picker */}
        <div style={{ marginBottom: '1rem' }}>
          <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.25rem' }}>
            Export Dataset
          </label>
          {exportsLoading ? (
            <Spinner size={14} />
          ) : exports.length === 0 ? (
            <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', padding: '0.5rem', background: 'var(--bg-secondary)', borderRadius: 6 }}>
              No exports available. Export a labeled dataset first from the Actions menu.
            </div>
          ) : (
            <select
              value={selectedExport}
              onChange={(e) => setSelectedExport(e.target.value)}
              style={{ width: '100%', padding: '0.5rem', borderRadius: 6, border: '1px solid var(--border-color)', fontSize: '0.85rem' }}
            >
              {exports.map((exp) => (
                <option key={exp.export_path} value={exp.export_path}>
                  {exp.project_name} v{exp.version} — {exp.image_count} images, {exp.format} ({exp.exported_at.slice(0, 10)})
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Config Grid */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginBottom: '1rem' }}>
          <div>
            <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.25rem' }}>
              Base Model
            </label>
            <select
              value={baseModel}
              onChange={(e) => setBaseModel(e.target.value)}
              style={{ width: '100%', padding: '0.5rem', borderRadius: 6, border: '1px solid var(--border-color)', fontSize: '0.85rem' }}
            >
              {baseModels.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.25rem' }}>
              Adapter Type
            </label>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              {['lora', 'full'].map((t) => (
                <button
                  key={t}
                  onClick={() => setAdapterType(t)}
                  style={{
                    flex: 1,
                    padding: '0.5rem',
                    borderRadius: 6,
                    border: `1px solid ${adapterType === t ? 'var(--accent-blue)' : 'var(--border-color)'}`,
                    background: adapterType === t ? 'rgba(59,130,246,0.1)' : 'var(--bg-primary)',
                    color: adapterType === t ? 'var(--accent-blue)' : 'var(--text-secondary)',
                    fontWeight: 600,
                    fontSize: '0.85rem',
                    cursor: 'pointer',
                    textTransform: 'uppercase',
                  }}
                >
                  {t === 'lora' ? 'LoRA' : 'Full'}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.25rem' }}>
              Epochs
            </label>
            <input
              type="number"
              min="1"
              max="100"
              value={epochs}
              onChange={(e) => setEpochs(parseInt(e.target.value) || 10)}
              style={{ width: '100%', padding: '0.5rem', borderRadius: 6, border: '1px solid var(--border-color)', fontSize: '0.85rem' }}
            />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: '0.8rem', fontWeight: 600, marginBottom: '0.25rem' }}>
              Learning Rate
            </label>
            <input
              type="number"
              step="0.00001"
              min="0.000001"
              max="1"
              value={learningRate}
              onChange={(e) => setLearningRate(parseFloat(e.target.value) || 0.0001)}
              style={{ width: '100%', padding: '0.5rem', borderRadius: 6, border: '1px solid var(--border-color)', fontSize: '0.85rem' }}
            />
          </div>
        </div>

        {error && (
          <div style={{ marginBottom: '0.75rem', padding: '0.5rem 0.75rem', borderRadius: 6, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: '#ef4444', fontSize: '0.8rem' }}>
            {error}
          </div>
        )}

        <button
          className="btn-primary"
          onClick={handleLaunch}
          disabled={submitting || !selectedExport || !!activeRun}
          style={{ padding: '0.6rem 1.5rem' }}
        >
          {submitting ? 'Submitting...' : activeRun ? 'Run in Progress...' : 'Launch Finetuning'}
        </button>
        {activeRun && (
          <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginLeft: '0.75rem' }}>
            Wait for the current run to finish before launching another.
          </span>
        )}
      </div>

      {/* Run History */}
      <div className="card">
        <h3 style={{ fontWeight: 600, fontSize: '1rem', margin: '0 0 0.75rem 0' }}>
          Run History
        </h3>
        {runsLoading ? (
          <Spinner size={14} />
        ) : runs.length === 0 ? (
          <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            No finetuning runs yet.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', fontSize: '0.8rem', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-color)' }}>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Run</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Status</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Model</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Adapter</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Epochs</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>LR</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Metrics</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Date</th>
                  <th style={{ textAlign: 'left', padding: '0.4rem 0.5rem', fontWeight: 600 }}>Links</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id} style={{ borderBottom: '1px solid var(--border-color)' }}>
                    <td style={{ padding: '0.4rem 0.5rem' }}>#{run.id}</td>
                    <td style={{ padding: '0.4rem 0.5rem' }}>
                      <span style={{ color: statusColor(run.status), fontWeight: 500 }}>
                        {run.status}
                      </span>
                    </td>
                    <td style={{ padding: '0.4rem 0.5rem' }}>{run.base_model || '—'}</td>
                    <td style={{ padding: '0.4rem 0.5rem' }}>{run.adapter_type || '—'}</td>
                    <td style={{ padding: '0.4rem 0.5rem' }}>{run.epochs || '—'}</td>
                    <td style={{ padding: '0.4rem 0.5rem' }}>{run.learning_rate || '—'}</td>
                    <td style={{ padding: '0.4rem 0.5rem' }}>
                      {run.metrics_json ? (
                        <span title={JSON.stringify(run.metrics_json)}>
                          {Object.entries(run.metrics_json).slice(0, 2).map(([k, v]) =>
                            `${k}: ${typeof v === 'number' ? v.toFixed(4) : v}`
                          ).join(', ')}
                        </span>
                      ) : '—'}
                    </td>
                    <td style={{ padding: '0.4rem 0.5rem' }}>
                      {run.created_at ? new Date(run.created_at).toLocaleDateString() : '—'}
                    </td>
                    <td style={{ padding: '0.4rem 0.5rem' }}>
                      {run.databricks_run_url && (
                        <a href={run.databricks_run_url} target="_blank" rel="noreferrer" style={{ marginRight: '0.5rem' }}>
                          Job ↗
                        </a>
                      )}
                      {run.mlflow_run_id && (
                        <a href={`${databricksHost}/#mlflow/experiments/${run.mlflow_experiment_id || ''}/runs/${run.mlflow_run_id}`}
                          target="_blank" rel="noreferrer">
                          MLflow ↗
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
```

**Step 2: Commit**

```bash
git add frontend/src/components/FinetuneTab.jsx
git commit -m "feat(finetune): create FinetuneTab React component"
```

---

## Task 8: Frontend — Add tab navigation to ProjectDashboard

**Files:**
- Modify: `frontend/src/pages/ProjectDashboard.jsx`

**Step 1: Add tab state and import FinetuneTab**

At the top of ProjectDashboard.jsx, add to imports:

```javascript
import FinetuneTab from '../components/FinetuneTab';
```

Add a state variable for active tab (after existing state declarations, around line 52):

```javascript
const [activeTab, setActiveTab] = useState('overview');
```

**Step 2: Add a tab bar in the render, before the AI Tools panel**

Insert a tab bar between the header section (with "Start Labeling" button) and the AI Tools panel. Around line 901 (after the closing `</div>` of the header block):

```jsx
      {/* Tab Navigation */}
      <div style={{
        display: 'flex',
        gap: '0',
        borderBottom: '2px solid var(--border-color)',
        marginBottom: '1rem',
      }}>
        {['overview', 'finetuning'].map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: '0.6rem 1.25rem',
              fontSize: '0.85rem',
              fontWeight: 600,
              border: 'none',
              borderBottom: activeTab === tab ? '2px solid var(--accent-blue)' : '2px solid transparent',
              marginBottom: '-2px',
              background: 'transparent',
              color: activeTab === tab ? 'var(--accent-blue)' : 'var(--text-secondary)',
              cursor: 'pointer',
              textTransform: 'capitalize',
            }}
          >
            {tab === 'finetuning' ? '🔧 Finetuning' : '📊 Overview'}
          </button>
        ))}
      </div>
```

**Step 3: Wrap existing content in a conditional for the "overview" tab**

Wrap the existing dashboard content (AI Tools panel, Analytics, Gallery — everything after the tab bar) in:

```jsx
      {activeTab === 'overview' && (
        <>
          {/* ... all existing content ... */}
        </>
      )}

      {activeTab === 'finetuning' && (
        <FinetuneTab projectId={projectId} appConfig={appConfig} />
      )}
```

Where `appConfig` is the config object already fetched in the existing code. Add state for it if not already available at component level. The existing code already fetches `fetchAppConfig()` in a useEffect — just store the result in a state variable (`appConfig`).

Look at the existing code around line 181:

```javascript
        setFinetuneConfigured(!!cfg.finetune_job_configured);
```

The `cfg` from `fetchAppConfig()` needs to be stored. Add state:

```javascript
const [appConfig, setAppConfig] = useState(null);
```

And in the effect where fetchAppConfig is called, add:

```javascript
        setAppConfig(cfg);
```

**Step 4: Only show the finetuning tab if finetune is configured**

Modify the tab list to conditionally include finetuning:

```jsx
        {['overview', ...(finetuneConfigured ? ['finetuning'] : [])].map((tab) => (
```

**Step 5: Commit**

```bash
git add frontend/src/pages/ProjectDashboard.jsx
git commit -m "feat(finetune): add tab navigation with dedicated Finetuning tab"
```

---

## Task 9: Frontend — Build and verify

**Step 1: Install dependencies (if needed) and build**

```bash
cd /workspace/group/cv-react-deploy/frontend
npm install
npm run build
```

Expected: Build succeeds with no errors.

**Step 2: Fix any build errors**

Address any TypeScript/lint errors from the build.

**Step 3: Commit build output**

```bash
git add frontend/dist/
git commit -m "chore: rebuild frontend with finetuning tab"
```

---

## Task 10: Backend — Add routes/__init__.py entry for exports_list

**Files:**
- Modify: `backend/routes/__init__.py`

**Step 1: Check and update routes/__init__.py**

Ensure `exports_list` is importable. Check if there's an `__all__` or explicit imports in the init file — if so, add `exports_list`.

**Step 2: Commit if changed**

```bash
git add backend/routes/__init__.py
git commit -m "chore: register exports_list in routes package"
```

---

## Summary of Changes

| Layer | Change | Purpose |
|-------|--------|---------|
| Backend model | Add columns: base_model, adapter_type, epochs, learning_rate, mlflow_run_id, mlflow_experiment_id, metrics_json | Store training config and results |
| Backend schemas | Expand FinetuneTriggerRequest and FinetuneRunOut, add ExportInfo | API contracts |
| Backend routes | New `GET /api/projects/{id}/exports` | List available exports |
| Backend routes | New `GET /api/projects/{id}/finetune-runs` | List all runs |
| Backend routes | Expand `POST /api/projects/{id}/finetune` | Accept config params |
| Backend triggers | Pass config to Databricks job | Job receives training config |
| Backend config | Add finetune_base_models, default_epochs, default_lr | Frontend defaults |
| Frontend API | Add listExports, listFinetuneRuns, triggerFinetuneWithConfig | API layer |
| Frontend component | New FinetuneTab.jsx | Full finetuning UI |
| Frontend dashboard | Tab navigation (Overview / Finetuning) | UX entry point |

## Future Enhancements (Not in this plan)

- Metrics sync: After a run completes, poll MLflow API and backfill `metrics_json` + `mlflow_run_id`
- Run comparison view: side-by-side metric comparison of two runs
- "Promote" button: tag a run as the best model
- Webhook/callback from finetune job to push metrics back to the app
