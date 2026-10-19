"""Blind multi-labeller classification consensus routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session, joinedload

from ..consensus import (
    case_submissions,
    claim_task,
    consensus_metrics,
    heartbeat_task,
    project_reviewers,
    reopen_under_voted_resolved_cases,
    release_task,
    require_consensus_project,
    require_labeller,
    require_project_owner,
    require_reviewer,
    reset_consensus_labeling,
    review_case,
    submit_task,
    task_annotations,
)
from ..deps import get_db, get_user_email
from ..models import (
    ConsensusCase,
    LabelingTask,
    ProjectSample,
)
from ..schemas import (
    AnnotationOut,
    ConsensusLockRequest,
    ConsensusMetricsOut,
    ConsensusProgressOut,
    ConsensusReopenResult,
    ConsensusResetRequest,
    ConsensusResetResult,
    ConsensusResolutionOut,
    ConsensusReviewAction,
    ConsensusReviewCaseOut,
    ConsensusReviewPage,
    ConsensusSubmitOut,
    ConsensusTaskAnnotationOut,
    ConsensusTaskOut,
    ConsensusTaskSubmit,
    SampleOut,
)

router = APIRouter(
    prefix="/api/projects/{project_id}/consensus",
    tags=["consensus"],
)


def _task_out(db: Session, task: LabelingTask) -> ConsensusTaskOut:
    sample = (
        db.query(ProjectSample)
        .options(joinedload(ProjectSample.annotations))
        .filter_by(id=task.sample_id, project_id=task.project_id)
        .first()
    )
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")
    sample_out = SampleOut.model_validate(sample)
    sample_out.annotations = [
        AnnotationOut.model_validate(annotation)
        for annotation in sample.annotations
        if annotation.is_draft and (annotation.created_by or "").startswith("model:")
    ]
    return ConsensusTaskOut(
        id=task.id,
        case_id=task.case_id,
        sample_id=task.sample_id,
        status=task.status,
        assigned_to=task.assigned_to or "",
        lock_token=task.lock_token or "",
        locked_at=task.locked_at,
        has_previous_submission=task.submitted_at is not None,
        editable_until_review=not task.locked_for_review,
        sample=sample_out,
        annotations=[
            ConsensusTaskAnnotationOut.model_validate(annotation)
            for annotation in task_annotations(task)
        ],
    )


@router.post("/tasks/next", response_model=Optional[ConsensusTaskOut])
def claim_next_task(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    project = require_consensus_project(db, project_id)
    user = get_user_email(request)
    require_labeller(project, user)
    task = claim_task(db, project, user)
    return _task_out(db, task) if task else None


@router.post("/samples/{sample_id}/claim", response_model=Optional[ConsensusTaskOut])
def claim_sample_task(
    project_id: int,
    sample_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    project = require_consensus_project(db, project_id)
    user = get_user_email(request)
    require_labeller(project, user)
    sample = db.query(ProjectSample).filter_by(id=sample_id, project_id=project_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")
    task = claim_task(db, project, user, sample_id=sample_id)
    return _task_out(db, task) if task else None


@router.post("/tasks/{task_id}/heartbeat", response_model=ConsensusTaskOut)
def heartbeat(
    project_id: int,
    task_id: int,
    payload: ConsensusLockRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    require_consensus_project(db, project_id)
    task = heartbeat_task(
        db, project_id, task_id, get_user_email(request), payload.lock_token,
    )
    return _task_out(db, task)


@router.post("/tasks/{task_id}/release")
def release(
    project_id: int,
    task_id: int,
    payload: ConsensusLockRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    require_consensus_project(db, project_id)
    release_task(
        db, project_id, task_id, get_user_email(request), payload.lock_token,
    )
    return {"detail": "Released."}


@router.post("/tasks/{task_id}/submit", response_model=ConsensusSubmitOut)
def submit(
    project_id: int,
    task_id: int,
    payload: ConsensusTaskSubmit,
    request: Request,
    db: Session = Depends(get_db),
):
    project = require_consensus_project(db, project_id)
    task, resolution = submit_task(
        db,
        project,
        task_id,
        get_user_email(request),
        payload.lock_token,
        payload.annotations,
        payload.allow_empty,
    )
    return ConsensusSubmitOut(
        task_id=task.id,
        case_id=task.case_id,
        case_state=task.case.state,
        submitted_labels=sorted(
            annotation.label for annotation in task_annotations(task)
        ),
        submitted_annotation_count=len(task_annotations(task)),
        resolution=(
            ConsensusResolutionOut.model_validate(resolution)
            if resolution else None
        ),
    )


@router.get("/progress", response_model=ConsensusProgressOut)
def progress(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    require_consensus_project(db, project_id)
    user = get_user_email(request)
    total_tasks = db.query(LabelingTask).filter_by(project_id=project_id).count()
    submitted = (
        db.query(LabelingTask)
        .filter_by(project_id=project_id, status="submitted")
        .count()
    )
    available = (
        db.query(LabelingTask)
        .filter_by(project_id=project_id, status="pending", assigned_to=None)
        .count()
    )
    mine = (
        db.query(LabelingTask)
        .filter_by(project_id=project_id, status="submitted", assigned_to=user)
        .count()
    )
    total_items = db.query(ConsensusCase).filter_by(project_id=project_id).count()
    resolved = (
        db.query(ConsensusCase)
        .filter_by(project_id=project_id, state="resolved")
        .count()
    )
    review = (
        db.query(ConsensusCase)
        .filter_by(project_id=project_id, state="review")
        .count()
    )
    return ConsensusProgressOut(
        total_tasks=total_tasks,
        submitted_tasks=submitted,
        available_tasks=available,
        my_submitted_tasks=mine,
        resolved_items=resolved,
        review_items=review,
        total_items=total_items,
    )


@router.post("/reopen-under-voted", response_model=ConsensusReopenResult)
def reopen_under_voted(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Reopen resolved or review cases that need more votes under current settings."""
    project = require_consensus_project(db, project_id)
    require_project_owner(project, get_user_email(request))
    result = reopen_under_voted_resolved_cases(db, project)
    db.commit()
    return ConsensusReopenResult(**result)


