"""
Bulk annotation import — POST /api/projects/{project_id}/import.

Reads a file from a UC Volume by reference, dispatches to a format
adapter, runs a two-pass validate-then-commit, returns counters.

Design: docs/plans/2026-04-28-api-import-design.md
Follow-up hardening: docs/plans/2026-04-29-api-import-followup.md
"""

import logging
import math
import os
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..deps import get_db, get_user_email
from ..import_adapters import get_adapter
from .. import image_meta, masks
from ..models import (
    LabelingProject, ProjectSample, Annotation, AnnotationHistory,
)
from ..schemas import (
    AnnotationCreate, ImportRequest, ImportResponse, ImportErrorItem,
)
from ..volumes import read_bytes, read_image_bytes, file_exists, is_volume_path

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["import"])

MAX_ITEMS = 500_000
MAX_ANNOTATIONS = 2_000_000
MAX_ERRORS_IN_RESPONSE = 100
MAX_FILE_BYTES = 200 * 1024 * 1024  # 200 MB


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


def _validate_volume_path(path: str, allow_local: bool) -> None:
    """Raise HTTPException 400 if ``path`` is unsafe.

    Default policy: must be a canonical ``/Volumes/...`` path with no
    ``..`` segments, no empty segments, and no backslashes.

    The test-only ``X-Test-Allow-Local-Path`` header sets
    ``allow_local=True`` so the pytest suite can exercise the endpoint
    against a local-filesystem stand-in. Production never sets it.
    """
    if not isinstance(path, str) or not path:
        _bad_request("volume_path is required")
    if "\\" in path:
        _bad_request("volume_path must not contain backslashes")
    parts = PurePosixPath(path).parts
    for p in parts[1:]:
        if p in ("", ".."):
            _bad_request("volume_path must not contain '..' or empty segments")
    if not is_volume_path(path):
        if not allow_local:
            _bad_request("volume_path must start with /Volumes/")


def _normalize_filename(raw: str) -> Optional[str]:
    """Return a safe basename, or ``None`` if the value is unsafe.

    Rejects values with path separators, ``..``, or empty strings.
    """
    if not isinstance(raw, str) or not raw:
        return None
    if "\\" in raw or "/" in raw:
        return None
    if raw in (".", ".."):
        return None
    name = PurePosixPath(raw).name
    return name or None


def _validate_annotation(
    ann: AnnotationCreate,
    project: LabelingProject,
) -> Optional[str]:
    """Return an error string or None if valid."""
    if ann.label not in project.class_list:
        return f"label '{ann.label}' not in project class_list"
    if ann.ann_type not in ("classification", "bbox", "mask"):
        return (
            "ann_type must be 'classification', 'bbox' or 'mask', "
            f"got '{ann.ann_type}'"
        )
    if project.task_type == "classification" and ann.ann_type in ("bbox", "mask"):
        return f"classification project cannot accept {ann.ann_type} annotations"
    if project.task_type == "detection" and ann.ann_type == "classification":
        return "detection project cannot accept classification-only annotations"
    if project.task_type == "segmentation" and ann.ann_type == "classification":
        return "segmentation project cannot accept classification-only annotations"
    if ann.ann_type == "mask" and not ann.mask_json:
        return "mask annotation missing mask_json"
    if ann.mask_json is not None:
        # Structure only: the size is checked against the actual image at
        # commit time, when the sample (and so its dimensions) is in hand.
        try:
            masks.validate_uncompressed(ann.mask_json)
        except masks.MaskValidationError as e:
            return f"invalid mask_json: {e}"
    if ann.ann_type == "bbox":
        bb = ann.bbox_json
        if not isinstance(bb, dict):
            return "bbox annotation missing bbox_json"
        coords: dict[str, float] = {}
        for k in ("x", "y", "w", "h"):
            v = bb.get(k)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                return f"bbox.{k} must be numeric"
            if not math.isfinite(v):
                return f"bbox.{k} must be finite (got {v!r})"
            coords[k] = float(v)
        x, y, w, h = coords["x"], coords["y"], coords["w"], coords["h"]
        if x < 0 or y < 0:
            return "bbox requires x>=0 and y>=0"
        if w <= 0 or h <= 0:
            return "bbox requires w>0 and h>0"
        # Allow a tiny epsilon for floating-point round-off from COCO
        # pixel-to-normalized conversion.
        if x + w > 1.0 + 1e-9 or y + h > 1.0 + 1e-9:
            return "bbox must fit within [0,1]: x+w<=1 and y+h<=1"
    return None


