"""Core services for blind, classification-only consensus labeling."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import exists, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, joinedload

from . import image_meta, masks
from .consensus_matching import bbox_iou, mask_iou, match_spatial_annotations
from .deps import LOCK_TIMEOUT
from .models import (
    Annotation,
    AnnotationHistory,
    ConsensusCase,
    ConsensusResolution,
    LabelingProject,
    LabelingTask,
    ProjectSample,
    TaskAnnotation,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalized_labels(labels) -> tuple[str, ...]:
    """Return a stable classification-set signature."""
    return tuple(sorted({str(label).strip() for label in labels if str(label).strip()}))


def project_reviewers(project: LabelingProject) -> set[str]:
    reviewers = {
        str(email).strip().lower()
        for email in (project.consensus_reviewer_emails or [])
        if str(email).strip()
    }
    if project.created_by:
        reviewers.add(project.created_by.strip().lower())
    return reviewers


def project_labellers(project: LabelingProject) -> set[str]:
    return {
        str(email).strip().lower()
        for email in (project.consensus_labeller_emails or [])
        if str(email).strip()
    }


def require_consensus_project(
    db: Session, project_id: int,
) -> LabelingProject:
    project = db.query(LabelingProject).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    if not project.consensus_enabled:
        raise HTTPException(status_code=409, detail="Consensus is not enabled for this project.")
    if project.task_type not in ("classification", "detection", "segmentation"):
        raise HTTPException(
            status_code=409,
            detail=f"Consensus does not support {project.task_type} projects.",
        )
    return project


def require_project_owner(project: LabelingProject, user_email: str) -> None:
    if (project.created_by or "").strip().lower() != user_email.strip().lower():
        raise HTTPException(status_code=403, detail="Only the project owner can change consensus settings.")


def require_reviewer(project: LabelingProject, user_email: str) -> None:
    if user_email.strip().lower() not in project_reviewers(project):
        raise HTTPException(status_code=403, detail="Reviewer access is required.")


def require_labeller(project: LabelingProject, user_email: str) -> None:
    user = user_email.strip().lower()
    owner = (project.created_by or "").strip().lower()
    if user == owner:
        return
    if user in project_reviewers(project):
        raise HTTPException(
            status_code=403,
            detail="This project assigns you to review, not blind labeling.",
        )
    labellers = project_labellers(project)
    if labellers and user not in labellers:
        raise HTTPException(
            status_code=403,
            detail="You are not assigned as a labeller for this project.",
        )


def _selected_for_consensus(project: LabelingProject, sample_id: int) -> bool:
    coverage = max(0, min(100, int(project.consensus_coverage_percent or 0)))
    if coverage == 100:
        return True
    if coverage == 0:
        return False
    digest = hashlib.sha256(f"{project.id}:{sample_id}".encode()).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    return bucket < coverage


def ensure_consensus_cases(
    db: Session,
    project: LabelingProject,
    sample_ids: list[int] | None = None,
) -> int:
    """Create stable cases and task slots for samples that do not have them."""
    query = db.query(ProjectSample.id).filter(ProjectSample.project_id == project.id)
    if sample_ids is not None:
        query = query.filter(ProjectSample.id.in_(sample_ids))
    existing = {
        row[0]
        for row in db.query(ConsensusCase.sample_id)
        .filter(ConsensusCase.project_id == project.id)
        .all()
    }
    created = 0
    for (sample_id,) in query.order_by(ProjectSample.id).all():
        if sample_id in existing:
            continue
        selected = _selected_for_consensus(project, sample_id)
        required = int(project.consensus_panel_size or 3) if selected else 1
        case = ConsensusCase(
            project_id=project.id,
            sample_id=sample_id,
            selected_for_consensus=selected,
            required_votes=required,
            quorum=min(int(project.consensus_quorum or 1), required),
            iou_threshold=float(project.consensus_iou_threshold),
            agreement_threshold=float(project.consensus_agreement_threshold),
        )
        db.add(case)
        db.flush()
        for slot in range(required):
            db.add(LabelingTask(
                case_id=case.id,
                project_id=project.id,
                sample_id=sample_id,
                slot_index=slot,
            ))
        created += 1
    return created


def reset_unstarted_cases(db: Session, project: LabelingProject) -> int:
    """Rebuild only cases where no task has ever been assigned."""
    cases = (
        db.query(ConsensusCase)
        .options(joinedload(ConsensusCase.tasks))
        .filter(ConsensusCase.project_id == project.id)
        .all()
    )
    sample_ids = []
    for case in cases:
        if all(task.assigned_to is None and task.status == "pending" for task in case.tasks):
            sample_ids.append(case.sample_id)
            db.delete(case)
    db.flush()
    ensure_consensus_cases(db, project, sample_ids)
    return len(sample_ids)


def reopen_under_voted_resolved_cases(
    db: Session, project: LabelingProject,
) -> dict[str, int]:
    """Reopen resolved or review cases that need more votes under current settings.

    Existing private submissions and append-only resolutions are preserved.
    Only the materialized final annotation is withdrawn while the expanded
    panel collects its remaining private votes. Disagreement queues already
    in review are unlocked the same way so extra panel slots can be filled.
    """
    cases = (
        db.query(ConsensusCase)
        .options(joinedload(ConsensusCase.tasks))
        .filter(
            ConsensusCase.project_id == project.id,
            ConsensusCase.state.in_(("resolved", "review")),
        )
        .order_by(ConsensusCase.id)
        .all()
    )
    reopened = 0
    tasks_added = 0
    annotations_removed = 0
    for case in cases:
        selected = _selected_for_consensus(project, case.sample_id)
        required = int(project.consensus_panel_size or 3) if selected else 1
        submitted_count = sum(task.status == "submitted" for task in case.tasks)
        if required <= submitted_count:
            continue

        active_resolution_id = case.active_resolution_id
        if active_resolution_id:
            annotations_removed += (
                db.query(Annotation)
                .filter(
                    Annotation.project_id == project.id,
                    Annotation.sample_id == case.sample_id,
                    Annotation.consensus_resolution_id == active_resolution_id,
                )
                .delete(synchronize_session=False)
            )

        for task in case.tasks:
            task.locked_for_review = False
            task.lock_token = None
            task.locked_at = None
            if task.submitted_at is not None:
                task.status = "submitted"
            elif task.status == "in_progress":
                task.status = "pending"
                task.assigned_to = None
                task.assigned_at = None

        next_slot = max((task.slot_index for task in case.tasks), default=-1) + 1
        while len(case.tasks) < required:
            task = LabelingTask(
                case_id=case.id,
                project_id=project.id,
                sample_id=case.sample_id,
                slot_index=next_slot,
            )
            db.add(task)
            case.tasks.append(task)
            next_slot += 1
            tasks_added += 1

        case.selected_for_consensus = selected
        case.required_votes = required
        case.quorum = min(int(project.consensus_quorum or 1), required)
        case.iou_threshold = float(project.consensus_iou_threshold)
        case.agreement_threshold = float(project.consensus_agreement_threshold)
        case.active_resolution_id = None
        case.state = "in_progress" if submitted_count else "pending"

        sample = db.query(ProjectSample).filter_by(id=case.sample_id).first()
        if sample:
            has_drafts = (
                db.query(Annotation.id)
                .filter_by(
                    project_id=project.id,
                    sample_id=case.sample_id,
                    is_draft=True,
                )
                .first()
                is not None
            )
            sample.status = "pre_labeled" if has_drafts else "unlabeled"
            sample.locked_by = None
            sample.locked_at = None
        reopened += 1

    return {
        "cases_reopened": reopened,
        "tasks_added": tasks_added,
        "annotations_removed": annotations_removed,
    }


def reset_consensus_labeling(
    db: Session, project: LabelingProject,
) -> dict[str, int]:
    """Remove all labeling work and recreate fresh consensus task panels."""
    annotations_deleted = (
        db.query(Annotation)
        .filter(Annotation.project_id == project.id)
        .delete(synchronize_session=False)
    )
    history_deleted = (
        db.query(AnnotationHistory)
        .filter(AnnotationHistory.project_id == project.id)
        .delete(synchronize_session=False)
    )
    cases = (
        db.query(ConsensusCase)
        .options(
            joinedload(ConsensusCase.tasks).joinedload(LabelingTask.annotations),
            joinedload(ConsensusCase.resolutions),
        )
        .filter(ConsensusCase.project_id == project.id)
        .all()
    )
    for case in cases:
        db.delete(case)
    db.flush()

    samples_reset = (
        db.query(ProjectSample)
        .filter(ProjectSample.project_id == project.id)
        .update(
            {
                ProjectSample.status: "unlabeled",
                ProjectSample.locked_by: None,
                ProjectSample.locked_at: None,
                ProjectSample.prediction_confidence: None,
            },
            synchronize_session=False,
        )
    )
    cases_recreated = ensure_consensus_cases(db, project)
    return {
        "samples_reset": samples_reset,
        "annotations_deleted": annotations_deleted,
        "history_deleted": history_deleted,
        "cases_recreated": cases_recreated,
    }


def delete_unstarted_cases(db: Session, project_id: int) -> int:
    """Delete cases that have no assignment history, using ORM cascades."""
    cases = (
        db.query(ConsensusCase)
        .options(joinedload(ConsensusCase.tasks))
        .filter(ConsensusCase.project_id == project_id)
        .all()
    )
    deleted = 0
    for case in cases:
        if all(task.assigned_to is None and task.status == "pending" for task in case.tasks):
            db.delete(case)
            deleted += 1
    db.flush()
    return deleted


def release_stale_tasks(db: Session, project_id: int) -> int:
    cutoff = utcnow() - LOCK_TIMEOUT
    tasks = (
        db.query(LabelingTask)
        .filter(
            LabelingTask.project_id == project_id,
            LabelingTask.status == "in_progress",
            LabelingTask.locked_at < cutoff,
        )
        .all()
    )
    for task in tasks:
        if task.submitted_at is not None:
            # A stale edit session must not erase the user's submitted vote or
            # make its slot available to another annotator.
            task.status = "submitted"
        else:
            task.status = "pending"
            task.assigned_to = None
            task.assigned_at = None
        task.lock_token = None
        task.locked_at = None
    return len(tasks)


def _claim_query(db: Session, project_id: int, user_email: str):
    prior_task = aliased(LabelingTask)
    already_has_case = exists().where(
        prior_task.case_id == ConsensusCase.id,
        prior_task.assigned_to == user_email,
    )
    return (
        db.query(LabelingTask)
        .join(ConsensusCase, ConsensusCase.id == LabelingTask.case_id)
        .filter(
            LabelingTask.project_id == project_id,
            LabelingTask.status == "pending",
            LabelingTask.assigned_to.is_(None),
            ConsensusCase.state.in_(["pending", "in_progress"]),
            ~already_has_case,
        )
        .order_by(ConsensusCase.sample_id, LabelingTask.slot_index)
    )


def claim_task(
    db: Session,
    project: LabelingProject,
    user_email: str,
    sample_id: int | None = None,
) -> LabelingTask | None:
    """Atomically assign one private task to a user."""
    release_stale_tasks(db, project.id)
    ensure_consensus_cases(db, project)
    db.flush()

    # Explicitly opening a sample from the gallery reopens this user's own
    # submitted vote while the case is still collecting votes. "Next" never
    # does this, otherwise users would loop over their previous submissions.
    if sample_id is not None:
        is_postgres = db.bind is not None and db.bind.dialect.name == "postgresql"
        case_query = db.query(ConsensusCase).filter_by(
            project_id=project.id, sample_id=sample_id,
        )
        if is_postgres:
            # Use the same row lock as evaluate_case so reopening cannot race
            # the final panel submission and resurrect a frozen vote.
            case_query = case_query.with_for_update()
        editable_case = case_query.first()
        editable = None
        if editable_case and editable_case.state in ("pending", "in_progress"):
            editable = (
                db.query(LabelingTask)
                .filter(
                    LabelingTask.case_id == editable_case.id,
                    LabelingTask.assigned_to == user_email,
                    LabelingTask.status == "submitted",
                    LabelingTask.locked_for_review.is_(False),
                )
                .order_by(LabelingTask.id)
                .first()
            )
        if editable:
            now = utcnow()
            editable.status = "in_progress"
            editable.lock_token = uuid4().hex
            editable.locked_at = now
            editable.case.state = "in_progress"
            db.commit()
            db.refresh(editable)
            return editable

    active = (
        db.query(LabelingTask)
        .filter(
            LabelingTask.project_id == project.id,
            LabelingTask.assigned_to == user_email,
            LabelingTask.status == "in_progress",
        )
    )
    if sample_id is not None:
        active = active.filter(LabelingTask.sample_id == sample_id)
    existing = active.order_by(LabelingTask.id).first()
    if existing:
        existing.locked_at = utcnow()
        if not existing.lock_token:
            existing.lock_token = uuid4().hex
        db.commit()
        db.refresh(existing)
        return existing

    is_postgres = db.bind is not None and db.bind.dialect.name == "postgresql"
    for _attempt in range(5):
        query = _claim_query(db, project.id, user_email)
        if sample_id is not None:
            query = query.filter(LabelingTask.sample_id == sample_id)
        if is_postgres:
            query = query.with_for_update(skip_locked=True)
        task = query.first()
        if not task:
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                continue
            return None

        now = utcnow()
        token = uuid4().hex
        if is_postgres:
            task.assigned_to = user_email
            task.assigned_at = now
            task.locked_at = now
            task.lock_token = token
            task.status = "in_progress"
            task.case.state = "in_progress"
            db.commit()
            db.refresh(task)
            return task

        # SQLite has no SKIP LOCKED. A guarded UPDATE makes two racing
        # sessions compete for the row rather than both believing they won it.
        task_id = task.id
        case_id = task.case_id
        db.expire_all()
        updated = (
            db.query(LabelingTask)
            .filter(
                LabelingTask.id == task_id,
                LabelingTask.status == "pending",
                LabelingTask.assigned_to.is_(None),
            )
            .update(
                {
                    LabelingTask.assigned_to: user_email,
                    LabelingTask.assigned_at: now,
                    LabelingTask.locked_at: now,
                    LabelingTask.lock_token: token,
                    LabelingTask.status: "in_progress",
                },
                synchronize_session=False,
            )
        )
        if updated:
            db.query(ConsensusCase).filter(ConsensusCase.id == case_id).update(
                {ConsensusCase.state: "in_progress"},
                synchronize_session=False,
            )
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                continue
            return _task_or_404(db, project.id, task_id)
        db.rollback()
    return None


def _task_or_404(db: Session, project_id: int, task_id: int) -> LabelingTask:
    task = (
        db.query(LabelingTask)
        .options(joinedload(LabelingTask.annotations), joinedload(LabelingTask.case))
        .filter_by(id=task_id, project_id=project_id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Consensus task not found.")
    return task


def require_task_lock(
    task: LabelingTask,
    user_email: str,
    lock_token: str,
    *,
    allow_submitted: bool = False,
) -> None:
    if task.assigned_to != user_email or task.lock_token != lock_token:
        raise HTTPException(status_code=409, detail="This task is not locked by this session.")
    if task.status == "submitted" and allow_submitted:
        return
    if task.status != "in_progress":
        raise HTTPException(status_code=409, detail=f"Task is {task.status}.")
    cutoff = utcnow() - LOCK_TIMEOUT
    locked_at = task.locked_at
    if locked_at is not None and locked_at.tzinfo is None:
        locked_at = locked_at.replace(tzinfo=timezone.utc)
    if not locked_at or locked_at < cutoff:
        raise HTTPException(status_code=409, detail="Task lock expired; claim it again.")


def heartbeat_task(
    db: Session, project_id: int, task_id: int, user_email: str, lock_token: str,
) -> LabelingTask:
    task = _task_or_404(db, project_id, task_id)
    require_task_lock(task, user_email, lock_token)
    task.locked_at = utcnow()
    db.commit()
    db.refresh(task)
    return task


def release_task(
    db: Session, project_id: int, task_id: int, user_email: str, lock_token: str,
) -> None:
    task = _task_or_404(db, project_id, task_id)
    require_task_lock(task, user_email, lock_token)
    if task.submitted_at is not None:
        task.status = "submitted"
    else:
        task.status = "pending"
        task.assigned_to = None
        task.assigned_at = None
    task.lock_token = None
    task.locked_at = None
    if (
        task.submitted_at is None
        and not any(
            t.status in ("in_progress", "submitted")
            for t in task.case.tasks if t.id != task.id
        )
    ):
        task.case.state = "pending"
    db.commit()


def task_annotations(task: LabelingTask) -> list[TaskAnnotation]:
    """Return only the task's current annotation revision."""
    revision = int(task.annotation_revision or 0)
    return [
        annotation
        for annotation in task.annotations
        if int(annotation.revision or 0) == revision
    ]


