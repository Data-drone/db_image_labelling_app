"""
FastAPI backend for CV Explorer — Phase 1.

Project-centric labeling API with Lakebase backend.
"""

import io
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import (
    Base, LabelingProject, ProjectSample, Annotation, init_db,
)
from .schemas import (
    ProjectCreate, ProjectOut, ProjectStats,
    SampleOut, SamplePage,
    AnnotationCreate, AnnotationOut,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(
    title="CV Explorer API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
LOCK_TIMEOUT = timedelta(minutes=5)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _is_volume_path(path: str) -> bool:
    return path.startswith("/Volumes/")


def _get_workspace_client():
    if not hasattr(_get_workspace_client, "_client"):
        from databricks.sdk import WorkspaceClient
        _get_workspace_client._client = WorkspaceClient()
    return _get_workspace_client._client


def _download_volume_file(path: str) -> Optional[bytes]:
    try:
        w = _get_workspace_client()
        resp = w.files.download(path)
        return resp.contents.read()
    except Exception:
        return None


def _get_user_email(request: Request) -> str:
    """Extract user email from Databricks Apps headers, fallback to 'anonymous'."""
    return (
        request.headers.get("X-Forwarded-Email")
        or request.headers.get("X-Forwarded-User")
        or "anonymous"
    )


# ---------------------------------------------------------------------------
# Database dependency
# ---------------------------------------------------------------------------
_engine = None
_session_factory = None


def get_db():
    """Yield a database session."""
    db = _session_factory()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@app.on_event("startup")
def startup():
    global _engine, _session_factory

    # Try Lakebase first, fallback to SQLite for local dev
    try:
        from .lakebase import init_lakebase
        _engine = init_lakebase()
        log.info("Connected to Lakebase")
    except Exception as e:
        log.warning("Lakebase init failed (%s), falling back to SQLite", e)
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        db_url = os.environ.get("DATABASE_URL", "sqlite:////tmp/cv_explorer.db")
        _engine = create_engine(db_url, echo=False)
        _session_factory = sessionmaker(bind=_engine)

    if _session_factory is None:
        from sqlalchemy.orm import sessionmaker
        _session_factory = sessionmaker(bind=_engine)

    # Create tables
    Base.metadata.create_all(_engine)
    log.info("Database tables ready")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Projects CRUD (Step 4)
# ---------------------------------------------------------------------------
@app.post("/api/projects", response_model=ProjectOut)
def create_project(
    payload: ProjectCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create a labeling project and scan the source volume for images."""
    existing = db.query(LabelingProject).filter_by(name=payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Project '{payload.name}' already exists.")

    user_email = _get_user_email(request)
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

    # Scan source volume for images
    sample_count = 0
    volume_path = payload.source_volume.rstrip("/")
    if _is_volume_path(volume_path):
        try:
            w = _get_workspace_client()
            for entry in w.files.list_directory_contents(volume_path + "/"):
                if not entry.is_directory:
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in IMAGE_EXTENSIONS:
                        fpath = volume_path + "/" + entry.name
                        db.add(ProjectSample(
                            project_id=project.id,
                            filepath=fpath,
                            filename=entry.name,
                        ))
                        sample_count += 1
        except Exception as e:
            log.warning("Volume scan failed: %s", e)
    elif os.path.isdir(volume_path):
        for fname in sorted(os.listdir(volume_path)):
            ext = os.path.splitext(fname)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                db.add(ProjectSample(
                    project_id=project.id,
                    filepath=os.path.join(volume_path, fname),
                    filename=fname,
                ))
                sample_count += 1

    db.commit()
    db.refresh(project)

    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description or "",
        task_type=project.task_type,
        class_list=project.class_list,
        source_volume=project.source_volume,
        created_by=project.created_by,
        created_at=project.created_at,
        sample_count=sample_count,
        labeled_count=0,
    )


@app.get("/api/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    """List all projects with sample counts."""
    projects = db.query(LabelingProject).order_by(LabelingProject.created_at.desc()).all()
    result = []
    for p in projects:
        total = db.query(ProjectSample).filter_by(project_id=p.id).count()
        labeled = db.query(ProjectSample).filter_by(project_id=p.id, status="labeled").count()
        result.append(ProjectOut(
            id=p.id,
            name=p.name,
            description=p.description or "",
            task_type=p.task_type,
            class_list=p.class_list,
            source_volume=p.source_volume,
            created_by=p.created_by,
            created_at=p.created_at,
            sample_count=total,
            labeled_count=labeled,
        ))
    return result


@app.get("/api/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)):
    """Get a single project."""
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    total = db.query(ProjectSample).filter_by(project_id=p.id).count()
    labeled = db.query(ProjectSample).filter_by(project_id=p.id, status="labeled").count()
    return ProjectOut(
        id=p.id,
        name=p.name,
        description=p.description or "",
        task_type=p.task_type,
        class_list=p.class_list,
        source_volume=p.source_volume,
        created_by=p.created_by,
        created_at=p.created_at,
        sample_count=total,
        labeled_count=labeled,
    )


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    """Delete a project and all associated samples/annotations."""
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    db.delete(p)
    db.commit()
    return {"detail": "Deleted."}


@app.get("/api/projects/{project_id}/stats", response_model=ProjectStats)
def project_stats(project_id: int, db: Session = Depends(get_db)):
    """Detailed stats for a project."""
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")

    total = db.query(ProjectSample).filter_by(project_id=project_id).count()
    labeled = db.query(ProjectSample).filter_by(project_id=project_id, status="labeled").count()
    skipped = db.query(ProjectSample).filter_by(project_id=project_id, status="skipped").count()
    unlabeled = total - labeled - skipped

    # Per-user breakdown
    user_rows = (
        db.query(
            Annotation.created_by,
            func.count(Annotation.id),
        )
        .filter(Annotation.project_id == project_id)
        .group_by(Annotation.created_by)
        .all()
    )
    skip_rows = (
        db.query(
            ProjectSample.locked_by,
            func.count(ProjectSample.id),
        )
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
        per_user.append({
            "user": user or "unknown",
            "labeled": count,
            "skipped": skip_map.get(user, 0),
        })
        seen_users.add(user)
    for user, count in skip_rows:
        if user and user not in seen_users:
            per_user.append({"user": user, "labeled": 0, "skipped": count})

    return ProjectStats(
        total=total,
        labeled=labeled,
        unlabeled=unlabeled,
        skipped=skipped,
        per_user=per_user,
    )


# ---------------------------------------------------------------------------
# Labeling Workflow (Step 5)
# ---------------------------------------------------------------------------
@app.get("/api/projects/{project_id}/next", response_model=Optional[SampleOut])
def get_next_sample(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Get the next unlabeled sample with lock-on-open."""
    now = datetime.now(timezone.utc)
    cutoff = now - LOCK_TIMEOUT

    sample = (
        db.query(ProjectSample)
        .filter(
            ProjectSample.project_id == project_id,
            ProjectSample.status == "unlabeled",
        )
        .filter(
            (ProjectSample.locked_by.is_(None)) | (ProjectSample.locked_at < cutoff)
        )
        .order_by(ProjectSample.id)
        .first()
    )

    if not sample:
        return None

    user_email = _get_user_email(request)
    sample.locked_by = user_email
    sample.locked_at = now
    db.commit()
    db.refresh(sample)

    return SampleOut.model_validate(sample)


@app.post(
    "/api/projects/{project_id}/samples/{sample_id}/annotate",
    response_model=AnnotationOut,
)
def annotate_sample(
    project_id: int,
    sample_id: int,
    payload: AnnotationCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Save an annotation and mark the sample as labeled."""
    sample = (
        db.query(ProjectSample)
        .filter_by(id=sample_id, project_id=project_id)
        .first()
    )
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    user_email = _get_user_email(request)
    ann = Annotation(
        sample_id=sample_id,
        project_id=project_id,
        label=payload.label,
        ann_type=payload.ann_type,
        bbox_json=payload.bbox_json,
        created_by=user_email,
    )
    db.add(ann)

    sample.status = "labeled"
    sample.locked_by = None
    sample.locked_at = None

    db.commit()
    db.refresh(ann)
    return AnnotationOut.model_validate(ann)


@app.post("/api/projects/{project_id}/samples/{sample_id}/skip")
def skip_sample(
    project_id: int,
    sample_id: int,
    db: Session = Depends(get_db),
):
    """Skip a sample."""
    sample = (
        db.query(ProjectSample)
        .filter_by(id=sample_id, project_id=project_id)
        .first()
    )
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    sample.status = "skipped"
    sample.locked_by = None
    sample.locked_at = None
    db.commit()
    return {"detail": "Skipped."}


@app.get("/api/projects/{project_id}/samples", response_model=SamplePage)
def list_project_samples(
    project_id: int,
    page: int = Query(0, ge=0),
    page_size: int = Query(24, ge=1, le=10000),
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Paginated sample list with optional status filter."""
    query = db.query(ProjectSample).filter(ProjectSample.project_id == project_id)
    if status:
        query = query.filter(ProjectSample.status == status)

    total = query.count()
    items = query.order_by(ProjectSample.id).offset(page * page_size).limit(page_size).all()

    return SamplePage(
        items=[SampleOut.model_validate(s) for s in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@app.get("/api/projects/{project_id}/samples/{sample_id}/image")
def serve_sample_image(
    project_id: int,
    sample_id: int,
    db: Session = Depends(get_db),
):
    """Serve the image file for a sample."""
    sample = (
        db.query(ProjectSample)
        .filter_by(id=sample_id, project_id=project_id)
        .first()
    )
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    if _is_volume_path(sample.filepath):
        data = _download_volume_file(sample.filepath)
        if data is None:
            raise HTTPException(status_code=404, detail="Image not found in volume.")
        return StreamingResponse(io.BytesIO(data), media_type="image/jpeg")
    else:
        if not os.path.exists(sample.filepath):
            raise HTTPException(status_code=404, detail="Image file not found.")
        return FileResponse(sample.filepath)


@app.get("/api/projects/{project_id}/samples/{sample_id}/thumbnail")
def serve_sample_thumbnail(
    project_id: int,
    sample_id: int,
    size: int = Query(300, ge=50, le=1000),
    db: Session = Depends(get_db),
):
    """Serve a resized thumbnail."""
    sample = (
        db.query(ProjectSample)
        .filter_by(id=sample_id, project_id=project_id)
        .first()
    )
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    from PIL import Image

    if _is_volume_path(sample.filepath):
        data = _download_volume_file(sample.filepath)
        if data is None:
            raise HTTPException(status_code=404, detail="Image not found in volume.")
        img = Image.open(io.BytesIO(data)).convert("RGB")
    else:
        if not os.path.exists(sample.filepath):
            raise HTTPException(status_code=404, detail="Image file not found.")
        img = Image.open(sample.filepath).convert("RGB")

    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# Volume browsing (kept from original — used by Create Project form)
# ---------------------------------------------------------------------------
@app.get("/api/catalogs")
def list_catalogs():
    try:
        w = _get_workspace_client()
        names = [c.name for c in w.catalogs.list()]
        return sorted(names)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/schemas")
def list_schemas(catalog: str = Query(...)):
    try:
        w = _get_workspace_client()
        return sorted(s.name for s in w.schemas.list(catalog_name=catalog))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/volumes")
def list_volumes(catalog: str = Query(...), schema: str = Query(...)):
    try:
        w = _get_workspace_client()
        return sorted(v.name for v in w.volumes.list(catalog_name=catalog, schema_name=schema))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/browse")
def browse_directory(path: str = Query(...)):
    if _is_volume_path(path):
        try:
            w = _get_workspace_client()
            folders, files = [], []
            for entry in w.files.list_directory_contents(path.rstrip("/") + "/"):
                if entry.is_directory:
                    folders.append({"name": entry.name, "image_count": 0})
                else:
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in IMAGE_EXTENSIONS:
                        files.append({"name": entry.name, "path": path.rstrip("/") + "/" + entry.name})
                    elif entry.name.endswith(".json"):
                        files.append({"name": entry.name, "path": path.rstrip("/") + "/" + entry.name})
            return {
                "path": path,
                "folders": sorted(folders, key=lambda x: x["name"]),
                "files": sorted(files, key=lambda x: x["name"]),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        if not os.path.isdir(path):
            raise HTTPException(status_code=404, detail=f"Directory not found: {path}")
        folders, files = [], []
        for entry in sorted(os.listdir(path)):
            full = os.path.join(path, entry)
            if os.path.isdir(full):
                folders.append({"name": entry, "image_count": 0})
            elif os.path.splitext(entry)[1].lower() in IMAGE_EXTENSIONS:
                files.append({"name": entry, "path": full})
        return {"path": path, "folders": folders, "files": files}


# ---------------------------------------------------------------------------
# Static file serving (React build)
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).parent.parent / "frontend" / "dist"

if STATIC_DIR.exists():
    from fastapi.staticfiles import StaticFiles
    from starlette.responses import HTMLResponse

    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="static-assets")

    @app.get("/vite.svg")
    def vite_svg():
        return FileResponse(str(STATIC_DIR / "vite.svg"))

    @app.get("/{path:path}")
    def serve_spa(path: str):
        if path.startswith("api/") or path.startswith("images/"):
            raise HTTPException(status_code=404)
        file_path = STATIC_DIR / path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return HTMLResponse(
            content=(STATIC_DIR / "index.html").read_text(),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
else:
    @app.get("/")
    def root():
        return {"message": "CV Explorer API. Frontend not built — run 'npm run build' in frontend/."}