@router.post("/import", response_model=ImportResponse)
def import_annotations(
    project_id: int,
    payload: ImportRequest,
    request: Request,
    user_email: str = Depends(get_user_email),
    db: Session = Depends(get_db),
):
    # --- Path validation (Critical #2) ----------------------------------
    allow_local = request.headers.get("X-Test-Allow-Local-Path") == "1"
    _validate_volume_path(payload.volume_path, allow_local=allow_local)

    # --- Project ---
    project = db.query(LabelingProject).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")

    if payload.on_missing_sample == "create" and (
        getattr(project, "source_type", None) or "volume"
    ) == "table":
        _bad_request(
            "on_missing_sample=create is not supported for table-backed projects; "
            "samples must already exist from the source table scan"
        )

    # --- Adapter (Pydantic v2 Literal guarantees format is valid) -------
    adapter = get_adapter(payload.format)

    # --- Size gate before read (Critical #3) ----------------------------
    # For local-path test mode we stat the file first; for UC Volumes we
    # rely on the post-read length check (read_bytes returns the whole
    # buffer). The per-item / per-annotation soft caps are enforced
    # after parse.
    if allow_local and os.path.exists(payload.volume_path):
        sz = os.path.getsize(payload.volume_path)
        if sz > MAX_FILE_BYTES:
            _bad_request(
                f"file too large: {sz} bytes > {MAX_FILE_BYTES} limit"
            )

    log.info(
        "import_started project=%s user=%s format=%s volume=%s dry_run=%s",
        project_id, user_email, payload.format, payload.volume_path, payload.dry_run,
    )
    raw = read_bytes(payload.volume_path)
    if raw is None:
        _bad_request(f"cannot read volume_path '{payload.volume_path}'")
    if len(raw) > MAX_FILE_BYTES:
        _bad_request(
            f"file too large: {len(raw)} bytes > {MAX_FILE_BYTES} limit"
        )

    # --- Parse ---
    items, parse_errors = adapter(raw)
    errors: list[ImportErrorItem] = list(parse_errors)

    if len(items) > MAX_ITEMS:
        _bad_request(
            f"payload too large: {len(items)} items > {MAX_ITEMS} limit; "
            f"split into multiple imports"
        )
    total_anns = sum(len(it.annotations) for it in items)
    if total_anns > MAX_ANNOTATIONS:
        _bad_request(
            f"payload too large: {total_anns} annotations > {MAX_ANNOTATIONS} limit"
        )

    # --- Filename normalization + duplicate detection (I #4, #6) --------
    seen: dict[str, int] = {}
    for idx, item in enumerate(items):
        norm = _normalize_filename(item.filename)
        if norm is None:
            errors.append(ImportErrorItem(
                row=idx + 1, filename=item.filename,
                reason="filename must be a basename with no separators, '..', or empty",
            ))
            continue
        item.filename = norm  # safe local mutation
        if norm in seen:
            errors.append(ImportErrorItem(
                row=idx + 1, filename=norm,
                reason=f"duplicate filename (also at row {seen[norm]})",
            ))
        else:
            seen[norm] = idx + 1

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
                    row=idx + 1, filename=item.filename,
                    reason="filename not found in project_samples",
                ))
                continue
            elif payload.on_missing_sample == "skip":
                continue
            elif payload.on_missing_sample == "create":
                full_path = project.source_volume.rstrip("/") + "/" + item.filename
                if not file_exists(full_path):
                    errors.append(ImportErrorItem(
                        row=idx + 1, filename=item.filename,
                        reason=(
                            "on_missing_sample=create requires file to exist "
                            f"under source_volume; '{full_path}' not found"
                        ),
                    ))
                    continue
                missing_filenames.append(item.filename)

        for ann in item.annotations:
            reason = _validate_annotation(ann, project)
            if not reason and ann.mask_json is not None:
                reason = _mask_size_error(db, project, item.filename, ann)
            if reason:
                errors.append(ImportErrorItem(
                    row=idx + 1, filename=item.filename, reason=reason,
                ))

    if errors:
        log.warning(
            "import_validation_failed project=%s error_count=%d",
            project_id, len(errors),
        )
        return _validation_422(errors)

    resp = ImportResponse(dry_run=payload.dry_run)

    if payload.dry_run:
        # NOTE: dry-run counters are a conservative estimate. They do not
        # prefetch existing annotations per sample, so actual
        # ``annotations_replaced`` / ``samples_skipped`` values may differ
        # when ``on_existing_annotations`` is ``replace`` or ``skip``. The
        # README documents this limitation.
        resp.samples_created = len(set(missing_filenames))
        for item in items:
            if item.filename not in name_to_id:
                if payload.on_missing_sample == "skip":
                    resp.samples_skipped += 1
                    continue
                if payload.on_missing_sample != "create":
                    continue
            resp.samples_touched += 1
            resp.annotations_created += len(item.annotations)
        log.info("import_dry_run_ok project=%s counters=%s",
                 project_id, resp.model_dump())
        return resp

    # --- Pass 2: commit ---
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
                        old_mask_json=old.mask_json,
                        new_mask_json=None,
                        changed_by=user_email,
                        changed_at=now,
                    ))
                    resp.annotations_replaced += 1
                db.query(Annotation).filter_by(
                    sample_id=sample_id, project_id=project_id,
                ).delete()

            for ann in item.annotations:
                mask_json = None
                bbox_json = ann.bbox_json
                if ann.mask_json is not None:
                    mask_json, bbox_json = _import_mask(db, sample, ann)
                db.add(AnnotationHistory(
                    sample_id=sample_id,
                    project_id=project_id,
                    action="create",
                    old_label=None,
                    new_label=ann.label,
                    old_ann_type=None,
                    new_ann_type=ann.ann_type,
                    old_bbox_json=None,
                    new_bbox_json=bbox_json,
                    old_mask_json=None,
                    new_mask_json=mask_json,
                    changed_by=user_email,
                    changed_at=now,
                ))
                db.add(Annotation(
                    sample_id=sample_id,
                    project_id=project_id,
                    label=ann.label,
                    ann_type=ann.ann_type,
                    bbox_json=bbox_json,
                    mask_json=mask_json,
                    is_draft=False,
                    created_by=user_email,
                    created_at=now,
                ))
                resp.annotations_created += 1

            # Status transitions (Important #7).
            if item.annotations:
                sample.status = "labeled"
                sample.locked_by = None
                sample.locked_at = None
                resp.samples_touched += 1
            elif payload.on_existing_annotations == "replace":
                # Replace-with-zero-annotations: sample now has no labels.
                sample.status = "unlabeled"
                sample.locked_by = None
                sample.locked_at = None
                resp.samples_touched += 1

        db.commit()
    except Exception:
        db.rollback()
        log.exception("import_commit_failed project=%s", project_id)
        raise HTTPException(status_code=500, detail="Import commit failed; see server log.")

    log.info("import_completed project=%s counters=%s",
             project_id, resp.model_dump())
    return resp


