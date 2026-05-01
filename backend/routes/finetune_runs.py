"""Finetuning job management — trigger after export, status polling."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..deps import get_db, get_user_email
from ..finetune_triggers import resolve_finetune_job_id, trigger_finetune_job
from ..job_utils import get_project_or_404, sync_run_status
from ..models import LabelingProject, FinetuneRun
from ..schemas import FinetuneRunOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["finetune-jobs"])


@router.post("/finetune", response_model=FinetuneRunOut)
def trigger_finetune(
    project_id: int,
    body: dict,
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
    export_path = (body.get("export_path") or "").strip()
    if not export_path:
        raise HTTPException(status_code=400, detail="export_path is required.")

    user_email = get_user_email(request)
    run_row = FinetuneRun(
        project_id=project_id,
        status="pending",
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
        raise HTTPException(status_code=502, detail=str(e)) from e

    run_row.databricks_run_id = drid
    run_row.status = "queued"
    db.commit()
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