@router.post("/reset-labeling", response_model=ConsensusResetResult)
def reset_labeling(
    project_id: int,
    payload: ConsensusResetRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Delete all project labeling work and recreate fresh private tasks."""
    project = require_consensus_project(db, project_id)
    require_project_owner(project, get_user_email(request))
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to permanently remove all labeling work.",
        )
    result = reset_consensus_labeling(db, project)
    db.commit()
    return ConsensusResetResult(**result)


def _review_case_out(
    case: ConsensusCase, *, include_annotations: bool = True,
) -> ConsensusReviewCaseOut:
    proposal = next(
        (
            resolution
            for resolution in sorted(case.resolutions, key=lambda row: row.id, reverse=True)
            if resolution.outcome == "review_required"
        ),
        None,
    )
    return ConsensusReviewCaseOut(
        case_id=case.id,
        sample_id=case.sample_id,
        filename=case.sample.filename,
        state=case.state,
        task_type=case.project.task_type,
        required_votes=case.required_votes,
        submissions=case_submissions(
            case, include_annotations=include_annotations,
        ),
        match_report=(
            proposal.match_report or {}
            if proposal and include_annotations else {}
        ),
        proposed_annotations=(
            proposal.final_annotations or []
            if proposal and include_annotations else []
        ),
    )


@router.get("/review", response_model=ConsensusReviewPage)
def review_queue(
    project_id: int,
    request: Request,
    page: int = Query(0, ge=0),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    project = require_consensus_project(db, project_id)
    require_reviewer(project, get_user_email(request))
    base = (
        db.query(ConsensusCase)
        .options(
            joinedload(ConsensusCase.sample),
            joinedload(ConsensusCase.project),
            joinedload(ConsensusCase.tasks).joinedload(LabelingTask.annotations),
            joinedload(ConsensusCase.resolutions),
        )
        .filter_by(project_id=project_id, state="review")
        .order_by(ConsensusCase.updated_at, ConsensusCase.id)
    )
    total = base.count()
    cases = base.offset(page * page_size).limit(page_size).all()
    return ConsensusReviewPage(
        items=[_review_case_out(case, include_annotations=False) for case in cases],
        total=total,
    )


@router.get("/review/{case_id}", response_model=ConsensusReviewCaseOut)
def review_detail(
    project_id: int,
    case_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    project = require_consensus_project(db, project_id)
    require_reviewer(project, get_user_email(request))
    case = (
        db.query(ConsensusCase)
        .options(
            joinedload(ConsensusCase.sample),
            joinedload(ConsensusCase.project),
            joinedload(ConsensusCase.tasks).joinedload(LabelingTask.annotations),
            joinedload(ConsensusCase.resolutions),
        )
        .filter_by(id=case_id, project_id=project_id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Consensus case not found.")
    return _review_case_out(case)


@router.post("/review/{case_id}", response_model=ConsensusResolutionOut)
def resolve_review(
    project_id: int,
    case_id: int,
    payload: ConsensusReviewAction,
    request: Request,
    db: Session = Depends(get_db),
):
    project = require_consensus_project(db, project_id)
    resolution = review_case(
        db,
        project,
        case_id,
        get_user_email(request),
        payload.action,
        payload.source_task_id,
        payload.labels,
        payload.annotations,
    )
    return ConsensusResolutionOut.model_validate(resolution)


@router.get("/metrics", response_model=ConsensusMetricsOut)
def metrics(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    project = require_consensus_project(db, project_id)
    if (
        get_user_email(request).strip().lower() not in project_reviewers(project)
    ):
        # Labellers can see aggregate progress but not per-user quality metrics.
        values = consensus_metrics(db, project_id)
        values["per_user"] = []
        return ConsensusMetricsOut(**values)
    return ConsensusMetricsOut(**consensus_metrics(db, project_id))
