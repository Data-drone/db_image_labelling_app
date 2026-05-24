"""List available exports for a project (scans the export volume)."""

import json
import logging
import os

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..deps import get_db
from ..job_utils import get_project_or_404
from ..models import LabelingProject
from ..schemas import ExportInfo
from ..volumes import _get_workspace_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["exports"])


@router.get("/exports", response_model=list[ExportInfo])
def list_exports(project_id: int, db: Session = Depends(get_db)):
    """List exports available for this project by scanning the export volume."""
    get_project_or_404(project_id, db, LabelingProject)

    export_volume = os.environ.get("EXPORT_VOLUME_PATH", "").strip().rstrip("/")
    if not export_volume:
        return []

    w = _get_workspace_client()
    results = []

    try:
        entries = list(w.files.list_directory_contents(export_volume + "/"))
    except Exception as e:
        log.warning("Could not list export volume %s: %s", export_volume, e)
        return []

    for entry in entries:
        if not entry.is_directory:
            continue
        meta_path = f"{export_volume}/{entry.name}/metadata.json"
        try:
            resp = w.files.download(meta_path)
            content = resp.contents.read()
            meta = json.loads(content)
        except Exception:
            continue

        # Only include exports belonging to this project
        if meta.get("project_id") != project_id:
            continue

        results.append(ExportInfo(
            export_path=f"{export_volume}/{entry.name}",
            project_name=meta.get("project_name", ""),
            version=meta.get("version", 1),
            task_type=meta.get("task_type", ""),
            class_list=meta.get("class_list", []),
            image_count=meta.get("image_count", 0),
            annotation_count=meta.get("annotation_count", 0),
            exported_at=meta.get("exported_at", ""),
            exported_by=meta.get("exported_by", ""),
            format=meta.get("format", ""),
        ))

    # Sort newest first
    results.sort(key=lambda x: x.exported_at, reverse=True)
    return results
