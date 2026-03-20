"""
Dataset export route.
"""

import io
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..deps import get_db, get_user_email
from ..models import LabelingProject, ProjectSample, Annotation
from ..volumes import is_volume_path, read_image_bytes, _get_workspace_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["export"])


@router.post("/export")
def export_project(
    project_id: int,
    body: dict,
    request: Request,
    db: Session = Depends(get_db),
):
    """Export labeled dataset to a UC Volume in COCO or CSV format."""
    from PIL import Image as PILImage

    export_path = (body.get("export_volume") or "").strip().rstrip("/")
    log.info("Export requested: project=%s, export_path=%r", project_id, export_path)
    if not export_path:
        raise HTTPException(status_code=400, detail="export_volume is required.")

    if not is_volume_path(export_path):
        raise HTTPException(
            status_code=400,
            detail="export_volume must be a UC Volume path (/Volumes/catalog/schema/volume/...).",
        )

    parts = export_path.strip("/").split("/")
    if len(parts) < 4:
        raise HTTPException(
            status_code=400,
            detail="export_volume must be at least /Volumes/catalog/schema/volume.",
        )

    w = _get_workspace_client()
    volume_root = "/" + "/".join(parts[:4])
    log.info("Checking volume root: %s", volume_root)
    try:
        next(iter(w.files.list_directory_contents(volume_root + "/")), None)
        log.info("Volume root OK")
    except Exception as vol_err:
        catalog_name, schema_name, volume_name = parts[1], parts[2], parts[3]
        log.error("Volume check failed: %s", vol_err)
        raise HTTPException(
            status_code=400,
            detail=f"Volume {catalog_name}.{schema_name}.{volume_name} does not exist. "
                   f"Please create it first: CREATE VOLUME {catalog_name}.{schema_name}.{volume_name}",
        )

    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")

    samples = (
        db.query(ProjectSample)
        .filter_by(project_id=project_id, status="labeled")
        .all()
    )
    log.info("Found %d labeled samples for project %d", len(samples), project_id)
    if not samples:
        raise HTTPException(status_code=400, detail="No labeled samples to export.")

    annotations = db.query(Annotation).filter_by(project_id=project_id).all()

    ann_by_sample = {}
    for a in annotations:
        ann_by_sample.setdefault(a.sample_id, []).append(a)

    safe_name = p.name.replace(" ", "_").replace("/", "_")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    export_dir = f"{export_path}/{safe_name}_v{p.version}_{ts}"

    is_detection = p.task_type == "detection"
    image_count = 0
    annotation_count = 0

    coco = {
        "info": {
            "description": f"CV Explorer export: {p.name} v{p.version}",
            "version": "1.0",
            "year": datetime.now().year,
        },
        "images": [],
        "annotations": [],
        "categories": [{"id": i, "name": c} for i, c in enumerate(p.class_list)],
    }
    class_to_id = {c: i for i, c in enumerate(p.class_list)}
    csv_rows = []

    first_error = None
    for sample in samples:
        try:
            img_data = read_image_bytes(sample.filepath)
            if img_data is None:
                log.warning("Skipping missing image: %s", sample.filepath)
                continue

            img = PILImage.open(io.BytesIO(img_data))
            img_w, img_h = img.size

            dest_path = f"{export_dir}/images/{sample.filename}"
            w.files.upload(dest_path, io.BytesIO(img_data), overwrite=True)
            image_count += 1
        except Exception as e:
            log.exception("Failed to copy image %s", sample.filename)
            if first_error is None:
                first_error = str(e)
            continue

        sample_anns = ann_by_sample.get(sample.id, [])

        if is_detection:
            coco_img_id = image_count
            coco["images"].append({
                "id": coco_img_id,
                "file_name": sample.filename,
                "width": img_w,
                "height": img_h,
            })

            for a in sample_anns:
                if a.ann_type == "bbox" and a.bbox_json:
                    bx = a.bbox_json["x"] * img_w
                    by = a.bbox_json["y"] * img_h
                    bw = a.bbox_json["w"] * img_w
                    bh = a.bbox_json["h"] * img_h
                    annotation_count += 1
                    coco["annotations"].append({
                        "id": annotation_count,
                        "image_id": coco_img_id,
                        "category_id": class_to_id.get(a.label, 0),
                        "bbox": [round(bx, 2), round(by, 2), round(bw, 2), round(bh, 2)],
                        "area": round(bw * bh, 2),
                        "iscrowd": 0,
                    })
        else:
            label = sample_anns[0].label if sample_anns else "unknown"
            csv_rows.append(f"{sample.filename},{label}")
            annotation_count += 1

    if image_count == 0:
        detail = "No images could be exported."
        if first_error:
            detail += f" First error: {first_error}"
        raise HTTPException(status_code=400, detail=detail)

    if is_detection:
        coco_bytes = json.dumps(coco, indent=2).encode("utf-8")
        w.files.upload(f"{export_dir}/annotations.json", io.BytesIO(coco_bytes), overwrite=True)
    else:
        csv_content = "filename,label\n" + "\n".join(csv_rows) + "\n"
        w.files.upload(f"{export_dir}/labels.csv", io.BytesIO(csv_content.encode("utf-8")), overwrite=True)

    metadata = {
        "project_id": p.id,
        "project_name": p.name,
        "version": p.version,
        "task_type": p.task_type,
        "class_list": p.class_list,
        "source_volume": p.source_volume,
        "image_count": image_count,
        "annotation_count": annotation_count,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "exported_by": get_user_email(request),
        "format": "coco" if is_detection else "csv",
    }
    w.files.upload(
        f"{export_dir}/metadata.json",
        io.BytesIO(json.dumps(metadata, indent=2).encode("utf-8")),
        overwrite=True,
    )

    return {
        "export_path": export_dir,
        "format": "coco" if is_detection else "csv",
        "images": image_count,
        "annotations": annotation_count,
    }
