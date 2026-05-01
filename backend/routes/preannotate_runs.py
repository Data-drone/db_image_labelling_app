"""
Async pre-annotation via Databricks Jobs — enqueue, status, list.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..deps import get_db, get_user_email
from ..inference import check_endpoint_health, resolve_endpoint
from ..models import LabelingProject, PreannotateRun
from ..preannotate_triggers import resolve_preannotate_job_id, trigger_preannotate_job
from ..schemas import PreAnnotateAsyncRequest, PreannotateRunOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["preannotate-jobs"])

_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


def _get_project(project_id: int, db: Session) -> LabelingProject:
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    return p


def _sync_run_with_databricks(row: PreannotateRun, db: Session) -> None:
    """If the DB row is non-terminal but the Databricks run has finished, update the row.

    Handles the case where the serverless job crashed before the worker could
    write back to the database (e.g. library install failure, OOM, timeout).
    """
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
            "Cross-check run %s (db_run=%s): life_cycle=%s result=%s msg=%.120s",
            row.id, row.databricks_run_id, lcs, result, msg,
        )

        if "FAILED" in result or "INTERNAL_ERROR" in lcs or "SKIPPED" in lcs or "BLOCKED" in lcs:
            row.status = "failed"
            row.error_message = (msg or f"Databricks run {lcs}/{result}")[:4000]
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
            log.info("Marked run %s as failed from Databricks state: %s / %s", row.id, lcs, result)
        elif "CANCEL" in result:
            row.status = "cancelled"
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
        elif "SUCCESS" in result and row.status in ("queued", "pending"):
            row.status = "succeeded"
            row.finished_at = datetime.now(timezone.utc)
            db.commit()
    except Exception:
        log.warning("Could not cross-check Databricks run %s", row.databricks_run_id, exc_info=True)


@router.post("/pre-annotate-async", response_model=PreannotateRunOut)
def enqueue_preannotate_job(
    project_id: int,
    payload: PreAnnotateAsyncRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a run row and trigger the bundle-deployed Databricks Job (non-blocking)."""
    if resolve_preannotate_job_id() is None:
        raise HTTPException(
            status_code=503,
            detail="Async pre-annotate is not configured (set PRE_ANNOTATE_DATABRICKS_JOB_ID).",
        )

    project = _get_project(project_id, db)
    endpoint_name = resolve_endpoint(project)
    if not endpoint_name:
        raise HTTPException(status_code=400, detail="No serving endpoint configured for this project.")

    health = check_endpoint_health(endpoint_name)
    if health["status"] != "ready":
        raise HTTPException(
            status_code=503,
            detail=f"Endpoint '{endpoint_name}' is not ready: {health.get('error') or health.get('state', 'unknown')}",
        )

    user_email = get_user_email(request)
    run_row = PreannotateRun(
        project_id=project_id,
        status="pending",
        max_samples=payload.max_samples or 0,
        include_pre_labeled=bool(payload.include_pre_labeled),
        min_confidence=payload.min_confidence,
        text_prompt=payload.text_prompt or None,
        created_by=user_email,
    )
    db.add(run_row)
    db.commit()
    db.refresh(run_row)

    try:
        drid = trigger_preannotate_job(run_row.id)
    except Exception as e:
        log.exception("Failed to submit pre-annotate job")
        run_row.status = "failed"
        run_row.error_message = str(e)[:4000]
        run_row.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=502, detail=str(e)) from e

    run_row.databricks_run_id = drid
    run_row.status = "queued"
    db.commit()
    db.refresh(run_row)
    return PreannotateRunOut.model_validate(run_row)


@router.get("/pre-annotate-runs/latest", response_model=PreannotateRunOut)
def get_latest_preannotate_run(project_id: int, db: Session = Depends(get_db)):
    _get_project(project_id, db)
    row = (
        db.query(PreannotateRun)
        .filter_by(project_id=project_id)
        .order_by(PreannotateRun.id.desc())
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="No pre-annotate runs for this project.")
    _sync_run_with_databricks(row, db)
    return PreannotateRunOut.model_validate(row)


@router.get("/pre-annotate-runs/{run_id}", response_model=PreannotateRunOut)
def get_preannotate_run(project_id: int, run_id: int, db: Session = Depends(get_db)):
    _get_project(project_id, db)
    row = (
        db.query(PreannotateRun)
        .filter_by(id=run_id, project_id=project_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Run not found.")
    _sync_run_with_databricks(row, db)
    return PreannotateRunOut.model_validate(row)
