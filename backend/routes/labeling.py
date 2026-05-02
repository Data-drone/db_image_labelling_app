"""
Labeling workflow routes — next sample, annotate, skip, image serving.
"""

import io
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload, subqueryload

from ..deps import get_db, get_user_email, LOCK_TIMEOUT
from ..models import ProjectSample, Annotation, AnnotationHistory
from ..preannotate import refresh_sample_status_after_annotation_change
from ..schemas import (
    SampleOut, SamplePage,
    AnnotationCreate, AnnotationBatchCreate, AnnotationOut,
    AnnotationHistoryOut,
    BulkDraftSampleIds,
    DraftMutationResult,
    SimilarSampleOut,
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
            ProjectSample.status.in_(["unlabeled", "pre_labeled"]),
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
    """Save an annotation and mark the sample as labeled.

    For classification, replaces the existing annotation (if any) so
    re-labeling works naturally.  History is recorded for every change.
    """
    sample = db.query(ProjectSample).filter_by(id=sample_id, project_id=project_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    user_email = get_user_email(request)

    existing = (
        db.query(Annotation)
        .filter_by(sample_id=sample_id, project_id=project_id, ann_type="classification")
        .all()
    )

    if existing:
        for old in existing:
            db.add(AnnotationHistory(
                sample_id=sample_id,
                project_id=project_id,
                action="update",
                old_label=old.label,
                new_label=payload.label,
                old_ann_type=old.ann_type,
                new_ann_type=payload.ann_type,
                old_bbox_json=old.bbox_json,
                new_bbox_json=payload.bbox_json,
                changed_by=user_email,
            ))
        db.query(Annotation).filter_by(
            sample_id=sample_id, project_id=project_id, ann_type="classification"
        ).delete()
    else:
        db.add(AnnotationHistory(
            sample_id=sample_id,
            project_id=project_id,
            action="create",
            old_label=None,
            new_label=payload.label,
            old_ann_type=None,
            new_ann_type=payload.ann_type,
            old_bbox_json=None,
            new_bbox_json=payload.bbox_json,
            changed_by=user_email,
        ))

    ann = Annotation(
        sample_id=sample_id,
        project_id=project_id,
        label=payload.label,
        ann_type=payload.ann_type,
        bbox_json=payload.bbox_json,
        is_draft=False,
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

    user_email = get_user_email(request)

    old_annotations = (
        db.query(Annotation)
        .filter_by(sample_id=sample_id, project_id=project_id)
        .all()
    )
    if old_annotations:
        for old in old_annotations:
            db.add(AnnotationHistory(
                sample_id=sample_id,
                project_id=project_id,
                action="delete",
                old_label=old.label,
                new_label=None,
                old_ann_type=old.ann_type,
                new_ann_type=None,
                old_bbox_json=old.bbox_json,
                new_bbox_json=None,
                changed_by=user_email,
            ))
        db.query(Annotation).filter_by(sample_id=sample_id, project_id=project_id).delete()

    created = []
    for ann in payload.annotations:
        db.add(AnnotationHistory(
            sample_id=sample_id,
            project_id=project_id,
            action="create",
            old_label=None,
            new_label=ann.label,
            old_ann_type=None,
            new_ann_type=ann.ann_type,
            old_bbox_json=None,
            new_bbox_json=ann.bbox_json,
            changed_by=user_email,
        ))
        a = Annotation(
            sample_id=sample_id,
            project_id=project_id,
            label=ann.label,
            ann_type=ann.ann_type,
            bbox_json=ann.bbox_json,
            is_draft=False,
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


@router.post(
    "/samples/{sample_id}/accept-drafts",
    response_model=DraftMutationResult,
)
def accept_drafts_for_sample(
    project_id: int,
    sample_id: int,
    db: Session = Depends(get_db),
):
    """Promote all draft annotations on this sample to confirmed (``is_draft=false``)."""
    sample = db.query(ProjectSample).filter_by(id=sample_id, project_id=project_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    rows = (
        db.query(Annotation)
        .filter_by(sample_id=sample_id, project_id=project_id)
        .filter(Annotation.is_draft.is_(True))
        .all()
    )
    for a in rows:
        a.is_draft = False
    refresh_sample_status_after_annotation_change(db, project_id, sample_id)
    db.commit()
    return DraftMutationResult(
        annotations_affected=len(rows),
        samples_touched=1 if rows else 0,
    )


@router.post(
    "/samples/{sample_id}/clear-drafts",
    response_model=DraftMutationResult,
)
def clear_drafts_for_sample(
    project_id: int,
    sample_id: int,
    db: Session = Depends(get_db),
):
    """Remove model draft annotations for this sample (``created_by`` like ``model:%``)."""
    sample = db.query(ProjectSample).filter_by(id=sample_id, project_id=project_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    rows = (
        db.query(Annotation)
        .filter_by(sample_id=sample_id, project_id=project_id)
        .filter(Annotation.is_draft.is_(True))
        .filter(Annotation.created_by.like("model:%"))
        .all()
    )
    n = len(rows)
    for a in rows:
        db.delete(a)
    refresh_sample_status_after_annotation_change(db, project_id, sample_id)
    db.commit()
    return DraftMutationResult(annotations_affected=n, samples_touched=1 if n else 0)


@router.post("/drafts/bulk-accept", response_model=DraftMutationResult)
def bulk_accept_drafts(
    project_id: int,
    payload: BulkDraftSampleIds,
    db: Session = Depends(get_db),
):
    """Accept drafts for the given sample IDs (same semantics as ``accept-drafts`` per sample)."""
    if not payload.sample_ids:
        return DraftMutationResult()

    touched = set()
    ann_count = 0
    for sid in payload.sample_ids:
        sample = db.query(ProjectSample).filter_by(id=sid, project_id=project_id).first()
        if not sample:
            continue
        rows = (
            db.query(Annotation)
            .filter_by(sample_id=sid, project_id=project_id)
            .filter(Annotation.is_draft.is_(True))
            .all()
        )
        if not rows:
            continue
        for a in rows:
            a.is_draft = False
        ann_count += len(rows)
        touched.add(sid)
        refresh_sample_status_after_annotation_change(db, project_id, sid)

    db.commit()
    return DraftMutationResult(
        annotations_affected=ann_count,
        samples_touched=len(touched),
    )


@router.post("/drafts/accept-all", response_model=DraftMutationResult)
def accept_all_drafts(project_id: int, db: Session = Depends(get_db)):
    """Confirm every draft annotation in the project."""
    rows = (
        db.query(Annotation)
        .filter_by(project_id=project_id)
        .filter(Annotation.is_draft.is_(True))
        .all()
    )
    touched = set()
    for a in rows:
        a.is_draft = False
        touched.add(a.sample_id)
    for sid in touched:
        refresh_sample_status_after_annotation_change(db, project_id, sid)
    db.commit()
    return DraftMutationResult(
        annotations_affected=len(rows),
        samples_touched=len(touched),
    )


@router.post("/drafts/clear-all", response_model=DraftMutationResult)
def clear_all_model_drafts(project_id: int, db: Session = Depends(get_db)):
    """Delete all model draft annotations project-wide."""
    rows = (
        db.query(Annotation)
        .filter_by(project_id=project_id)
        .filter(Annotation.is_draft.is_(True))
        .filter(Annotation.created_by.like("model:%"))
        .all()
    )
    touched = {a.sample_id for a in rows}
    n = len(rows)
    for a in rows:
        db.delete(a)
    for sid in touched:
        refresh_sample_status_after_annotation_change(db, project_id, sid)
    db.commit()
    return DraftMutationResult(annotations_affected=n, samples_touched=len(touched))


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


@router.get(
    "/samples/{sample_id}/history",
    response_model=list[AnnotationHistoryOut],
)
def get_sample_history(
    project_id: int,
    sample_id: int,
    db: Session = Depends(get_db),
):
    """Return annotation history for a sample, newest first."""
    rows = (
        db.query(AnnotationHistory)
        .filter_by(sample_id=sample_id, project_id=project_id)
        .order_by(AnnotationHistory.changed_at.desc())
        .all()
    )
    return [AnnotationHistoryOut.model_validate(r) for r in rows]


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
    label: Optional[str] = None,
    filename: Optional[str] = None,
    labeler: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Paginated sample list with optional filters (AND logic)."""
    query = db.query(ProjectSample).filter(ProjectSample.project_id == project_id)

    if status:
        query = query.filter(ProjectSample.status == status)
    if filename:
        escaped = filename.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(ProjectSample.filename.ilike(f"%{escaped}%", escape="\\"))

    needs_join = label or labeler
    if needs_join:
        query = query.join(Annotation, Annotation.sample_id == ProjectSample.id)
        if label:
            query = query.filter(Annotation.label == label)
        if labeler:
            query = query.filter(Annotation.created_by == labeler)
        query = query.distinct()

    if needs_join:
        query = query.options(subqueryload(ProjectSample.annotations))
    else:
        query = query.options(joinedload(ProjectSample.annotations))

    total = query.count()
    items = query.order_by(ProjectSample.id).offset(page * page_size).limit(page_size).all()

    def to_out(s):
        out = SampleOut.model_validate(s)
        out.labels = list({a.label for a in s.annotations})
        return out

    return SamplePage(
        items=[to_out(s) for s in items],
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


def _find_similar_pgvector(
    db: Session, project_id: int, target, limit: int,
) -> list[SimilarSampleOut]:
    """Use pgvector cosine distance operator for indexed ANN search."""
    from sqlalchemy import text

    if target.embedding is None:
        return []
    query_vec = "[" + ",".join(str(float(v)) for v in target.embedding) + "]"

    rows = db.execute(
        text(
            "SELECT id, filename, status, 1 - (embedding_vec <=> :q) AS similarity "
            "FROM project_samples "
            "WHERE project_id = :pid AND embedding_vec IS NOT NULL AND id != :sid "
            "ORDER BY embedding_vec <=> :q "
            "LIMIT :lim"
        ),
        {"q": query_vec, "pid": project_id, "sid": target.id, "lim": limit},
    ).fetchall()

    return [
        SimilarSampleOut(sample_id=r.id, filename=r.filename, similarity=round(r.similarity, 4), status=r.status)
        for r in rows
    ]


def _find_similar_python(
    db: Session, project_id: int, target, limit: int,
) -> list[SimilarSampleOut]:
    """Fallback: load JSON embeddings and compute cosine similarity in Python."""
    candidates = (
        db.query(ProjectSample.id, ProjectSample.filename, ProjectSample.status, ProjectSample.embedding)
        .filter(
            ProjectSample.project_id == project_id,
            ProjectSample.embedding.isnot(None),
            ProjectSample.id != target.id,
        )
        .all()
    )
    if not candidates:
        return []

    target_emb = target.embedding
    norm_t = sum(x * x for x in target_emb) ** 0.5
    if norm_t == 0:
        return []

    scored = []
    for cid, fname, status, emb in candidates:
        dot = sum(a * b for a, b in zip(target_emb, emb))
        norm_c = sum(x * x for x in emb) ** 0.5
        if norm_c == 0:
            continue
        sim = dot / (norm_t * norm_c)
        scored.append((sim, cid, fname, status))

    scored.sort(reverse=True)
    return [
        SimilarSampleOut(sample_id=sid, filename=fn, similarity=round(s, 4), status=st)
        for s, sid, fn, st in scored[:limit]
    ]


@router.get("/samples/{sample_id}/similar", response_model=list[SimilarSampleOut])
def find_similar_samples(
    project_id: int,
    sample_id: int,
    limit: int = Query(24, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Find visually similar samples by cosine similarity of embeddings.

    Uses pgvector (indexed ANN) on Postgres, falls back to in-Python cosine on SQLite.
    """
    target = db.query(ProjectSample).filter_by(id=sample_id, project_id=project_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Sample not found.")
    if target.embedding is None:
        raise HTTPException(status_code=400, detail="Sample has no embedding. Generate embeddings first.")

    try:
        dialect = db.bind.dialect.name
    except Exception:
        dialect = "sqlite"

    if dialect == "postgresql":
        try:
            return _find_similar_pgvector(db, project_id, target, limit)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("pgvector similarity failed, falling back to Python: %s", exc)

    return _find_similar_python(db, project_id, target, limit)