def _task_signature(task: LabelingTask) -> tuple[str, ...]:
    return normalized_labels(a.label for a in task_annotations(task))


def _annotation_spec(annotation) -> dict:
    row = {
        "label": str(annotation.label).strip(),
        "ann_type": annotation.ann_type,
    }
    if getattr(annotation, "bbox_json", None) is not None:
        row["bbox_json"] = annotation.bbox_json
    if getattr(annotation, "mask_json", None) is not None:
        row["mask_json"] = annotation.mask_json
    return row


def _spec_signature(specs: list[dict]) -> str:
    return json.dumps(
        sorted(
            specs,
            key=lambda row: (
                row.get("label", ""),
                row.get("ann_type", ""),
                json.dumps(row.get("bbox_json"), sort_keys=True),
                json.dumps(row.get("mask_json"), sort_keys=True),
            ),
        ),
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_task_annotations(
    db: Session,
    project: LabelingProject,
    sample_id: int,
    annotations,
    allow_empty: bool,
) -> list[dict]:
    if not annotations:
        if project.task_type == "classification" or not allow_empty:
            raise HTTPException(
                status_code=400,
                detail=(
                    "At least one annotation is required. Send allow_empty=true "
                    "to submit a spatial no-objects vote."
                ),
            )
        return []

    expected_type = {
        "classification": "classification",
        "detection": "bbox",
        "segmentation": "mask",
    }[project.task_type]
    if any(ann.ann_type != expected_type for ann in annotations):
        raise HTTPException(
            status_code=422,
            detail=f"{project.task_type} consensus accepts {expected_type} annotations only.",
        )
    labels = [str(ann.label).strip() for ann in annotations]
    if any(not label for label in labels):
        raise HTTPException(status_code=422, detail="Annotation labels cannot be empty.")
    unknown = sorted({label for label in labels if label not in (project.class_list or [])})
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown project labels: {', '.join(unknown)}")

    if project.task_type == "classification":
        normalized = normalized_labels(labels)
        if len(normalized) != len(annotations):
            raise HTTPException(
                status_code=422,
                detail="Duplicate classification labels are not allowed.",
            )
        return [
            {"label": label, "ann_type": "classification"}
            for label in normalized
        ]

    sample = db.query(ProjectSample).filter_by(
        id=sample_id, project_id=project.id,
    ).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    prepared = []
    for annotation, label in zip(annotations, labels):
        if project.task_type == "detection":
            bbox = annotation.bbox_json
            if not isinstance(bbox, dict):
                raise HTTPException(status_code=422, detail="Detection annotations require bbox_json.")
            try:
                x, y, w, h = (float(bbox[key]) for key in ("x", "y", "w", "h"))
            except (KeyError, TypeError, ValueError):
                raise HTTPException(status_code=422, detail="bbox_json requires numeric x, y, w, h.")
            if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 or y + h > 1:
                raise HTTPException(
                    status_code=422,
                    detail="bbox_json must have positive size and fit within normalized [0,1] bounds.",
                )
            prepared.append({
                "label": label,
                "ann_type": "bbox",
                "bbox_json": {"x": x, "y": y, "w": w, "h": h},
            })
            continue

        dims = image_meta.resolve_dimensions(db, sample)
        if not dims:
            raise HTTPException(
                status_code=422,
                detail="Cannot save a consensus mask because image dimensions are unavailable.",
            )
        image_width, image_height = dims
        try:
            height, width, counts = masks.validate_uncompressed(
                annotation.mask_json, image_height, image_width,
            )
        except masks.MaskValidationError as error:
            raise HTTPException(status_code=422, detail=f"Invalid mask: {error}") from error
        prepared.append({
            "label": label,
            "ann_type": "mask",
            "bbox_json": masks.normalized_bbox(counts, height, width),
            "mask_json": masks.to_storage(counts, height, width),
        })
    return prepared


def _materialize_resolution(
    db: Session,
    case: ConsensusCase,
    resolution: ConsensusResolution,
    annotations: list[dict],
) -> None:
    db.query(Annotation).filter(
        Annotation.project_id == case.project_id,
        Annotation.sample_id == case.sample_id,
        Annotation.is_draft.is_(False),
    ).delete(synchronize_session=False)
    for annotation in annotations:
        db.add(Annotation(
            sample_id=case.sample_id,
            project_id=case.project_id,
            label=annotation["label"],
            ann_type=annotation["ann_type"],
            bbox_json=annotation.get("bbox_json"),
            mask_json=annotation.get("mask_json"),
            is_draft=False,
            created_by=resolution.resolver,
            consensus_resolution_id=resolution.id,
        ))
    sample = db.query(ProjectSample).filter_by(id=case.sample_id).first()
    if sample:
        sample.status = "labeled"
        sample.locked_by = None
        sample.locked_at = None
    case.state = "resolved"
    case.active_resolution_id = resolution.id


def create_resolution(
    db: Session,
    case: ConsensusCase,
    *,
    annotations: list[dict],
    method: str,
    resolver: str,
    vote_count: int,
    agreement_score: float | None,
    source_task_id: int | None = None,
    match_report: dict | None = None,
) -> ConsensusResolution:
    labels = normalized_labels(annotation["label"] for annotation in annotations)
    resolution = ConsensusResolution(
        case_id=case.id,
        project_id=case.project_id,
        sample_id=case.sample_id,
        method=method,
        outcome="resolved",
        final_labels=list(labels),
        final_annotations=annotations,
        match_report=match_report or {},
        agreement_score=agreement_score,
        vote_count=vote_count,
        resolver=resolver,
        source_task_id=source_task_id,
    )
    db.add(resolution)
    db.flush()
    _materialize_resolution(db, case, resolution, annotations)
    return resolution


def evaluate_case(
    db: Session, project: LabelingProject, case: ConsensusCase,
) -> ConsensusResolution | None:
    # Expired reopen sessions must count as submitted again, otherwise the
    # last panel vote can never freeze while an abandoned editor holds a slot.
    release_stale_tasks(db, project.id)
    db.flush()
    is_postgres = db.bind is not None and db.bind.dialect.name == "postgresql"
    case_query = db.query(ConsensusCase).filter(ConsensusCase.id == case.id)
    if is_postgres:
        case_query = case_query.with_for_update()
    case = case_query.one()
    if case.state == "resolved" and case.active_resolution_id:
        return db.query(ConsensusResolution).filter_by(id=case.active_resolution_id).first()
    submitted = (
        db.query(LabelingTask)
        .options(joinedload(LabelingTask.annotations))
        .filter_by(case_id=case.id, status="submitted")
        .order_by(LabelingTask.id)
        .all()
    )
    if len(submitted) < case.required_votes:
        case.state = "in_progress"
        return None

    # This is the freeze boundary: once the complete panel exists, neither an
    # automatic outcome nor reviewer input may race against changing votes.
    for task in submitted:
        task.locked_for_review = True

    if project.task_type != "classification":
        merge = match_spatial_annotations(
            [
                {"task_id": task.id, "annotations": task_annotations(task)}
                for task in submitted
            ],
            task_type=project.task_type,
            required_votes=case.required_votes,
            quorum=min(int(case.quorum or 1), case.required_votes),
            iou_threshold=float(case.iou_threshold),
            agreement_threshold=float(case.agreement_threshold),
        )
        annotations = list(merge["final_annotations"])
        if not merge["requires_review"]:
            method = "single" if case.required_votes == 1 else "auto-spatial"
            return create_resolution(
                db,
                case,
                annotations=annotations,
                method=method,
                resolver="consensus:auto",
                vote_count=merge["vote_count"],
                agreement_score=merge["agreement_score"],
                match_report=merge["match_report"],
            )
        proposal = ConsensusResolution(
            case_id=case.id,
            project_id=case.project_id,
            sample_id=case.sample_id,
            method="auto-match",
            outcome="review_required",
            final_labels=list(normalized_labels(row["label"] for row in annotations)),
            final_annotations=annotations,
            match_report=merge["match_report"],
            vote_count=merge["vote_count"],
            agreement_score=merge["agreement_score"],
            resolver="consensus:auto",
        )
        db.add(proposal)
        case.state = "review"
        return None

    counts = Counter(_task_signature(task) for task in submitted)
    ranked = counts.most_common()
    winning_labels, winning_count = ranked[0]
    unique_winner = len(ranked) == 1 or winning_count > ranked[1][1]
    quorum = min(int(case.quorum or 1), case.required_votes)
    if unique_winner and winning_count >= quorum:
        method = "single" if case.required_votes == 1 else "auto-vote"
        return create_resolution(
            db,
            case,
            annotations=[
                {"label": label, "ann_type": "classification"}
                for label in winning_labels
            ],
            method=method,
            resolver="consensus:auto",
            vote_count=winning_count,
            agreement_score=winning_count / len(submitted),
        )
    case.state = "review"
    return None


def submit_task(
    db: Session,
    project: LabelingProject,
    task_id: int,
    user_email: str,
    lock_token: str,
    annotations,
    allow_empty: bool,
) -> tuple[LabelingTask, ConsensusResolution | None]:
    release_stale_tasks(db, project.id)
    db.flush()
    task = _task_or_404(db, project.id, task_id)
    prepared = _validate_task_annotations(
        db, project, task.sample_id, annotations, allow_empty,
    )
    if task.locked_for_review:
        require_task_lock(task, user_email, lock_token, allow_submitted=True)
        if _spec_signature(
            [_annotation_spec(row) for row in task_annotations(task)]
        ) != _spec_signature(prepared):
            raise HTTPException(
                status_code=409,
                detail="This vote is locked for review.",
            )
        resolution = (
            db.query(ConsensusResolution)
            .filter_by(id=task.case.active_resolution_id)
            .first()
            if task.case.active_resolution_id else None
        )
        return task, resolution

    if task.status == "submitted":
        require_task_lock(task, user_email, lock_token, allow_submitted=True)
    else:
        require_task_lock(task, user_email, lock_token)
    current = [_annotation_spec(row) for row in task_annotations(task)]
    if task.annotation_revision == 0 or _spec_signature(current) != _spec_signature(prepared):
        task.annotation_revision = int(task.annotation_revision or 0) + 1
        for annotation in prepared:
            db.add(TaskAnnotation(
                task_id=task.id,
                revision=task.annotation_revision,
                **annotation,
            ))
    task.status = "submitted"
    task.submitted_at = utcnow()
    task.locked_at = None
    db.flush()
    db.refresh(task)
    resolution = evaluate_case(db, project, task.case)
    db.commit()
    if resolution:
        db.refresh(resolution)
    return task, resolution


def case_submissions(
    case: ConsensusCase, *, include_annotations: bool = True,
) -> list[dict]:
    rows = []
    for task in sorted(case.tasks, key=lambda item: item.id):
        if task.status != "submitted":
            continue
        rows.append({
            "task_id": task.id,
            "submitted_by": task.assigned_to or "unknown",
            "submitted_at": task.submitted_at,
            "labels": list(_task_signature(task)),
            "annotations": [
                {
                    "id": annotation.id,
                    **_annotation_spec(annotation),
                }
                for annotation in sorted(
                    task_annotations(task), key=lambda row: row.id,
                )
            ] if include_annotations else [],
        })
    return rows


def review_case(
    db: Session,
    project: LabelingProject,
    case_id: int,
    user_email: str,
    action: str,
    source_task_id: int | None,
    labels: list[str],
    annotations,
) -> ConsensusResolution:
    require_reviewer(project, user_email)
    case = (
        db.query(ConsensusCase)
        .options(joinedload(ConsensusCase.tasks).joinedload(LabelingTask.annotations))
        .filter_by(id=case_id, project_id=project.id)
        .first()
    )
    if not case:
        raise HTTPException(status_code=404, detail="Consensus case not found.")
    if case.state != "review":
        raise HTTPException(status_code=409, detail=f"Case is {case.state}.")

    submitted = [task for task in case.tasks if task.status == "submitted"]
    signatures = [_task_signature(task) for task in submitted]

    if action == "relabel":
        next_slot = max((task.slot_index for task in case.tasks), default=-1) + 1
        resolution = ConsensusResolution(
            case_id=case.id,
            project_id=case.project_id,
            sample_id=case.sample_id,
            method="manual-relabel",
            outcome="relabel_requested",
            final_labels=[],
            vote_count=len(submitted),
            resolver=user_email,
        )
        db.add(resolution)
        case.required_votes += 1
        case.state = "in_progress"
        db.add(LabelingTask(
            case_id=case.id,
            project_id=case.project_id,
            sample_id=case.sample_id,
            slot_index=next_slot,
        ))
        db.commit()
        db.refresh(resolution)
        return resolution

    if action == "reject":
        resolution = ConsensusResolution(
            case_id=case.id,
            project_id=case.project_id,
            sample_id=case.sample_id,
            method="manual-reject",
            outcome="rejected",
            final_labels=[],
            vote_count=len(submitted),
            resolver=user_email,
        )
        db.add(resolution)
        db.flush()
        case.state = "rejected"
        case.active_resolution_id = resolution.id
        sample = db.query(ProjectSample).filter_by(id=case.sample_id).first()
        if sample:
            sample.status = "skipped"
        db.commit()
        db.refresh(resolution)
        return resolution

    source_task = None
    if action == "pick":
        source_task = next((task for task in submitted if task.id == source_task_id), None)
        if not source_task:
            raise HTTPException(status_code=422, detail="Pick requires a submitted source_task_id.")
        final_annotations = [
            _annotation_spec(row) for row in task_annotations(source_task)
        ]
        method = "manual-pick"
    elif action == "edit":
        if project.task_type == "classification" and labels and not annotations:
            annotations = [
                type(
                    "_Annotation",
                    (),
                    {
                        "label": label,
                        "ann_type": "classification",
                        "bbox_json": None,
                        "mask_json": None,
                    },
                )
                for label in labels
            ]
        final_annotations = _validate_task_annotations(
            db,
            project,
            case.sample_id,
            annotations,
            allow_empty=project.task_type != "classification",
        )
        method = "manual-edit"
    else:
        raise HTTPException(status_code=422, detail="Unknown review action.")

    final_labels = normalized_labels(row["label"] for row in final_annotations)
    matching = sum(1 for signature in signatures if signature == final_labels)
    resolution = create_resolution(
        db,
        case,
        annotations=final_annotations,
        method=method,
        resolver=user_email,
        vote_count=matching,
        agreement_score=(matching / len(signatures)) if signatures else 0.0,
        source_task_id=source_task.id if source_task else None,
    )
    db.commit()
    db.refresh(resolution)
    return resolution


def _task_agrees_with_resolution(
    project: LabelingProject,
    case: ConsensusCase,
    task: LabelingTask,
    resolution: ConsensusResolution,
) -> bool:
    if project.task_type == "classification":
        return _task_signature(task) == normalized_labels(resolution.final_labels or [])
    final_annotations = list(resolution.final_annotations or [])
    remaining = list(range(len(final_annotations)))
    for annotation in task_annotations(task):
        best = None
        best_score = -1.0
        for index in remaining:
            candidate = final_annotations[index]
            if candidate.get("label") != annotation.label:
                continue
            score = (
                bbox_iou(annotation.bbox_json, candidate.get("bbox_json"))
                if project.task_type == "detection"
                else mask_iou(annotation.mask_json, candidate.get("mask_json"))
            )
            if score > best_score:
                best, best_score = index, score
        if best is None or best_score < float(case.iou_threshold):
            return False
        remaining.remove(best)
    return not remaining


def consensus_metrics(db: Session, project_id: int) -> dict:
    project = db.query(LabelingProject).filter_by(id=project_id).one()
    cases = (
        db.query(ConsensusCase)
        .options(
            joinedload(ConsensusCase.tasks).joinedload(LabelingTask.annotations),
            joinedload(ConsensusCase.resolutions),
        )
        .filter(ConsensusCase.project_id == project_id)
        .all()
    )
    state_counts = Counter(case.state for case in cases)
    tasks = [task for case in cases for task in case.tasks]
    active_resolutions = [
        next((r for r in case.resolutions if r.id == case.active_resolution_id), None)
        for case in cases
    ]
    active_resolutions = [r for r in active_resolutions if r is not None]
    scores = [r.agreement_score for r in active_resolutions if r.agreement_score is not None]

    distribution = [
        {"bucket": label, "count": sum(1 for score in scores if low <= score <= high)}
        for label, low, high in (
            ("0-49%", 0.0, 0.499999),
            ("50-74%", 0.5, 0.749999),
            ("75-99%", 0.75, 0.999999),
            ("100%", 1.0, 1.0),
        )
    ]
    per_user: dict[str, dict] = {}
    final_by_case = {
        case.id: next(
            (r for r in case.resolutions if r.id == case.active_resolution_id),
            None,
        )
        for case in cases
        if case.active_resolution_id
    }
    for case in cases:
        for task in case.tasks:
            if task.status != "submitted":
                continue
            user = task.assigned_to or "unknown"
            row = per_user.setdefault(user, {
                "user": user,
                "submitted": 0,
                "agreed": 0,
                "disagreed": 0,
                "agreement_rate": 0.0,
                "avg_minutes": 0.0,
                "_minutes": [],
            })
            row["submitted"] += 1
            if case.id in final_by_case and final_by_case[case.id] is not None:
                if _task_agrees_with_resolution(
                    project, case, task, final_by_case[case.id],
                ):
                    row["agreed"] += 1
                else:
                    row["disagreed"] += 1
            if task.assigned_at and task.submitted_at:
                start = task.assigned_at
                finish = task.submitted_at
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                if finish.tzinfo is None:
                    finish = finish.replace(tzinfo=timezone.utc)
                row["_minutes"].append(max(0.0, (finish - start).total_seconds() / 60))
    for row in per_user.values():
        decided = row["agreed"] + row["disagreed"]
        row["agreement_rate"] = round(row["agreed"] / decided, 4) if decided else 0.0
        minutes = row.pop("_minutes")
        row["avg_minutes"] = round(sum(minutes) / len(minutes), 2) if minutes else 0.0

    return {
        "total_items": len(cases),
        "pending_items": state_counts["pending"],
        "in_progress_items": state_counts["in_progress"],
        "resolved_items": state_counts["resolved"],
        "review_items": state_counts["review"],
        "rejected_items": state_counts["rejected"],
        "total_tasks": len(tasks),
        "submitted_tasks": sum(task.status == "submitted" for task in tasks),
        "auto_resolved": sum(
            r.method in ("auto-vote", "auto-spatial", "single")
            for r in active_resolutions
        ),
        "manually_resolved": sum(r.method.startswith("manual-") for r in active_resolutions),
        "mean_agreement_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        "agreement_distribution": distribution,
        "per_user": sorted(per_user.values(), key=lambda row: row["user"]),
    }
