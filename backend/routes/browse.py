"""
Volume and catalog browsing routes — used by the Create Project form.
"""

import io
import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..volumes import IMAGE_EXTENSIONS, is_volume_path, read_image_bytes, _get_workspace_client

router = APIRouter(prefix="/api", tags=["browse"])


@router.get("/catalogs")
def list_catalogs():
    try:
        w = _get_workspace_client()
        names = [c.name for c in w.catalogs.list()]
        return sorted(names)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/schemas")
def list_schemas(catalog: str = Query(...)):
    try:
        w = _get_workspace_client()
        return sorted(s.name for s in w.schemas.list(catalog_name=catalog))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/volumes")
def list_volumes(catalog: str = Query(...), schema: str = Query(...)):
    try:
        w = _get_workspace_client()
        return sorted(v.name for v in w.volumes.list(catalog_name=catalog, schema_name=schema))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/browse")
def browse_directory(
    path: str = Query(...),
    page: int = Query(0, ge=0),
    page_size: int = Query(50, ge=1, le=500),
):
    """Browse a UC Volume or local directory for images.

    Local filesystem browsing is restricted to /Volumes and /tmp to
    prevent arbitrary path traversal.  Files are paginated; folders are
    always returned in full.
    """
    ALLOWED_LOCAL_PREFIXES = ("/Volumes/", "/tmp/")

    if is_volume_path(path):
        try:
            w = _get_workspace_client()
            folders, files = [], []
            for entry in w.files.list_directory_contents(path.rstrip("/") + "/"):
                if entry.is_directory:
                    folders.append({"name": entry.name, "image_count": 0})
                else:
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in IMAGE_EXTENSIONS:
                        files.append({"name": entry.name, "path": path.rstrip("/") + "/" + entry.name})
                    elif entry.name.endswith(".json"):
                        files.append({"name": entry.name, "path": path.rstrip("/") + "/" + entry.name})
            files_sorted = sorted(files, key=lambda x: x["name"])
            total_files = len(files_sorted)
            start = page * page_size
            page_files = files_sorted[start : start + page_size]
            return {
                "path": path,
                "folders": sorted(folders, key=lambda x: x["name"]),
                "files": page_files,
                "total_files": total_files,
                "page": page,
                "page_size": page_size,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        resolved = os.path.realpath(path)
        if not any(resolved.startswith(prefix) for prefix in ALLOWED_LOCAL_PREFIXES):
            raise HTTPException(status_code=403, detail="Local browsing restricted to allowed directories.")
        if not os.path.isdir(resolved):
            raise HTTPException(status_code=404, detail=f"Directory not found: {path}")
        folders, files = [], []
        for entry in sorted(os.listdir(resolved)):
            full = os.path.join(resolved, entry)
            if os.path.isdir(full):
                folders.append({"name": entry, "image_count": 0})
            elif os.path.splitext(entry)[1].lower() in IMAGE_EXTENSIONS:
                files.append({"name": entry, "path": full})
        total_files = len(files)
        start = page * page_size
        page_files = files[start : start + page_size]
        return {
            "path": path,
            "folders": folders,
            "files": page_files,
            "total_files": total_files,
            "page": page,
            "page_size": page_size,
        }


@router.get("/browse/thumbnail")
def browse_thumbnail(
    path: str = Query(...),
    size: int = Query(120, ge=32, le=400),
):
    """Serve a resized thumbnail for a file by its volume/local path.

    Same path-safety rules as browse_directory apply for local files.
    """
    ALLOWED_LOCAL_PREFIXES = ("/Volumes/", "/tmp/")

    if not is_volume_path(path):
        resolved = os.path.realpath(path)
        if not any(resolved.startswith(prefix) for prefix in ALLOWED_LOCAL_PREFIXES):
            raise HTTPException(status_code=403, detail="Access denied.")

    data = read_image_bytes(path)
    if data is None:
        raise HTTPException(status_code=404, detail="Image not found.")

    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((size, size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=3600"},
    )
