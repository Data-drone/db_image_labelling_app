# Finetune Integration Hardening Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix GPT-5.5 review blockers 2, 4, 5, 6, 7 on the finetuning integration (skip #1 already fixed, skip #3 authz deferred).

**Architecture:** Backend-only changes to `finetune_triggers.py`, `routes/finetune_runs.py`, `job_utils.py`, `models.py`, `schemas.py`. No frontend changes needed — the frontend already polls by run ID after trigger.

**Tech Stack:** FastAPI, SQLAlchemy 2, Pydantic v2, Databricks SDK (WorkspaceClient)

---

### Task 1: Validate export_path in finetune trigger (Blocker #2)

**Files:**
- Modify: `backend/routes/finetune_runs.py:20-63`

**Step 1: Add path validation to trigger_finetune**

The export route already validates paths start with `/Volumes/`. The finetune trigger should validate:
1. Path is a UC Volume path (starts with `/Volumes/`)
2. Path is under the configured `EXPORT_VOLUME_PATH` (env var)
3. No `..` segments

```python
# Add at top of file:
import os
from pathlib import PurePosixPath

# Add validation inside trigger_finetune, after export_path extraction:
    # Validate export_path
    if not export_path.startswith("/Volumes/"):
        raise HTTPException(status_code=400, detail="export_path must be a UC Volume path.")
    parts = PurePosixPath(export_path).parts
    if ".." in parts:
        raise HTTPException(status_code=400, detail="export_path must not contain '..' segments.")
    # Check path is under configured export volume
    allowed_prefix = os.environ.get("EXPORT_VOLUME_PATH", "").strip().rstrip("/")
    if allowed_prefix and not export_path.startswith(allowed_prefix):
        raise HTTPException(
            status_code=400,
            detail=f"export_path must be under the configured export volume ({allowed_prefix}).",
        )
```

**Step 2: Also add Pydantic request model instead of raw dict**

Replace `body: dict` with a proper schema:

```python
# In schemas.py, add:
class FinetuneTriggerRequest(BaseModel):
    export_path: str
```

Update route signature:
```python
from ..schemas import FinetuneTriggerRequest, FinetuneRunOut

def trigger_finetune(
    project_id: int,
    body: FinetuneTriggerRequest,
    ...
```

Then use `body.export_path` instead of `body.get("export_path")`.

**Step 3: Commit**

```bash
git add backend/routes/finetune_runs.py backend/schemas.py
git commit -m "fix(finetune): validate export_path against /Volumes/ and EXPORT_VOLUME_PATH"
```

---

### Task 2: Add active-run guard (Blocker #4)

**Files:**
- Modify: `backend/routes/finetune_runs.py`

**Step 1: Add duplicate-run check before submission**

```python
# Inside trigger_finetune, after project check:
    active = (
        db.query(FinetuneRun)
        .filter(
            FinetuneRun.project_id == project_id,
            FinetuneRun.status.in_(["pending", "queued", "running"]),
        )
        .first()
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"A finetune run is already active (run {active.id}, status={active.status}).",
        )
```

**Step 2: Commit**

```bash
git add backend/routes/finetune_runs.py
git commit -m "fix(finetune): reject duplicate submissions when run already active"
```

---

### Task 3: Prevent orphaned Databricks runs (Blocker #5)

**Files:**
- Modify: `backend/routes/finetune_runs.py`
- Modify: `backend/job_utils.py`

**Step 1: Restructure the submission to minimize orphan window**

Change `trigger_finetune` to use try/finally and capture run_id even on partial failure:

```python
    run_row = FinetuneRun(
        project_id=project_id,
        status="submitting",  # changed from "pending"
        export_path=export_path,
        created_by=user_email,
    )
    db.add(run_row)
    db.commit()
    db.refresh(run_row)

    try:
        drid = trigger_finetune_job(run_row.id, export_path)
    except Exception as e:
        log.exception("Failed to submit finetune job")
        run_row.status = "failed"
        run_row.error_message = str(e)[:4000]
        run_row.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=502, detail="Failed to submit finetuning job.") from e

    run_row.databricks_run_id = drid
    run_row.status = "queued"
    try:
        db.commit()
    except Exception:
        log.critical(
            "ORPHANED RUN: finetune run_id=%s submitted as databricks_run_id=%s but DB commit failed",
            run_row.id, drid,
        )
        db.rollback()
        raise
    db.refresh(run_row)
    return FinetuneRunOut.model_validate(run_row)
```

**Step 2: Add idempotency token to job submission**

In `backend/finetune_triggers.py`:

```python
def trigger_finetune_job(run_db_id: int, export_path: str) -> int:
    job_id = resolve_finetune_job_id()
    if job_id is None:
        raise RuntimeError(...)

    job_params = {
        "run_id": str(run_db_id),
        "export_path": export_path,
    }
    drid = trigger_databricks_job(job_id, job_params, idempotency_token=f"finetune-{run_db_id}")
    ...
```

In `backend/job_utils.py`, accept optional idempotency_token:

```python
def trigger_databricks_job(job_id: int, job_params: dict, idempotency_token: str | None = None) -> int:
    from .volumes import _get_workspace_client
    w = _get_workspace_client()
    kwargs = {"job_id": job_id, "job_parameters": job_params}
    if idempotency_token:
        kwargs["idempotency_token"] = idempotency_token
    resp = w.jobs.run_now(**kwargs)
    ...
```

**Step 3: Update active-run check to include "submitting" status**

```python
    FinetuneRun.status.in_(["pending", "submitting", "queued", "running"]),
```

**Step 4: Commit**

```bash
git add backend/routes/finetune_runs.py backend/finetune_triggers.py backend/job_utils.py
git commit -m "fix(finetune): add idempotency token and crash-safe submission flow"
```

