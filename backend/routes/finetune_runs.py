"""Finetuning job management — trigger after export, status polling."""

import logging
import os
from datetime import datetime, timezone
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..deps import get_db, get_user_email
from ..finetune_triggers import resolve_finetune_job_id, trigger_finetune_job
from ..job_utils import get_project_or_404, sync_run_status
from ..models import LabelingProject, FinetuneRun
from ..schemas import FinetuneTriggerRequest, FinetuneRunOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["finetune-jobs"])


def _validate_export_path(export_path: str) -> None:
    """Raise 400 if export_path is unsafe or outside allowed volume."""
    if not export_path.startswith("/Volumes/"):
        raise HTTPException(status_code=400, detail="export_path must be a UC Volume path.")
    parts = PurePosixPath(export_path).parts
    if ".." in parts:
        raise HTTPException(status_code=400, detail="export_path must not contain '..' segments.")
    allowed_prefix = os.environ.get("EXPORT_VOLUME_PATH", "").strip().rstrip("/")
    if allowed_prefix and not export_path.startswith(allowed_prefix):
        raise HTTPException(
            status_code=400,
            detail=f"export_path must be under the configured export volume ({allowed_prefix}).",
        )


@router.post("/finetune", response_model=FinetuneRunOut)
def trigger_finetune(
    project_id: int,
    body: FinetuneTriggerRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a finetune run row and trigger the Databricks Job."""
    if resolve_finetune_job_id() is None:
        raise HTTPException(
            status_code=503,
            detail="Finetuning is not configured (set FINETUNE_DATABRICKS_JOB_ID).",
        )

    get_project_or_404(project_id, db, LabelingProject)
    export_path = body.export_path.strip()
    if not export_path:
        raise HTTPException(status_code=400, detail="export_path is required.")

    # Blocker #2: validate path is safe and under allowed volume
    _validate_export_path(export_path)

    # Blocker #4: reject if a run is already active for this project
    active = (
        db.query(FinetuneRun)
        .filter(
            FinetuneRun.project_id == project_id,
            FinetuneRun.status.in_(["submitting", "queued", "running"]),
        )
        .first()
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"A finetune run is already active (run {active.id}, status={active.status}).",
        )

    user_email = get_user_email(request)
    run_row = FinetuneRun(
        project_id=project_id,
        status="submitting",
        export_path=export_path,
        created_by=user_email,
    )
    db.add(run_row)
    db.commit()
    db.refresh(run_row)

    # Blocker #5: submit with idempotency token; crash-safe flow
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
    # Blocker #7: build run URL for troubleshooting
    host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
    job_id = resolve_finetune_job_id()
    if host and job_id:
        run_row.databricks_run_url = f"{host}/#job/{job_id}/run/{drid}"
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


@router.get("/finetune-runs/latest", response_model=FinetuneRunOut)
def get_latest_finetune_run(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(project_id, db, LabelingProject)
    row = (
        db.query(FinetuneRun)
        .filter_by(project_id=project_id)
        .order_by(FinetuneRun.id.desc())
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="No finetune runs for this project.")
    sync_run_status(row, db)
    return FinetuneRunOut.model_validate(row)


@router.get("/finetune-runs/{run_id}", response_model=FinetuneRunOut)
def get_finetune_run(project_id: int, run_id: int, db: Session = Depends(get_db)):
    get_project_or_404(project_id, db, LabelingProject)
    row = (
        db.query(FinetuneRun)
        .filter_by(id=run_id, project_id=project_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Run not found.")
    sync_run_status(row, db)
    return FinetuneRunOut.model_validate(row)
