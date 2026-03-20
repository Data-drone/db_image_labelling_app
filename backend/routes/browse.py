"""
Volume and catalog browsing routes — used by the Create Project form.
"""

import os

from fastapi import APIRouter, HTTPException, Query

from ..volumes import IMAGE_EXTENSIONS, is_volume_path, _get_workspace_client

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
def browse_directory(path: str = Query(...)):
    """Browse a UC Volume or local directory for images.

    Local filesystem browsing is restricted to /Volumes and /tmp to
    prevent arbitrary path traversal.
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
            return {
                "path": path,
                "folders": sorted(folders, key=lambda x: x["name"]),
                "files": sorted(files, key=lambda x: x["name"]),
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
        return {"path": path, "folders": folders, "files": files}
