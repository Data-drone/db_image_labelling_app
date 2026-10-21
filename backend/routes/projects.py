"""
Project CRUD routes.
"""

import logging
import math
import os
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import distinct, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..deps import get_db, get_user_email
from ..models import (
    Annotation,
    AnnotationHistory,
    ClusterMapPointCache,
    ClusterMapRun,
    ConsensusCase,
    EmbeddingRun,
    FinetuneRun,
    LabelingProject,
    LabelingTask,
    PreannotateRun,
    ProjectSample,
)
from ..schemas import (
    ProjectCreate, ProjectUpdate, ProjectOut, ProjectStats,
    ClassCount, DailyVelocity, DetailedProjectStats, SourceSyncOut,
)
from ..uc_tables import TableScanResult, TableSourceError, scan_table_for_samples
from ..volumes import clamp_scan_max_depth, scan_volume_for_samples
from .users import record_current_user, validate_active_project_users

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _source_type(p) -> str:
    return (getattr(p, "source_type", None) or "volume").strip().lower()


def _scan_project_source(db: Session, project: LabelingProject):
    """Populate project_samples from the project's configured source.

    Incremental: existing sample rows are kept (labels stay).
    Returns a TableScanResult (added / skipped_existing / volume_hint).
    """
    if _source_type(project) == "table":
        result = scan_table_for_samples(
            db,
            project.id,
            project.source_table,
            path_column=project.image_path_column or "image_path",
            source_filter=project.source_filter,
        )
        if result.volume_hint and not (project.source_volume or "").strip():
            project.source_volume = result.volume_hint
        return result
    added = scan_volume_for_samples(
        db,
        project.id,
        project.source_volume,
        include_subfolders=bool(getattr(project, "scan_include_subfolders", False)),
        max_depth=getattr(project, "scan_max_depth", None),
    )
    return TableScanResult(added=added, skipped_existing=0, volume_hint=project.source_volume)


def _require_can_manage_project(p: LabelingProject, request: Request) -> None:
    """Archive/delete is limited to the project owner."""
    owner = (p.created_by or "").strip().lower()
    caller = get_user_email(request).strip().lower()
    if owner != caller:
        raise HTTPException(
            status_code=403,
            detail="Only the project owner can archive or delete this project.",
        )


def _lineage_projects(db: Session, p: LabelingProject) -> list[LabelingProject]:
    root_id = p.parent_project_id or p.id
    return (
        db.query(LabelingProject)
        .filter(
            (LabelingProject.id == root_id)
            | (LabelingProject.parent_project_id == root_id)
        )
        .all()
    )


def _purge_project_row(db: Session, p: LabelingProject) -> None:
    """Delete a project and rows that still FK to it without ORM cascades."""
    pid = p.id
    db.query(PreannotateRun).filter_by(project_id=pid).delete(synchronize_session=False)
    db.query(EmbeddingRun).filter_by(project_id=pid).delete(synchronize_session=False)
    db.query(FinetuneRun).filter_by(project_id=pid).delete(synchronize_session=False)
    db.query(ClusterMapPointCache).filter_by(project_id=pid).delete(synchronize_session=False)
    db.query(ClusterMapRun).filter_by(project_id=pid).delete(synchronize_session=False)
    db.query(AnnotationHistory).filter_by(project_id=pid).delete(synchronize_session=False)
    db.delete(p)


def _table_source_changed(p: LabelingProject, payload) -> bool:
    if payload.source_type is not None and payload.source_type != _source_type(p):
        return True
    if payload.source_table is not None and (payload.source_table or "").strip() != (p.source_table or ""):
        return True
    if payload.image_path_column is not None and (
        (payload.image_path_column or "image_path") != (p.image_path_column or "image_path")
    ):
        return True
    if payload.source_filter is not None and (payload.source_filter or "").strip() != (p.source_filter or ""):
        return True
    return False