---

### Task 4: Expand status mapping in sync_run_status (Blocker #6)

**Files:**
- Modify: `backend/job_utils.py:46-84`

**Step 1: Rewrite the status mapping block**

Replace the current if/elif chain with a more complete mapping:

```python
def sync_run_status(row, db: Session) -> None:
    """If a run row is non-terminal but the Databricks run has finished, update it."""
    if row.status in _TERMINAL_STATUSES or not row.databricks_run_id:
        return
    try:
        from .volumes import _get_workspace_client
        w = _get_workspace_client()
        run = w.jobs.get_run(run_id=row.databricks_run_id)
        state = run.state
        if not state:
            return
        lcs = str(getattr(state, "life_cycle_state", "") or "").upper()
        result = str(getattr(state, "result_state", "") or "").upper()
        msg = str(getattr(state, "state_message", "") or "")

        log.info(
            "Cross-check run %s (db_run=%s): life_cycle=%s result=%s msg=%.120s",
            row.id, row.databricks_run_id, lcs, result, msg,
        )

        # Terminal states
        if "SUCCESS" in result:
            row.status = "succeeded"
            row.finished_at = row.finished_at or datetime.now(timezone.utc)
            db.commit()
        elif "FAILED" in result or "TIMEDOUT" in result:
            row.status = "failed"
            row.error_message = (msg or f"Databricks run {lcs}/{result}")[:4000]
            row.finished_at = row.finished_at or datetime.now(timezone.utc)
            db.commit()
        elif "CANCEL" in result:
            row.status = "cancelled"
            row.finished_at = row.finished_at or datetime.now(timezone.utc)
            db.commit()
        elif "INTERNAL_ERROR" in lcs or "SKIPPED" in lcs or "BLOCKED" in lcs:
            row.status = "failed"
            row.error_message = (msg or f"Databricks lifecycle: {lcs}")[:4000]
            row.finished_at = row.finished_at or datetime.now(timezone.utc)
            db.commit()
        # Non-terminal states
        elif "RUNNING" in lcs or "TERMINATING" in lcs:
            if row.status != "running":
                row.status = "running"
                row.started_at = row.started_at or datetime.now(timezone.utc)
                db.commit()
        elif "PENDING" in lcs or "QUEUED" in lcs or "WAITING_FOR_RETRY" in lcs:
            if row.status not in ("queued", "running"):
                row.status = "queued"
                db.commit()
    except Exception:
        log.warning("Could not cross-check Databricks run %s", row.databricks_run_id, exc_info=True)
```

**Step 2: Add "submitting" to _TERMINAL_STATUSES exclusion check**

Already fine — `submitting` is not in `_TERMINAL_STATUSES` so sync will run on it, which is correct.

**Step 3: Commit**

```bash
git add backend/job_utils.py
git commit -m "fix(finetune): expand status mapping to handle TIMEDOUT, TERMINATED, WAITING_FOR_RETRY"
```

---

### Task 5: Add Databricks run URL to FinetuneRun (Blocker #7 — minimal)

**Files:**
- Modify: `backend/models.py` (add column)
- Modify: `backend/schemas.py` (add field)
- Modify: `backend/routes/finetune_runs.py` (populate URL after submission)

**Step 1: Add column to FinetuneRun model**

```python
class FinetuneRun(Base):
    ...
    databricks_run_id = Column(BigInteger, nullable=True)
    databricks_run_url = Column(Text, nullable=True)  # NEW
    error_message = Column(Text, nullable=True)
    ...
```

**Step 2: Add field to FinetuneRunOut schema**

```python
class FinetuneRunOut(BaseModel):
    ...
    databricks_run_id: Optional[int] = None
    databricks_run_url: Optional[str] = None  # NEW
    error_message: Optional[str] = None
    ...
```

**Step 3: Populate URL after submission**

In `trigger_finetune`, after setting `databricks_run_id`:

```python
    run_row.databricks_run_id = drid
    # Build run URL for troubleshooting
    host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
    job_id = resolve_finetune_job_id()
    if host and job_id:
        run_row.databricks_run_url = f"{host}/#job/{job_id}/run/{drid}"
    run_row.status = "queued"
```

**Step 4: Commit**

```bash
git add backend/models.py backend/schemas.py backend/routes/finetune_runs.py
git commit -m "feat(finetune): add databricks_run_url for troubleshooting"
```

---

### Task 6: Don't leak exception details (from review)

**Files:**
- Modify: `backend/routes/finetune_runs.py`

**Step 1: Already handled in Task 3** — the `raise HTTPException(status_code=502, detail="Failed to submit finetuning job.")` replaces the old `detail=str(e)`. Verify this is in place after Task 3.

---

## Summary of changes

| Blocker | Fix | Files |
|---------|-----|-------|
| #1 env var | Already fixed in `resources/cv_explorer_app.yml` | None |
| #2 path validation | Validate /Volumes/, EXPORT_VOLUME_PATH prefix, no `..` | finetune_runs.py, schemas.py |
| #4 duplicate runs | Active-run guard (409) | finetune_runs.py |
| #5 orphans | Idempotency token, crash-safe try/finally, CRITICAL log | finetune_runs.py, finetune_triggers.py, job_utils.py |
| #6 status mapping | Handle TIMEDOUT, TERMINATED, WAITING_FOR_RETRY, PENDING, QUEUED | job_utils.py |
| #7 tracking | databricks_run_url column + field | models.py, schemas.py, finetune_runs.py |

## Note on GitHub push

The PAT in the git remote has expired. These commits will be local only. Push manually after refreshing the token, or provide a new PAT.
