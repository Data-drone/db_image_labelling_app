"""
Bulk annotation import — POST /api/projects/{project_id}/import.

Reads a file from a UC Volume by reference, dispatches to a format
adapter, runs a two-pass validate-then-commit, returns counters.

Design: docs/plans/2026-04-28-api-import-design.md
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..deps import get_db, get_user_email
from ..import_adapters import get_adapter, NormalizedImportItem
from ..models import (
    LabelingProject, ProjectSample, Annotation, AnnotationHistory,
)
from ..schemas import (
    AnnotationCreate, ImportRequest, ImportResponse, ImportErrorItem,
)
from ..volumes import read_bytes, file_exists, is_volume_path

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["import"])

MAX_ITEMS = 500_000
MAX_ERRORS_IN_RESPONSE = 100

VALID_ON_MISSING = {"error", "skip", "create"}
VALID_ON_EXISTING = {"replace", "append", "skip"}


def _bad_request(reason: str):
    raise HTTPException(status_code=400, detail=reason)


def _validation_422(errors: list[ImportErrorItem]):
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Import validation failed",
            "errors": [e.model_dump() for e in errors[:MAX_ERRORS_IN_RESPONSE]],
            "error_count": len(errors),
        },
    )


def _validate_annotation(
    ann: AnnotationCreate,
    project: LabelingProject,
) -> Optional[str]:
    """Return an error string or None if valid."""
    if ann.label not in project.class_list:
        return f"label '{ann.label}' not in project class_list"
    if ann.ann_type not in ("classification", "bbox"):
        return f"ann_type must be 'classification' or 'bbox', got '{ann.ann_type}'"
    if project.task_type == "classification" and ann.ann_type == "bbox":
        return "classification project cannot accept bbox annotations"
    if project.task_type == "detection" and ann.ann_type == "classification":
        return "detection project cannot accept classification-only annotations"
    if ann.ann_type == "bbox":
        bb = ann.bbox_json
        if not isinstance(bb, dict):
            return "bbox annotation missing bbox_json"
        for k in ("x", "y", "w", "h"):
            v = bb.get(k)
            if not isinstance(v, (int, float)):
                return f"bbox.{k} must be numeric"
            if not 0.0 <= float(v) <= 1.0:
                return f"bbox.{k}={v} out of range [0,1]"
    return None


@router.post("/import")
def import_annotations(
    project_id: int,
    payload: ImportRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    # --- Policy flags ---
    if payload.on_missing_sample not in VALID_ON_MISSING:
        _bad_request(f"on_missing_sample must be one of {sorted(VALID_ON_MISSING)}")
    if payload.on_existing_annotations not in VALID_ON_EXISTING:
        _bad_request(f"on_existing_annotations must be one of {sorted(VALID_ON_EXISTING)}")

    # --- Project ---
    project = db.query(LabelingProject).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")

    # --- Adapter ---
    try:
        adapter = get_adapter(payload.format)
    except ValueError as e:
        _bad_request(str(e))

    # --- Read source file ---
    log.info(
        "import_started project=%s format=%s volume=%s dry_run=%s",
        project_id, payload.format, payload.volume_path, payload.dry_run,
    )
    raw = read_bytes(payload.volume_path)
    if raw is None:
        _bad_request(f"cannot read volume_path '{payload.volume_path}'")

    # --- Parse ---
    items, parse_errors = adapter(raw)
    errors: list[ImportErrorItem] = list(parse_errors)

    if len(items) > MAX_ITEMS:
        _bad_request(
            f"payload too large ({len(items)} items > {MAX_ITEMS} limit); "
            f"split into multiple imports or wait for the async import endpoint"
        )

    # --- Filename -> sample_id map ---
    rows = db.query(ProjectSample.id, ProjectSample.filename).filter(
        ProjectSample.project_id == project_id,
    ).all()
    name_to_id: dict[str, int] = {fname: sid for sid, fname in rows}

    # --- Pass 1: validate everything ---
    missing_filenames: list[str] = []  # for on_missing_sample == "create"

    for idx, item in enumerate(items):
        has_sample = item.filename in name_to_id
        if not has_sample:
            if payload.on_missing_sample == "error":
                errors.append(ImportErrorItem(
                    row=idx, filename=item.filename,
                    reason="filename not found in project_samples",
                ))
                continue
            elif payload.on_missing_sample == "skip":
                # Skipped items don't contribute to counts; note it as a warning.
                continue
            elif payload.on_missing_sample == "create":
                # Must exist under the project's source_volume.
                full_path = project.source_volume.rstrip("/") + "/" + item.filename
                if not file_exists(full_path):
                    errors.append(ImportErrorItem(
                        row=idx, filename=item.filename,
                        reason=(
                            "on_missing_sample=create requires file to exist "
                            f"under source_volume; '{full_path}' not found"
                        ),
                    ))
                    continue
                missing_filenames.append(item.filename)

        for ann in item.annotations:
            reason = _validate_annotation(ann, project)
            if reason:
                errors.append(ImportErrorItem(
                    row=idx, filename=item.filename, reason=reason,
                ))

    if errors:
        log.warning(
            "import_validation_failed project=%s error_count=%d",
            project_id, len(errors),
        )
        return _validation_422(errors)

    # Build a response skeleton for dry_run / commit paths.
    resp = ImportResponse(dry_run=payload.dry_run)

    # Count what WOULD happen (also used as actual counts in pass 2).
    # For dry_run we short-circuit here.
    items_to_process = [
        item for item in items
        if item.filename in name_to_id or item.filename in set(missing_filenames)
        or payload.on_missing_sample == "skip"
    ]

    if payload.dry_run:
        resp.samples_created = len(set(missing_filenames))
        for item in items:
            if item.filename not in name_to_id:
                if payload.on_missing_sample == "skip":
                    resp.samples_skipped += 1
                    continue
                if payload.on_missing_sample != "create":
                    # Shouldn't happen (caught in pass 1), defensive.
                    continue
            resp.samples_touched += 1
            resp.annotations_created += len(item.annotations)
        log.info("import_dry_run_ok project=%s counters=%s", project_id, resp.model_dump())
        return resp

    # --- Pass 2: commit ---
    user_email = get_user_email(request)
    now = datetime.now(timezone.utc)

    try:
        # Insert created samples first; capture new IDs.
        for fname in set(missing_filenames):
            full_path = project.source_volume.rstrip("/") + "/" + fname
            new_sample = ProjectSample(
                project_id=project_id,
                filepath=full_path,
                filename=fname,
                status="unlabeled",
            )
            db.add(new_sample)
            db.flush()
            name_to_id[fname] = new_sample.id
            resp.samples_created += 1

        for item in items:
            if item.filename not in name_to_id:
                # Must be on_missing_sample=skip (already validated).
                resp.samples_skipped += 1
                continue

            sample_id = name_to_id[item.filename]
            sample = db.query(ProjectSample).filter_by(id=sample_id).one()

            existing = db.query(Annotation).filter_by(
                sample_id=sample_id, project_id=project_id,
            ).all()

            if existing and payload.on_existing_annotations == "skip":
                resp.samples_skipped += 1
                continue

            if existing and payload.on_existing_annotations == "replace":
                for old in existing:
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
                        changed_at=now,
                    ))
                    resp.annotations_replaced += 1
                db.query(Annotation).filter_by(
                    sample_id=sample_id, project_id=project_id,
                ).delete()

            for ann in item.annotations:
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
                    changed_at=now,
                ))
                db.add(Annotation(
                    sample_id=sample_id,
                    project_id=project_id,
                    label=ann.label,
                    ann_type=ann.ann_type,
                    bbox_json=ann.bbox_json,
                    created_by=user_email,
                    created_at=now,
                ))
                resp.annotations_created += 1

            if item.annotations:
                sample.status = "labeled"
                sample.locked_by = None
                sample.locked_at = None
                resp.samples_touched += 1

        db.commit()
    except Exception:
        db.rollback()
        log.exception("import_commit_failed project=%s", project_id)
        raise HTTPException(status_code=500, detail="Import commit failed; see server log.")

    log.info("import_completed project=%s counters=%s", project_id, resp.model_dump())
    return resp