def _project_out(p, total=None, labeled=None, user_email=None, db: Session | None = None):
    """Build a ProjectOut from a LabelingProject model instance."""
    normalized_user = (user_email or "").strip().lower()
    reviewers = {
        str(email).strip().lower()
        for email in (getattr(p, "consensus_reviewer_emails", None) or [])
    }
    labellers = {
        str(email).strip().lower()
        for email in (getattr(p, "consensus_labeller_emails", None) or [])
    }
    if normalized_user and normalized_user == (p.created_by or "").strip().lower():
        consensus_role = "owner"
    elif normalized_user and normalized_user in reviewers:
        consensus_role = "reviewer"
    elif labellers and normalized_user not in labellers:
        consensus_role = "viewer"
    else:
        consensus_role = "labeller"
    consensus_counts = {
        "consensus_total_tasks": 0,
        "consensus_submitted_tasks": 0,
        "consensus_resolved_items": 0,
        "consensus_review_items": 0,
    }
    if db is not None and bool(getattr(p, "consensus_enabled", False)):
        consensus_counts = {
            "consensus_total_tasks": db.query(LabelingTask).filter_by(project_id=p.id).count(),
            "consensus_submitted_tasks": (
                db.query(LabelingTask)
                .filter_by(project_id=p.id, status="submitted")
                .count()
            ),
            "consensus_resolved_items": (
                db.query(ConsensusCase)
                .filter_by(project_id=p.id, state="resolved")
                .count()
            ),
            "consensus_review_items": (
                db.query(ConsensusCase)
                .filter_by(project_id=p.id, state="review")
                .count()
            ),
        }
    expose_members = consensus_role == "owner"
    return ProjectOut(
        id=p.id,
        name=p.name,
        description=p.description or "",
        task_type=p.task_type,
        class_list=p.class_list,
        source_volume=p.source_volume,
        source_type=getattr(p, "source_type", None) or "volume",
        source_table=getattr(p, "source_table", None),
        image_path_column=getattr(p, "image_path_column", None),
        source_filter=getattr(p, "source_filter", None),
        serving_endpoint=p.serving_endpoint,
        endpoint_config=p.endpoint_config,
        consensus_enabled=bool(getattr(p, "consensus_enabled", False)),
        consensus_panel_size=getattr(p, "consensus_panel_size", 3) or 3,
        consensus_coverage_percent=getattr(p, "consensus_coverage_percent", 100),
        consensus_quorum=getattr(p, "consensus_quorum", 2) or 2,
        consensus_iou_threshold=getattr(p, "consensus_iou_threshold", 0.5),
        consensus_agreement_threshold=getattr(
            p, "consensus_agreement_threshold", 0.5,
        ),
        consensus_reviewer_emails=(
            list(getattr(p, "consensus_reviewer_emails", None) or [])
            if expose_members else []
        ),
        consensus_labeller_emails=(
            list(getattr(p, "consensus_labeller_emails", None) or [])
            if expose_members else []
        ),
        consensus_role=consensus_role,
        created_by=p.created_by,
        created_at=p.created_at,
        archived_at=getattr(p, "archived_at", None),
        sample_count=total if total is not None else 0,
        labeled_count=labeled if labeled is not None else 0,
        version=p.version or 1,
        parent_project_id=p.parent_project_id,
        scan_include_subfolders=bool(getattr(p, "scan_include_subfolders", False)),
        scan_max_depth=getattr(p, "scan_max_depth", 3) or 3,
        **consensus_counts,
    )


