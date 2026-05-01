"""
Finetuning job management — trigger after export, status polling.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..deps import get_db, get_user_email
from ..finetune_triggers import resolve_finetune_job_id, trigger_finetune_job
from ..models import LabelingProject, FinetuneRun
from ..schemas import FinetuneRunOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["finetune-jobs"])

_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


def _get_project(project_id: int, db: Session) -> LabelingProject:
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    return p


def _sync_run_with_databricks(row: FinetuneRun, db: Session) -> None:
    """If the DB row is non-terminal but the Databricks run has finished, update the row."""
    if row.status in _TERMINAL_STATUSES or not row.databricks_run_id:
        return
    try:
        from ..volumes import _get_workspace_client
        w = _get_workspace_client()
        run = w.jobs.get_run(run_id=row.databricks_run_id)
        state = run.state
        if not state:
            return
        lcs = str(getattr(state, "life_cycle_state", "") or "").upper()
        result = str(getattr(state, "result_state", "") or "").upper()
        msg = str(getattr(state, "state_message", "") or "")

        log.info(
            "Cross-check finetune run %s (db_run=%s): life_cycle=%s result=%s msg=%.120s",
            row.id, row.databricks_run_id, lcs, result, msg,
        )

        if "RUNNING" in lcs and row.status != "running":
            row.status = "running"
            row.started_at = row.started_at or datetime.now(timezone.utc)
            db.commit()
        elif "FAILED" in result or "INTERNAL_ERROR" in lcs or "SKIPPED" in lcs or "BLOCKED" in lcs:
            row.status = "failed"
            row.error_message = (msg or f"Databricks run {lcs}/{result}")[:4000]
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
        elif "CANCEL" in result:
            row.status = "cancelled"
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
        elif "SUCCESS" in result:
            row.status = "succeeded"
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
    except Exception:
        log.warning("Could not cross-check Databricks finetune run %s", row.databricks_run_id, exc_info=True)


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

    _get_project(project_id, db)
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
    _get_project(project_id, db)
    row = (
        db.query(FinetuneRun)
        .filter_by(project_id=project_id)
        .order_by(FinetuneRun.id.desc())
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="No finetune runs for this project.")
    _sync_run_with_databricks(row, db)
    return FinetuneRunOut.model_validate(row)


@router.get("/finetune-runs/{run_id}", response_model=FinetuneRunOut)
def get_finetune_run(project_id: int, run_id: int, db: Session = Depends(get_db)):
    _get_project(project_id, db)
    row = (
        db.query(FinetuneRun)
        .filter_by(id=run_id, project_id=project_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Run not found.")
    _sync_run_with_databricks(row, db)
    return FinetuneRunOut.model_validate(row)
