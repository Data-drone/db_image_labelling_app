"""
Helpers for reading images and scanning volumes/directories.

Centralises volume I/O so the rest of the app never touches
the Databricks SDK or os.listdir for image access directly.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Iterator, Optional

from sqlalchemy.orm import Session

from .models import ProjectSample

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif"}

# Depth 0 = selected directory only (historical default). Recursion is extra
# folder levels under that path. A hard cap keeps UC listing bounded.
DEFAULT_SCAN_MAX_DEPTH = 3
MAX_SCAN_DEPTH = 8
MAX_SCAN_IMAGES = 50_000


@dataclass(frozen=True)
class ImageRef:
    """One discoverable image under a scan root."""

    filepath: str
    filename: str  # path relative to the scan root, posix separators


@dataclass
class ImageDiscovery:
    images: list[ImageRef] = field(default_factory=list)
    error: Optional[str] = None
    truncated: bool = False


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


def read_bytes(filepath: str) -> Optional[bytes]:
    """Read arbitrary file bytes from a UC Volume or local filesystem.

    Same I/O path as read_image_bytes, but named for non-image payloads
    (e.g. COCO JSON, JSONL label files). Returns None if unreadable.
    """
    return read_image_bytes(filepath)


def file_exists(filepath: str) -> bool:
    """Cheap existence check for a UC Volume or local path."""
    if is_volume_path(filepath):
        try:
            w = _get_workspace_client()
            w.files.get_metadata(filepath)
            return True
        except Exception:
            return False
    return os.path.exists(filepath)


def clamp_scan_max_depth(value: Optional[int]) -> int:
    """Bound a requested recursion depth to ``[0, MAX_SCAN_DEPTH]``."""
    if value is None:
        return DEFAULT_SCAN_MAX_DEPTH
    try:
        depth = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SCAN_MAX_DEPTH
    return max(0, min(depth, MAX_SCAN_DEPTH))


def effective_scan_depth(include_subfolders: bool, max_depth: Optional[int] = None) -> int:
    """Return walk depth for a scan: 0 means the selected directory only."""
    if not include_subfolders:
        return 0
    return clamp_scan_max_depth(max_depth)


def _is_image_name(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS


def _join_rel(prefix: str, name: str) -> str:
    return name if not prefix else f"{prefix}/{name}"


def iter_images(
    root: str,
    *,
    max_depth: int = 0,
    limit: int = MAX_SCAN_IMAGES,
) -> Iterator[ImageRef]:
    """Yield images under ``root``, optionally descending into subfolders.

    ``max_depth`` is extra folder levels below ``root`` (0 = this folder
    only). Relative ``filename`` values use posix separators so identical
    basenames in different subfolders stay distinct.
    """
    discovery = discover_images(root, max_depth=max_depth, limit=limit)
    yield from discovery.images


def discover_images(
    root: str,
    *,
    max_depth: int = 0,
    limit: int = MAX_SCAN_IMAGES,
) -> ImageDiscovery:
    """Collect images under ``root`` with the same rules as ``iter_images``.

    Listing failures are recorded on ``error`` and do not discard images
    already found. ``truncated`` is set when ``limit`` is hit.
    """
    root = (root or "").rstrip("/")
    result = ImageDiscovery()
    if not root:
        return result
    depth = max(0, int(max_depth))
    cap = max(1, int(limit))

    if is_volume_path(root):
        try:
            client = _get_workspace_client()
        except Exception as e:
            result.error = str(e)
            log.warning("Volume scan of %s failed before listing: %s", root, e)
            return result
        _walk_volume(client, root, "", depth, cap, result)
    elif os.path.isdir(root):
        _walk_local(root, "", depth, cap, result)
    return result


def _walk_volume(client, current: str, rel_prefix: str, remaining: int, limit: int, result: ImageDiscovery) -> None:
    if result.truncated:
        return
    try:
        entries = list(client.files.list_directory_contents(current + "/"))
    except Exception as e:
        result.error = str(e)
        log.warning("Volume scan of %s stopped after %d samples: %s",
                    current, len(result.images), e)
        return

    subdirs = []
    for entry in entries:
        name = getattr(entry, "name", "") or ""
        if not name or name in (".", ".."):
            continue
        if getattr(entry, "is_directory", False):
            subdirs.append(name)
            continue
        if not _is_image_name(name):
            continue
        result.images.append(ImageRef(
            filepath=f"{current}/{name}",
            filename=_join_rel(rel_prefix, name),
        ))
        if len(result.images) >= limit:
            result.truncated = True
            return

    if remaining <= 0:
        return
    for name in sorted(subdirs):
        _walk_volume(
            client,
            f"{current}/{name}",
            _join_rel(rel_prefix, name),
            remaining - 1,
            limit,
            result,
        )
        if result.truncated:
            return


def _walk_local(current: str, rel_prefix: str, remaining: int, limit: int, result: ImageDiscovery) -> None:
    if result.truncated:
        return
    try:
        entries = list(os.scandir(current))
    except OSError as e:
        result.error = str(e)
        log.warning("Volume scan of %s stopped after %d samples: %s",
                    current, len(result.images), e)
        return

    subdirs = []
    files = []
    for entry in entries:
        name = entry.name
        if not name or name in (".", ".."):
            continue
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)
        except OSError:
            continue
        if is_dir:
            subdirs.append(name)
        elif is_file:
            files.append(name)

    for name in sorted(files):
        if not _is_image_name(name):
            continue
        result.images.append(ImageRef(
            filepath=os.path.join(current, name),
            filename=_join_rel(rel_prefix, name),
        ))
        if len(result.images) >= limit:
            result.truncated = True
            return

    if remaining <= 0:
        return
    for name in sorted(subdirs):
        _walk_local(
            os.path.join(current, name),
            _join_rel(rel_prefix, name),
            remaining - 1,
            limit,
            result,
        )
        if result.truncated:
            return


def scan_volume_for_samples(
    db: Session,
    project_id: int,
    volume_path: str,
    include_subfolders: bool = False,
    max_depth: Optional[int] = None,
) -> int:
    """Scan a volume or local directory for images and create ProjectSample rows.

    Returns the number of samples added. Existing filepaths are skipped so a
    rescan does not duplicate rows. Nested files use a relative ``filename``.
    """
    volume_path = (volume_path or "").rstrip("/")
    count = 0
    existing = {
        fp for (fp,) in db.query(ProjectSample.filepath).filter_by(project_id=project_id)
        if fp
    }
    discovery = discover_images(
        volume_path,
        max_depth=effective_scan_depth(include_subfolders, max_depth),
    )
    for image in discovery.images:
        if image.filepath in existing:
            continue
        db.add(ProjectSample(
            project_id=project_id,
            filepath=image.filepath,
            filename=image.filename,
        ))
        existing.add(image.filepath)
        count += 1
    if discovery.truncated:
        log.warning(
            "Volume scan of %s hit the %d-image cap; later files were skipped",
            volume_path, MAX_SCAN_IMAGES,
        )
    return count
