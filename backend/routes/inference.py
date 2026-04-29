"""
Pre-annotation routes — predict, batch pre-annotate, endpoint health.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..deps import get_db
from ..inference import (
    resolve_endpoint,
    check_endpoint_health,
    predict_sample,
    get_default_endpoint,
)
from ..models import LabelingProject, ProjectSample, Annotation
from ..schemas import (
    PredictionOut,
    PreAnnotateRequest,
    PreAnnotateProgress,
    EndpointStatus,
)
from ..volumes import read_image_bytes

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["inference"])


def _get_project(project_id: int, db: Session) -> LabelingProject:
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")
    return p


@router.get("/endpoint-status", response_model=EndpointStatus)
def get_endpoint_status(project_id: int, db: Session = Depends(get_db)):
    """Check the health of the serving endpoint configured for this project."""
    project = _get_project(project_id, db)
    endpoint_name = resolve_endpoint(project)

    if not endpoint_name:
        return EndpointStatus(status="not_configured")

    try:
        result = check_endpoint_health(endpoint_name)
        return EndpointStatus(**result)
    except Exception as e:
        return EndpointStatus(
            status="error", endpoint=endpoint_name, error=str(e),
        )


@router.get(
    "/samples/{sample_id}/predict",
    response_model=list[PredictionOut],
)
def predict_single_sample(
    project_id: int,
    sample_id: int,
    db: Session = Depends(get_db),
):
    """Get model predictions for a single sample without saving them."""
    project = _get_project(project_id, db)
    endpoint_name = resolve_endpoint(project)
    if not endpoint_name:
        raise HTTPException(
            status_code=400,
            detail="No serving endpoint configured for this project.",
        )

    sample = (
        db.query(ProjectSample)
        .filter_by(id=sample_id, project_id=project_id)
        .first()
    )
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found.")

    image_bytes = read_image_bytes(sample.filepath)
    if not image_bytes:
        raise HTTPException(status_code=500, detail="Could not read image file.")

    try:
        predictions = predict_sample(
            endpoint_name=endpoint_name,
            image_bytes=image_bytes,
            task_type=project.task_type,
            class_list=list(project.class_list),
            endpoint_config=project.endpoint_config,
        )
    except Exception as e:
        log.error("Prediction failed for sample %d: %s", sample_id, e)
        raise HTTPException(
            status_code=502,
            detail=f"Model endpoint error: {e}",
        )

    return [PredictionOut(**p) for p in predictions]


@router.post("/pre-annotate", response_model=PreAnnotateProgress)
def pre_annotate_project(
    project_id: int,
    payload: PreAnnotateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Batch pre-annotate unlabeled samples using the configured model endpoint.

    This is a synchronous endpoint that processes samples sequentially.
    For large projects, consider running with a limited max_samples.
    """
    project = _get_project(project_id, db)
    endpoint_name = resolve_endpoint(project)
    if not endpoint_name:
        raise HTTPException(
            status_code=400,
            detail="No serving endpoint configured for this project.",
        )

    health = check_endpoint_health(endpoint_name)
    if health["status"] != "ready":
        raise HTTPException(
            status_code=503,
            detail=f"Endpoint '{endpoint_name}' is not ready: {health.get('error') or health.get('state', 'unknown')}",
        )

    query = (
        db.query(ProjectSample)
        .filter_by(project_id=project_id, status="unlabeled")
        .order_by(ProjectSample.id)
    )
    if payload.max_samples and payload.max_samples > 0:
        query = query.limit(payload.max_samples)

    samples = query.all()
    created_by = f"model:{endpoint_name}"

    endpoint_config = dict(project.endpoint_config or {})
    if payload.min_confidence is not None:
        endpoint_config["min_confidence"] = payload.min_confidence

    completed = 0
    failed = 0
    skipped = 0

    for sample in samples:
        image_bytes = read_image_bytes(sample.filepath)
        if not image_bytes:
            failed += 1
            continue

        try:
            predictions = predict_sample(
                endpoint_name=endpoint_name,
                image_bytes=image_bytes,
                task_type=project.task_type,
                class_list=list(project.class_list),
                endpoint_config=endpoint_config,
            )
        except Exception as e:
            log.warning("Pre-annotate failed for sample %d: %s", sample.id, e)
            failed += 1
            continue

        if not predictions:
            skipped += 1
            continue

        for pred in predictions:
            ann = Annotation(
                sample_id=sample.id,
                project_id=project_id,
                label=pred["label"],
                ann_type=pred["ann_type"],
                bbox_json=pred.get("bbox_json"),
                created_by=created_by,
            )
            db.add(ann)

        sample.status = "pre_labeled"
        completed += 1

        if completed % 50 == 0:
            db.flush()

    db.commit()

    return PreAnnotateProgress(
        completed=completed,
        failed=failed,
        skipped=skipped,
        total=len(samples),
    )


@router.get("/settings")
def get_project_inference_settings(
    project_id: int,
    db: Session = Depends(get_db),
):
    """Return inference settings for a project, including defaults."""
    project = _get_project(project_id, db)
    endpoint_name = resolve_endpoint(project)
    return {
        "serving_endpoint": project.serving_endpoint,
        "default_serving_endpoint": get_default_endpoint(),
        "resolved_endpoint": endpoint_name,
        "endpoint_config": project.endpoint_config,
        "pre_annotation_enabled": endpoint_name is not None,
    }
