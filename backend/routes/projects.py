"""
Project CRUD routes.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..deps import get_db, get_user_email
from ..models import LabelingProject, ProjectSample, Annotation
from ..schemas import ProjectCreate, ProjectUpdate, ProjectOut, ProjectStats
from ..volumes import scan_volume_for_samples

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _project_out(p, total=None, labeled=None):
    """Build a ProjectOut from a LabelingProject model instance."""
    return ProjectOut(
        id=p.id,
        name=p.name,
        description=p.description or "",
        task_type=p.task_type,
        class_list=p.class_list,
        source_volume=p.source_volume,
        created_by=p.created_by,
        created_at=p.created_at,
        sample_count=total if total is not None else 0,
        labeled_count=labeled if labeled is not None else 0,
        version=p.version or 1,
        parent_project_id=p.parent_project_id,
    )


@router.post("", response_model=ProjectOut)
def create_project(
    payload: ProjectCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a labeling project and scan the source volume for images."""
    existing = db.query(LabelingProject).filter_by(name=payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Project '{payload.name}' already exists.")

    user_email = get_user_email(request)
    project = LabelingProject(
        name=payload.name,
        description=payload.description,
        task_type=payload.task_type,
        class_list=payload.class_list,
        source_volume=payload.source_volume,
        created_by=user_email,
    )
    db.add(project)
    db.flush()

    sample_count = scan_volume_for_samples(db, project.id, payload.source_volume)

    db.commit()
    db.refresh(project)

    return _project_out(project, sample_count, 0)


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    """List all projects with sample counts."""
    projects = db.query(LabelingProject).order_by(LabelingProject.created_at.desc()).all()
    result = []
    for p in projects:
        total = db.query(ProjectSample).filter_by(project_id=p.id).count()
        labeled = db.query(ProjectSample).filter_by(project_id=p.id, status="labeled").count()
        result.append(_project_out(p, total, labeled))
    return result


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)):
    """Get a single project."""
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    total = db.query(ProjectSample).filter_by(project_id=p.id).count()
    labeled = db.query(ProjectSample).filter_by(project_id=p.id, status="labeled").count()
    return _project_out(p, total, labeled)


@router.post("/{project_id}/classes")
def add_project_class(project_id: int, body: dict, db: Session = Depends(get_db)):
    """Add a new class to a project's class_list."""
    class_name = (body.get("class_name") or "").strip()
    if not class_name:
        raise HTTPException(status_code=400, detail="class_name is required.")
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
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
    db: Session = Depends(get_db),
):
    """Update editable fields on a project."""
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")

    if payload.source_volume is not None and payload.source_volume != p.source_volume:
        if not payload.confirm_source_change:
            raise HTTPException(
                status_code=400,
                detail="Changing source volume will delete all samples and annotations. "
                       "Set confirm_source_change=true to proceed.",
            )
        db.query(Annotation).filter_by(project_id=project_id).delete()
        db.query(ProjectSample).filter_by(project_id=project_id).delete()
        p.source_volume = payload.source_volume

        scan_volume_for_samples(db, p.id, payload.source_volume)

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
        p.class_list = payload.class_list

    db.commit()
    db.refresh(p)

    total = db.query(ProjectSample).filter_by(project_id=p.id).count()
    labeled = db.query(ProjectSample).filter_by(project_id=p.id, status="labeled").count()
    return _project_out(p, total, labeled)


@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    """Delete a project and all associated samples/annotations."""
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    db.delete(p)
    db.commit()
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

    db.commit()
    db.refresh(new_project)

    return _project_out(new_project, sample_count, 0)


@router.get("/{project_id}/stats", response_model=ProjectStats)
def project_stats(project_id: int, db: Session = Depends(get_db)):
    """Detailed stats for a project."""
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")

    total = db.query(ProjectSample).filter_by(project_id=project_id).count()
    labeled = db.query(ProjectSample).filter_by(project_id=project_id, status="labeled").count()
    skipped = db.query(ProjectSample).filter_by(project_id=project_id, status="skipped").count()
    unlabeled = total - labeled - skipped

    user_rows = (
        db.query(Annotation.created_by, func.count(Annotation.id))
        .filter(Annotation.project_id == project_id)
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

    return ProjectStats(
        total=total, labeled=labeled, unlabeled=unlabeled,
        skipped=skipped, per_user=per_user,
    )