def _import_mask(db, sample, ann):
    """Validate an imported mask against the real image and compress it.

    Returns ``(stored_mask, bbox_json)``. The bbox is derived from the mask so
    a re-imported export cannot drift from the geometry it describes.
    """
    dims = image_meta.resolve_dimensions(db, sample)
    if not dims:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot import a mask for '{sample.filename}': its image "
                "dimensions could not be read."
            ),
        )
    img_w, img_h = dims
    try:
        height, width, counts = masks.validate_uncompressed(ann.mask_json, img_h, img_w)
    except masks.MaskValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid mask for '{sample.filename}': {e}",
        )
    return masks.to_storage(counts, height, width), masks.normalized_bbox(
        counts, height, width
    )


def _mask_size_error(db, project, filename: str, ann) -> Optional[str]:
    """Check an imported mask against the real image, as a row-level reason.

    Done in the validation pass so a mask drawn against the wrong image size
    is reported alongside every other bad row, instead of aborting a partly
    applied commit.
    """
    sample = db.query(ProjectSample).filter_by(
        project_id=project.id, filename=filename,
    ).first()
    if sample is not None:
        dims = image_meta.resolve_dimensions(db, sample)
    else:
        # on_missing_sample=create: the file was already confirmed to exist.
        full_path = project.source_volume.rstrip("/") + "/" + filename
        data = read_image_bytes(full_path)
        dims = image_meta.dimensions_from_bytes(data) if data else None
    if not dims:
        return "image dimensions could not be read, so a mask cannot be imported"
    img_w, img_h = dims
    try:
        masks.validate_uncompressed(ann.mask_json, img_h, img_w)
    except masks.MaskValidationError as e:
        return f"invalid mask_json: {e}"
    return None