@router.post("", response_model=ProjectOut)
def create_project(
    payload: ProjectCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a labeling project and scan the volume or Delta table for images."""
    existing = db.query(LabelingProject).filter_by(name=payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Project '{payload.name}' already exists.")

    if payload.task_type not in ("classification", "detection", "segmentation"):
        raise HTTPException(
            status_code=400,
            detail=(
                "task_type must be 'classification', 'detection' or "
                f"'segmentation', got '{payload.task_type}'."
            ),
        )

    user_email = get_user_email(request)
    record_current_user(db, request)
    validate_active_project_users(
        db,
        payload.consensus_reviewer_emails + payload.consensus_labeller_emails,
    )
    # Do not silently attach SERVING_ENDPOINT from env. The create form treats
    # model assist as optional; the UI can opt in via /inference-defaults.
    serving_ep = (payload.serving_endpoint or "").strip() or None
    ep_config = dict(payload.endpoint_config or {})
    if "use_data_plane" not in ep_config:
        env_dp = os.environ.get("USE_SERVING_DATA_PLANE", "").strip().lower()
        if env_dp in ("1", "true", "yes", "on"):
            ep_config["use_data_plane"] = True
    if "adapter" not in ep_config:
        env_adapter = os.environ.get("SERVING_ENDPOINT_ADAPTER", "").strip()
        if env_adapter:
            ep_config["adapter"] = env_adapter
    source_type = payload.source_type or "volume"
    project = LabelingProject(
        name=payload.name,
        description=payload.description,
        task_type=payload.task_type,
        class_list=payload.class_list,
        source_volume=(payload.source_volume or "").strip(),
        source_type=source_type,
        source_table=(payload.source_table or "").strip() or None,
        image_path_column=(payload.image_path_column or None),
        source_filter=(payload.source_filter or "").strip() or None,
        serving_endpoint=serving_ep,
        endpoint_config=ep_config or None,
        consensus_enabled=payload.consensus_enabled,
        consensus_panel_size=payload.consensus_panel_size,
        consensus_coverage_percent=payload.consensus_coverage_percent,
        consensus_quorum=payload.consensus_quorum,
        consensus_iou_threshold=payload.consensus_iou_threshold,
        consensus_agreement_threshold=payload.consensus_agreement_threshold,
        consensus_reviewer_emails=payload.consensus_reviewer_emails,
        consensus_labeller_emails=payload.consensus_labeller_emails,
        scan_include_subfolders=bool(payload.scan_include_subfolders),
        scan_max_depth=clamp_scan_max_depth(payload.scan_max_depth),
        created_by=user_email,
    )
    db.add(project)
    db.flush()

    try:
        scan_result = _scan_project_source(db, project)
    except TableSourceError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e

    if project.consensus_enabled:
        from ..consensus import ensure_consensus_cases
        ensure_consensus_cases(db, project)

    if _env_truthy("PRE_ANNOTATE_ON_IMPORT"):
        from ..inference import check_endpoint_health, resolve_endpoint
        from ..preannotate import run_preannotate_for_samples

        ep = resolve_endpoint(project)
        if ep and check_endpoint_health(ep).get("status") == "ready":
            try:
                max_n = int(os.environ.get("PRE_ANNOTATE_ON_IMPORT_MAX_SAMPLES", "50"))
            except ValueError:
                max_n = 50
            q = (
                db.query(ProjectSample)
                .filter_by(project_id=project.id, status="unlabeled")
                .order_by(ProjectSample.id)
            )
            if max_n > 0:
                q = q.limit(max_n)
            samples = q.all()
            if samples:
                try:
                    run_preannotate_for_samples(db, project, samples, min_confidence=None)
                except Exception as e:
                    log.warning("PRE_ANNOTATE_ON_IMPORT failed: %s", e)

    db.commit()
    db.refresh(project)

    return _project_out(project, scan_result.added, 0, user_email, db)


@router.get("", response_model=list[ProjectOut])
def list_projects(
    request: Request,
    include_archived: bool = Query(False),
    db: Session = Depends(get_db),
):
    """List projects with sample counts. Archived projects are hidden unless requested."""
    q = db.query(LabelingProject)
    if not include_archived:
        q = q.filter(LabelingProject.archived_at.is_(None))
    projects = q.order_by(LabelingProject.created_at.desc()).all()
    result = []
    for p in projects:
        total = db.query(ProjectSample).filter_by(project_id=p.id).count()
        labeled = db.query(ProjectSample).filter_by(project_id=p.id, status="labeled").count()
        result.append(_project_out(p, total, labeled, get_user_email(request), db))
    return result


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, request: Request, db: Session = Depends(get_db)):
    """Get a single project."""
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    total = db.query(ProjectSample).filter_by(project_id=p.id).count()
    labeled = db.query(ProjectSample).filter_by(project_id=p.id, status="labeled").count()
    return _project_out(p, total, labeled, get_user_email(request), db)


@router.post("/{project_id}/sync-source", response_model=SourceSyncOut)
def sync_project_source(project_id: int, db: Session = Depends(get_db)):
    """Pick up new images from the project's volume folder or Delta table.

    Does not delete samples or annotations. Rows Auto Loader already ingested
    into this project are skipped.
    """
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    try:
        result = _scan_project_source(db, p)
    except TableSourceError as e:
        db.rollback()
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    if p.consensus_enabled:
        from ..consensus import ensure_consensus_cases
        ensure_consensus_cases(db, p)
    db.commit()
    total = db.query(ProjectSample).filter_by(project_id=p.id).count()
    labeled = db.query(ProjectSample).filter_by(project_id=p.id, status="labeled").count()
    return SourceSyncOut(
        added=result.added,
        skipped_existing=result.skipped_existing,
        sample_count=total,
        labeled_count=labeled,
        source_type=_source_type(p),
        source_table=getattr(p, "source_table", None),
        source_volume=p.source_volume or "",
    )


@router.post("/{project_id}/classes")
def add_project_class(
    project_id: int,
    body: dict,
    request: Request,
    db: Session = Depends(get_db),
):
    """Add a new class to a project's class_list."""
    class_name = (body.get("class_name") or "").strip()
    if not class_name:
        raise HTTPException(status_code=400, detail="class_name is required.")
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    if p.consensus_enabled:
        from ..consensus import require_project_owner
        require_project_owner(p, get_user_email(request))
    current = list(p.class_list or [])
    if class_name in current:
        raise HTTPException(status_code=409, detail=f"Class '{class_name}' already exists.")
    current.append(class_name)
    p.class_list = current
    db.commit()
    db.refresh(p)
    return {"class_list": p.class_list}


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Update editable fields on a project."""
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    record_current_user(db, request)

    consensus_fields = (
        "consensus_enabled",
        "consensus_panel_size",
        "consensus_coverage_percent",
        "consensus_quorum",
        "consensus_iou_threshold",
        "consensus_agreement_threshold",
        "consensus_reviewer_emails",
        "consensus_labeller_emails",
    )
    consensus_changed = any(
        getattr(payload, field) is not None
        and getattr(payload, field) != getattr(p, field)
        for field in consensus_fields
    )
    structural_consensus_changed = any(
        getattr(payload, field) is not None
        and getattr(payload, field) != getattr(p, field)
        for field in (
            "consensus_enabled",
            "consensus_panel_size",
            "consensus_coverage_percent",
            "consensus_quorum",
            "consensus_iou_threshold",
            "consensus_agreement_threshold",
        )
    )
    if consensus_changed:
        from ..consensus import require_project_owner
        require_project_owner(p, get_user_email(request))

        effective_reviewers = (
            payload.consensus_reviewer_emails
            if payload.consensus_reviewer_emails is not None
            else list(p.consensus_reviewer_emails or [])
        )
        effective_labellers = (
            payload.consensus_labeller_emails
            if payload.consensus_labeller_emails is not None
            else list(p.consensus_labeller_emails or [])
        )
        overlap = set(effective_reviewers) & set(effective_labellers)
        if overlap:
            raise HTTPException(
                status_code=422,
                detail=(
                    "A person cannot be both a reviewer and a labeller: "
                    + ", ".join(sorted(overlap))
                ),
            )
        validate_active_project_users(
            db,
            list(effective_reviewers) + list(effective_labellers),
        )

        panel = payload.consensus_panel_size or p.consensus_panel_size
        quorum = payload.consensus_quorum or p.consensus_quorum
        if quorum > panel:
            raise HTTPException(
                status_code=422,
                detail="consensus_quorum cannot exceed consensus_panel_size.",
            )
        active_tasks = (
            db.query(LabelingTask)
            .filter(
                LabelingTask.project_id == project_id,
                LabelingTask.assigned_to.isnot(None),
            )
            .count()
        )
        if structural_consensus_changed and active_tasks and not payload.reset_consensus_unstarted:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Consensus work has started. Set reset_consensus_unstarted=true "
                    "to apply settings only to work that has not started."
                ),
            )
        if payload.consensus_enabled is False and active_tasks:
            raise HTTPException(
                status_code=409,
                detail="Consensus cannot be disabled after labeling work has started.",
            )

    scan_flags_changed = False
    if payload.scan_include_subfolders is not None:
        include = bool(payload.scan_include_subfolders)
        if include != bool(getattr(p, "scan_include_subfolders", False)):
            scan_flags_changed = True
        p.scan_include_subfolders = include
    if payload.scan_max_depth is not None:
        depth = clamp_scan_max_depth(payload.scan_max_depth)
        if depth != (getattr(p, "scan_max_depth", 3) or 3):
            scan_flags_changed = True
        p.scan_max_depth = depth

    volume_changed = (
        payload.source_volume is not None
        and payload.source_volume != p.source_volume
        and _source_type(p) == "volume"
        and (payload.source_type or _source_type(p)) == "volume"
    )
    table_changed = _table_source_changed(p, payload)
    if volume_changed or table_changed:
        if not payload.confirm_source_change:
            raise HTTPException(
                status_code=400,
                detail="Changing the image source will delete all samples and annotations. "
                       "Set confirm_source_change=true to proceed.",
            )
        for case in db.query(ConsensusCase).filter_by(project_id=project_id).all():
            db.delete(case)
        db.flush()
        db.query(Annotation).filter_by(project_id=project_id).delete()
        db.query(ProjectSample).filter_by(project_id=project_id).delete()
        if payload.source_type is not None:
            p.source_type = payload.source_type
        if payload.source_table is not None:
            p.source_table = (payload.source_table or "").strip() or None
        if payload.image_path_column is not None:
            p.image_path_column = payload.image_path_column
        if payload.source_filter is not None:
            p.source_filter = (payload.source_filter or "").strip() or None
        if payload.source_volume is not None:
            p.source_volume = payload.source_volume
        try:
            _scan_project_source(db, p)
        except TableSourceError as e:
            db.rollback()
            raise HTTPException(status_code=e.status_code, detail=str(e)) from e
        except ValueError as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(e)) from e
        if p.consensus_enabled:
            from ..consensus import ensure_consensus_cases
            ensure_consensus_cases(db, p)
    elif scan_flags_changed and _source_type(p) == "volume":
        try:
            _scan_project_source(db, p)
        except TableSourceError as e:
            db.rollback()
            raise HTTPException(status_code=e.status_code, detail=str(e)) from e
        except ValueError as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(e)) from e
        if p.consensus_enabled:
            from ..consensus import ensure_consensus_cases
            ensure_consensus_cases(db, p)

    if payload.name is not None:
        existing = db.query(LabelingProject).filter(
            LabelingProject.name == payload.name,
            LabelingProject.id != project_id,
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Project '{payload.name}' already exists.")
        p.name = payload.name

    if payload.description is not None:
        p.description = payload.description

    if payload.class_list is not None:
        if p.consensus_enabled:
            from ..consensus import require_project_owner
            require_project_owner(p, get_user_email(request))
            if (
                db.query(LabelingTask)
                .filter_by(project_id=project_id, status="submitted")
                .count()
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Classes cannot change after consensus votes have been submitted.",
                )
        p.class_list = payload.class_list

    if payload.serving_endpoint is not None:
        p.serving_endpoint = payload.serving_endpoint or None

    if payload.endpoint_config is not None:
        p.endpoint_config = payload.endpoint_config or None

    was_consensus_enabled = p.consensus_enabled
    for field in consensus_fields:
        value = getattr(payload, field)
        if value is not None:
            setattr(p, field, value)
    db.flush()

    if consensus_changed:
        from ..consensus import (
            delete_unstarted_cases,
            ensure_consensus_cases,
        )
        if not p.consensus_enabled:
            delete_unstarted_cases(db, p.id)
        elif not was_consensus_enabled:
            ensure_consensus_cases(db, p)
        elif structural_consensus_changed:
            delete_unstarted_cases(db, p.id)
            ensure_consensus_cases(db, p)

    db.commit()
    db.refresh(p)

    total = db.query(ProjectSample).filter_by(project_id=p.id).count()
    labeled = db.query(ProjectSample).filter_by(project_id=p.id, status="labeled").count()
    return _project_out(p, total, labeled, get_user_email(request), db)


@router.post("/{project_id}/archive", response_model=ProjectOut)
def archive_project(
    project_id: int,
    request: Request,
    all_versions: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Hide a project from the default list. Labels and samples are kept."""
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    targets = _lineage_projects(db, p) if all_versions else [p]
    now = datetime.now(timezone.utc)
    for row in targets:
        _require_can_manage_project(row, request)
        if row.archived_at is None:
            row.archived_at = now
    db.commit()
    db.refresh(p)
    total = db.query(ProjectSample).filter_by(project_id=p.id).count()
    labeled = db.query(ProjectSample).filter_by(project_id=p.id, status="labeled").count()
    return _project_out(p, total, labeled, get_user_email(request), db)


@router.post("/{project_id}/unarchive", response_model=ProjectOut)
def unarchive_project(
    project_id: int,
    request: Request,
    all_versions: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Restore an archived project to the default list."""
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    targets = _lineage_projects(db, p) if all_versions else [p]
    for row in targets:
        _require_can_manage_project(row, request)
        row.archived_at = None
    db.commit()
    db.refresh(p)
    total = db.query(ProjectSample).filter_by(project_id=p.id).count()
    labeled = db.query(ProjectSample).filter_by(project_id=p.id, status="labeled").count()
    return _project_out(p, total, labeled, get_user_email(request), db)


@router.delete("/{project_id}")
def delete_project(
    project_id: int,
    request: Request,
    all_versions: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Delete a project and all associated samples/annotations/runs.

    Source images and exported training dataset folders on UC Volumes are kept.
    """
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    targets = _lineage_projects(db, p) if all_versions else [p]
    for row in targets:
        _require_can_manage_project(row, request)
    try:
        for row in targets:
            _purge_project_row(db, row)
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Could not delete this project because related records still reference it.",
        ) from e
    return {"detail": "Deleted."}


@router.post("/{project_id}/clone", response_model=ProjectOut)
def clone_project(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Clone a project as a new version with fresh empty annotations."""
    parent = db.query(LabelingProject).filter_by(id=project_id).first()
    if not parent:
        raise HTTPException(status_code=404, detail="Project not found.")

    root_id = parent.parent_project_id or parent.id
    max_version = db.query(func.max(LabelingProject.version)).filter(
        (LabelingProject.id == root_id) |
        (LabelingProject.parent_project_id == root_id)
    ).scalar() or 1
    new_version = max_version + 1

    base_name = parent.name
    for suffix in [f" v{v}" for v in range(new_version - 1, 0, -1)]:
        if base_name.endswith(suffix):
            base_name = base_name[: -len(suffix)]
            break
    new_name = f"{base_name} v{new_version}"

    if db.query(LabelingProject).filter_by(name=new_name).first():
        raise HTTPException(status_code=409, detail=f"Project '{new_name}' already exists.")

    user_email = get_user_email(request)
    new_project = LabelingProject(
        name=new_name,
        description=parent.description,
        task_type=parent.task_type,
        class_list=list(parent.class_list),
        source_volume=parent.source_volume,
        source_type=getattr(parent, "source_type", None) or "volume",
        source_table=getattr(parent, "source_table", None),
        image_path_column=getattr(parent, "image_path_column", None),
        source_filter=getattr(parent, "source_filter", None),
        serving_endpoint=parent.serving_endpoint,
        endpoint_config=parent.endpoint_config,
        consensus_enabled=bool(getattr(parent, "consensus_enabled", False)),
        consensus_panel_size=getattr(parent, "consensus_panel_size", 3) or 3,
        consensus_coverage_percent=getattr(parent, "consensus_coverage_percent", 100),
        consensus_quorum=getattr(parent, "consensus_quorum", 2) or 2,
        consensus_iou_threshold=getattr(parent, "consensus_iou_threshold", 0.5),
        consensus_agreement_threshold=getattr(
            parent, "consensus_agreement_threshold", 0.5,
        ),
        consensus_reviewer_emails=list(
            getattr(parent, "consensus_reviewer_emails", None) or []
        ),
        consensus_labeller_emails=list(
            getattr(parent, "consensus_labeller_emails", None) or []
        ),
        scan_include_subfolders=bool(getattr(parent, "scan_include_subfolders", False)),
        scan_max_depth=getattr(parent, "scan_max_depth", 3) or 3,
        created_by=user_email,
        version=new_version,
        parent_project_id=root_id,
    )
    db.add(new_project)
    db.flush()

    parent_samples = db.query(ProjectSample).filter_by(project_id=parent.id).all()
    sample_count = 0
    for s in parent_samples:
        db.add(ProjectSample(
            project_id=new_project.id,
            filepath=s.filepath,
            filename=s.filename,
        ))
        sample_count += 1

    if new_project.consensus_enabled:
        from ..consensus import ensure_consensus_cases
        ensure_consensus_cases(db, new_project)

    db.commit()
    db.refresh(new_project)

    return _project_out(new_project, sample_count, 0, user_email, db)


@router.get("/{project_id}/stats", response_model=ProjectStats)
def project_stats(project_id: int, db: Session = Depends(get_db)):
    """Detailed stats for a project."""
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")

    total = db.query(ProjectSample).filter_by(project_id=project_id).count()
    labeled = db.query(ProjectSample).filter_by(project_id=project_id, status="labeled").count()
    skipped = db.query(ProjectSample).filter_by(project_id=project_id, status="skipped").count()
    pre_labeled = db.query(ProjectSample).filter_by(project_id=project_id, status="pre_labeled").count()
    unlabeled = total - labeled - skipped - pre_labeled

    if p.consensus_enabled:
        user_rows = (
            db.query(LabelingTask.assigned_to, func.count(LabelingTask.id))
            .filter(
                LabelingTask.project_id == project_id,
                LabelingTask.status == "submitted",
            )
            .group_by(LabelingTask.assigned_to)
            .all()
        )
    else:
        user_rows = (
            db.query(Annotation.created_by, func.count(Annotation.id))
            .filter(Annotation.project_id == project_id)
            .filter(Annotation.is_draft.is_(False))
            .group_by(Annotation.created_by)
            .all()
        )
    skip_rows = (
        db.query(ProjectSample.locked_by, func.count(ProjectSample.id))
        .filter(
            ProjectSample.project_id == project_id,
            ProjectSample.status == "skipped",
        )
        .group_by(ProjectSample.locked_by)
        .all()
    )
    skip_map = {row[0]: row[1] for row in skip_rows if row[0]}
    per_user = []
    seen_users = set()
    for user, count in user_rows:
        per_user.append({"user": user or "unknown", "labeled": count, "skipped": skip_map.get(user, 0)})
        seen_users.add(user)
    for user, count in skip_rows:
        if user and user not in seen_users:
            per_user.append({"user": user, "labeled": 0, "skipped": count})

    try:
        embedded = (
            db.query(ProjectSample)
            .filter(ProjectSample.project_id == project_id, ProjectSample.embedding.isnot(None))
            .count()
        )
    except Exception:
        db.rollback()
        embedded = 0

    return ProjectStats(
        total=total, labeled=labeled, unlabeled=unlabeled,
        skipped=skipped, pre_labeled=pre_labeled, embedded=embedded,
        per_user=per_user,
    )


@router.get("/{project_id}/stats/detailed", response_model=DetailedProjectStats)
def project_stats_detailed(project_id: int, db: Session = Depends(get_db)):
    """Rich stats for the progress dashboard."""
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")

    total = db.query(ProjectSample).filter_by(project_id=project_id).count()
    labeled = db.query(ProjectSample).filter_by(project_id=project_id, status="labeled").count()
    skipped = db.query(ProjectSample).filter_by(project_id=project_id, status="skipped").count()
    unlabeled = total - labeled - skipped

    # Per-class counts (human-confirmed only; excludes model drafts)
    class_rows = (
        db.query(Annotation.label, func.count(Annotation.id))
        .filter(Annotation.project_id == project_id)
        .filter(Annotation.is_draft.is_(False))
        .group_by(Annotation.label)
        .all()
    )
    class_map = {row[0]: row[1] for row in class_rows}
    per_class = [
        ClassCount(label=cls, count=class_map.get(cls, 0))
        for cls in (p.class_list or [])
    ]

    # Daily velocity — last 30 days, counting distinct samples per day
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    if p.consensus_enabled:
        vel_rows = (
            db.query(
                func.date(LabelingTask.submitted_at).label("day"),
                func.count(LabelingTask.id).label("cnt"),
            )
            .filter(
                LabelingTask.project_id == project_id,
                LabelingTask.status == "submitted",
                LabelingTask.submitted_at >= cutoff,
            )
            .group_by(func.date(LabelingTask.submitted_at))
            .order_by(func.date(LabelingTask.submitted_at))
            .all()
        )
    else:
        vel_rows = (
            db.query(
                func.date(Annotation.created_at).label("day"),
                func.count(distinct(Annotation.sample_id)).label("cnt"),
            )
            .filter(
                Annotation.project_id == project_id,
                Annotation.created_at >= cutoff,
                Annotation.is_draft.is_(False),
            )
            .group_by(func.date(Annotation.created_at))
            .order_by(func.date(Annotation.created_at))
            .all()
        )
    daily_velocity = [
        DailyVelocity(date=str(row.day), count=row.cnt)
        for row in vel_rows
    ]

    # Per-user breakdown (reuse logic from basic stats)
    if p.consensus_enabled:
        user_rows = (
            db.query(LabelingTask.assigned_to, func.count(LabelingTask.id))
            .filter(
                LabelingTask.project_id == project_id,
                LabelingTask.status == "submitted",
            )
            .group_by(LabelingTask.assigned_to)
            .all()
        )
    else:
        user_rows = (
            db.query(Annotation.created_by, func.count(Annotation.id))
            .filter(Annotation.project_id == project_id)
            .filter(Annotation.is_draft.is_(False))
            .group_by(Annotation.created_by)
            .all()
        )
    skip_rows = (
        db.query(ProjectSample.locked_by, func.count(ProjectSample.id))
        .filter(
            ProjectSample.project_id == project_id,
            ProjectSample.status == "skipped",
        )
        .group_by(ProjectSample.locked_by)
        .all()
    )
    skip_map = {row[0]: row[1] for row in skip_rows if row[0]}
    per_user: list[dict] = []
    seen_users: set[str] = set()
    for user, count in user_rows:
        per_user.append({"user": user or "unknown", "labeled": count, "skipped": skip_map.get(user, 0)})
        seen_users.add(user)
    for user, count in skip_rows:
        if user and user not in seen_users:
            per_user.append({"user": user, "labeled": 0, "skipped": count})

    # Average daily rate (last 7 days) — count distinct samples, not annotation rows
    week_cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    if p.consensus_enabled:
        week_count = (
            db.query(func.count(LabelingTask.id))
            .filter(
                LabelingTask.project_id == project_id,
                LabelingTask.status == "submitted",
                LabelingTask.submitted_at >= week_cutoff,
            )
            .scalar()
        ) or 0
        first_ann = (
            db.query(func.min(LabelingTask.submitted_at))
            .filter(
                LabelingTask.project_id == project_id,
                LabelingTask.status == "submitted",
            )
            .scalar()
        )
    else:
        week_count = (
            db.query(func.count(distinct(Annotation.sample_id)))
            .filter(
                Annotation.project_id == project_id,
                Annotation.created_at >= week_cutoff,
                Annotation.is_draft.is_(False),
            )
            .scalar()
        ) or 0
        first_ann = (
            db.query(func.min(Annotation.created_at))
            .filter(Annotation.project_id == project_id)
            .filter(Annotation.is_draft.is_(False))
            .scalar()
        )
    days_active = 7
    if first_ann is not None:
        days_active = max(1, min(7, (date.today() - first_ann.date()).days))
    avg_daily_rate = round(week_count / days_active, 1) if week_count else 0.0

    estimated_completion_date = None
    if avg_daily_rate > 0 and unlabeled > 0:
        days_remaining = unlabeled / avg_daily_rate
        est = date.today() + timedelta(days=math.ceil(days_remaining))
        estimated_completion_date = est.isoformat()

    return DetailedProjectStats(
        total=total,
        labeled=labeled,
        unlabeled=unlabeled,
        skipped=skipped,
        per_class=per_class,
        daily_velocity=daily_velocity,
        per_user=per_user,
        avg_daily_rate=avg_daily_rate,
        estimated_completion_date=estimated_completion_date,
    )
