"""List available exports for a project (scans the export volume)."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from databricks.sdk.errors import NotFound
from sqlalchemy.orm import Session

from ..deps import get_db
from ..dataset_exports import (
    CLASSIFICATION_REFERENCE_FORMAT,
    REFERENCE_FORMATS,
    default_export_volume,
    reference_loading_code,
)
from ..job_utils import get_project_or_404
from ..models import LabelingProject
from ..schemas import ExportInfo
from ..volumes import _get_workspace_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["exports"])


@router.get("/exports", response_model=list[ExportInfo])
def list_exports(project_id: int, db: Session = Depends(get_db)):
    """List exports available for this project by scanning the export volume."""
    project = get_project_or_404(project_id, db, LabelingProject)
    export_volume = default_export_volume(project)
    if not export_volume.startswith("/Volumes/"):
        return []

    w = _get_workspace_client()
    results = []

    try:
        entries = list(w.files.list_directory_contents(export_volume + "/"))
    except NotFound:
        return []
    except Exception as e:
        log.warning("Could not list export volume %s: %s", export_volume, e)
        raise HTTPException(502, "Could not load dataset history. Check export Volume access and retry.") from e

    directories = [f"{export_volume}/{entry.name}" for entry in entries if entry.is_directory]
    if f"{export_volume}/exports" in directories:
        try:
            legacy_entries = w.files.list_directory_contents(export_volume + "/exports/")
            directories.extend(f"{export_volume}/exports/{entry.name}" for entry in legacy_entries if entry.is_directory)
        except Exception:
            log.warning("Could not list legacy exports", exc_info=True)

    for export_dir in directories:
        meta_path = f"{export_dir}/metadata.json"
        try:
            resp = w.files.download(meta_path)
            with resp.contents as contents:
                content = contents.read()
            meta = json.loads(content)
        except Exception:
            continue

        # Only include exports belonging to this project
        if not isinstance(meta, dict) or meta.get("project_id") != project_id:
            continue
        if meta.get("format") in REFERENCE_FORMATS and meta.get("status") != "ready":
            continue

        loading_code = (
            reference_loading_code(export_dir, meta.get("task_type"))
            if meta.get("format") in REFERENCE_FORMATS else None
        )

        results.append(ExportInfo(
            export_path=export_dir,
            project_name=meta.get("project_name", ""),
            version=meta.get("version", 1),
            task_type=meta.get("task_type", ""),
            class_list=meta.get("class_list", []),
            image_count=meta.get("image_count", 0),
            annotation_count=meta.get("annotation_count", 0),
            exported_at=meta.get("exported_at", ""),
            exported_by=meta.get("exported_by", ""),
            format=meta.get("format", ""),
            huggingface_code=loading_code if meta.get("format") == CLASSIFICATION_REFERENCE_FORMAT else None,
            loading_code=loading_code,
        ))

    # Sort newest first
    results.sort(key=lambda x: x.exported_at, reverse=True)
    return results
