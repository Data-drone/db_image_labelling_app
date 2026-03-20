"""
Labeling workflow routes — next sample, annotate, skip, image serving.
"""

import io
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..deps import get_db, get_user_email, LOCK_TIMEOUT
from ..models import ProjectSample, Annotation
from ..schemas import (
    SampleOut, SamplePage,
    AnnotationCreate, AnnotationBatchCreate, AnnotationOut,
)
from ..volumes import read_image_bytes

router = APIRouter(prefix="/api/projects/{project_id}", tags=["labeling"])


@router.get("/next", response_model=Optional[SampleOut])
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

    user_email = get_user_email(request)
    sample.locked_by = user_email
    sample.locked_at = now
    db.commit()
    db.refresh(sample)

    return SampleOut.model_validate(sample)


@router.post(
    "/samples/{sample_id}/annotate",
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
    sample = db.query(ProjectSample).filter_by(id=sample_id, project_id=project_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    user_email = get_user_email(request)
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


@router.post(
    "/samples/{sample_id}/annotate-batch",
    response_model=list[AnnotationOut],
)
def annotate_sample_batch(
    project_id: int,
    sample_id: int,
    payload: AnnotationBatchCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Save multiple annotations for a sample in one transaction."""
    sample = db.query(ProjectSample).filter_by(id=sample_id, project_id=project_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    if not payload.annotations:
        raise HTTPException(status_code=400, detail="At least one annotation is required.")

    db.query(Annotation).filter_by(sample_id=sample_id, project_id=project_id).delete()

    user_email = get_user_email(request)
    created = []
    for ann in payload.annotations:
        a = Annotation(
            sample_id=sample_id,
            project_id=project_id,
            label=ann.label,
            ann_type=ann.ann_type,
            bbox_json=ann.bbox_json,
            created_by=user_email,
        )
        db.add(a)
        created.append(a)

    sample.status = "labeled"
    sample.locked_by = None
    sample.locked_at = None

    db.commit()
    for a in created:
        db.refresh(a)

    return [AnnotationOut.model_validate(a) for a in created]


@router.post("/samples/{sample_id}/skip")
def skip_sample(
    project_id: int,
    sample_id: int,
    db: Session = Depends(get_db),
):
    """Skip a sample."""
    sample = db.query(ProjectSample).filter_by(id=sample_id, project_id=project_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    sample.status = "skipped"
    sample.locked_by = None
    sample.locked_at = None
    db.commit()
    return {"detail": "Skipped."}


@router.get("/samples/{sample_id}", response_model=SampleOut)
def get_sample(
    project_id: int,
    sample_id: int,
    db: Session = Depends(get_db),
):
    """Get a single sample by ID with its annotations."""
    sample = db.query(ProjectSample).filter_by(id=sample_id, project_id=project_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")
    return SampleOut.model_validate(sample)


@router.get("/samples", response_model=SamplePage)
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
        total=total, page=page, page_size=page_size,
    )


@router.get("/samples/{sample_id}/image")
def serve_sample_image(
    project_id: int,
    sample_id: int,
    db: Session = Depends(get_db),
):
    """Serve the image file for a sample."""
    sample = db.query(ProjectSample).filter_by(id=sample_id, project_id=project_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    data = read_image_bytes(sample.filepath)
    if data is None:
        raise HTTPException(status_code=404, detail="Image not found.")
    return StreamingResponse(io.BytesIO(data), media_type="image/jpeg")


@router.get("/samples/{sample_id}/thumbnail")
def serve_sample_thumbnail(
    project_id: int,
    sample_id: int,
    size: int = Query(300, ge=50, le=1000),
    db: Session = Depends(get_db),
):
    """Serve a resized thumbnail."""
    sample = db.query(ProjectSample).filter_by(id=sample_id, project_id=project_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    from PIL import Image

    data = read_image_bytes(sample.filepath)
    if data is None:
        raise HTTPException(status_code=404, detail="Image not found.")
    img = Image.open(io.BytesIO(data)).convert("RGB")

    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/jpeg")
