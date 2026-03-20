"""
Helpers for reading images and scanning volumes/directories.

Centralises volume I/O so the rest of the app never touches
the Databricks SDK or os.listdir for image access directly.
"""

import io
import os
from typing import Optional

from sqlalchemy.orm import Session

from .models import ProjectSample

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif"}


def is_volume_path(path: str) -> bool:
    return path.startswith("/Volumes/")


def _get_workspace_client():
    if not hasattr(_get_workspace_client, "_client"):
        from databricks.sdk import WorkspaceClient
        _get_workspace_client._client = WorkspaceClient()
    return _get_workspace_client._client


def read_image_bytes(filepath: str) -> Optional[bytes]:
    """Read image bytes from a UC Volume path or local filesystem.

    Returns None if the file cannot be read.
    """
    if is_volume_path(filepath):
        try:
            w = _get_workspace_client()
            resp = w.files.download(filepath)
            return resp.contents.read()
        except Exception:
            return None
    else:
        if not os.path.exists(filepath):
            return None
        with open(filepath, "rb") as f:
            return f.read()


def scan_volume_for_samples(
    db: Session,
    project_id: int,
    volume_path: str,
) -> int:
    """Scan a volume or local directory for images and create ProjectSample rows.

    Returns the number of samples added.
    """
    volume_path = volume_path.rstrip("/")
    count = 0

    if is_volume_path(volume_path):
        try:
            w = _get_workspace_client()
            for entry in w.files.list_directory_contents(volume_path + "/"):
                if not entry.is_directory:
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in IMAGE_EXTENSIONS:
                        db.add(ProjectSample(
                            project_id=project_id,
                            filepath=volume_path + "/" + entry.name,
                            filename=entry.name,
                        ))
                        count += 1
        except Exception:
            return count
    elif os.path.isdir(volume_path):
        for fname in sorted(os.listdir(volume_path)):
            ext = os.path.splitext(fname)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                db.add(ProjectSample(
                    project_id=project_id,
                    filepath=os.path.join(volume_path, fname),
                    filename=fname,
                ))
                count += 1

    return count
