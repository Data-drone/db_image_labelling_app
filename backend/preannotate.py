"""
Shared pre-annotation batch runner (dashboard, on-import, future async jobs).

Clears prior model drafts per sample before re-inferring (idempotent re-run).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def clear_model_drafts_for_sample(db: Session, project_id: int, sample_id: int) -> None:
    from .models import Annotation

    (
        db.query(Annotation)
        .filter(
            Annotation.project_id == project_id,
            Annotation.sample_id == sample_id,
            Annotation.is_draft.is_(True),
            Annotation.created_by.like("model:%"),
        )
        .delete(synchronize_session=False)
    )


def refresh_sample_status_after_annotation_change(db: Session, project_id: int, sample_id: int) -> None:
    from .models import Annotation, ProjectSample

    s = (
        db.query(ProjectSample)
        .filter_by(id=sample_id, project_id=project_id)
        .first()
    )
    if not s or s.status == "skipped":
        return
    total = (
        db.query(Annotation)
        .filter_by(sample_id=sample_id, project_id=project_id)
        .count()
    )
    if total == 0:
        s.status = "unlabeled"
        return
    drafts = (
        db.query(Annotation)
        .filter_by(sample_id=sample_id, project_id=project_id)
        .filter(Annotation.is_draft.is_(True))
        .count()
    )
    s.status = "pre_labeled" if drafts else "labeled"


def run_preannotate_for_samples(
    db: Session,
    project: Any,
    samples: list,
    *,
    min_confidence: Optional[float] = None,
    text_prompt: Optional[str] = None,
) -> dict[str, int]:
    """Run model inference and insert draft annotations. Caller must commit.

    Returns counters: completed, failed, skipped, total.
    """
    from .inference import predict_sample, resolve_endpoint
    from .models import Annotation
    from .volumes import read_image_bytes

    endpoint_name = resolve_endpoint(project)
    if not endpoint_name:
        return {"completed": 0, "failed": 0, "skipped": 0, "total": len(samples)}

    endpoint_config = dict(project.endpoint_config or {})
    if min_confidence is not None:
        endpoint_config["min_confidence"] = min_confidence
    if text_prompt:
        endpoint_config["sam_text_prompt"] = text_prompt

    created_by = f"model:{endpoint_name}"
    completed = failed = skipped = 0

    for sample in samples:
        image_bytes = read_image_bytes(sample.filepath)
        if not image_bytes:
            failed += 1
            refresh_sample_status_after_annotation_change(db, project.id, sample.id)
            continue

        clear_model_drafts_for_sample(db, project.id, sample.id)

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
            refresh_sample_status_after_annotation_change(db, project.id, sample.id)
            continue

        if not predictions:
            skipped += 1
            refresh_sample_status_after_annotation_change(db, project.id, sample.id)
            continue

        for pred in predictions:
            db.add(
                Annotation(
                    sample_id=sample.id,
                    project_id=project.id,
                    label=pred["label"],
                    ann_type=pred["ann_type"],
                    bbox_json=pred.get("bbox_json"),
                    is_draft=True,
                    created_by=created_by,
                )
            )
        sample.status = "pre_labeled"
        completed += 1
        if completed % 50 == 0:
            db.flush()

    return {
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "total": len(samples),
    }
