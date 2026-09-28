"""
Dataset export route.
"""

import io
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import image_meta, masks as mask_utils
from ..deps import get_db, get_user_email
from ..dataset_exports import default_export_volume, export_reference_dataset, validate_volume_path
from ..models import LabelingProject, ProjectSample, Annotation
from ..schemas import ExportRequest
from ..volumes import read_image_bytes, _get_workspace_client

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["export"])


@router.post("/export")
def export_project(
    project_id: int,
    body: ExportRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Prepare a reference snapshot or copy a labeled dataset to a UC Volume."""
    p = db.query(LabelingProject).filter_by(id=project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Project not found.")

    export_path = body.export_volume.strip().rstrip("/") or default_export_volume(p)
    validate_volume_path(export_path)
    if body.mode == "reference":
        return export_reference_dataset(p, export_path, get_user_email(request), db, _get_workspace_client())
    from PIL import Image as PILImage

    log.info("Export requested: project=%s, export_path=%r", project_id, export_path)

    # export_path already cleared validate_volume_path above (UC Volume,
    # >=4 segments), so only the live existence of the volume is left to check.
    w = _get_workspace_client()
    parts = export_path.strip("/").split("/")
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

    is_segmentation = p.task_type == "segmentation"
    # Segmentation exports in COCO too -- the same file, with a `segmentation`
    # field alongside each `bbox`.
    is_coco = p.task_type == "detection" or is_segmentation
    image_count = 0
    annotation_count = 0
    mask_count = 0

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

            # Upload upright bytes and record the matching dimensions, so the
            # exported image shares the coordinate frame the masks and boxes
            # were drawn in.
            img_data, img_w, img_h = image_meta.normalize_for_export(img_data)
            if img_w is None or img_h is None:
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

        sample_anns = [
            a for a in ann_by_sample.get(sample.id, [])
            if not a.is_draft
        ]

        if is_coco:
            coco_img_id = image_count
            coco["images"].append({
                "id": coco_img_id,
                "file_name": sample.filename,
                "width": img_w,
                "height": img_h,
            })

            union_mask = None
            for a in sample_anns:
                if a.ann_type not in ("bbox", "mask") or not a.bbox_json:
                    continue
                bx = a.bbox_json["x"] * img_w
                by = a.bbox_json["y"] * img_h
                bw = a.bbox_json["w"] * img_w
                bh = a.bbox_json["h"] * img_h
                annotation_count += 1
                entry = {
                    "id": annotation_count,
                    "image_id": coco_img_id,
                    "category_id": class_to_id.get(a.label, 0),
                    "bbox": [round(bx, 2), round(by, 2), round(bw, 2), round(bh, 2)],
                    "area": round(bw * bh, 2),
                    "iscrowd": 0,
                }

                counts = _mask_counts(a, img_h, img_w, sample.filename)
                if counts is not None:
                    # pycocotools wants the compressed string, and `area` must
                    # be the mask's pixel count -- not the box area, which
                    # would inflate every AP number computed from this file.
                    entry["segmentation"] = {
                        "size": [img_h, img_w],
                        "counts": mask_utils.rle_to_string(counts),
                    }
                    entry["area"] = mask_utils.rle_area(counts)
                    union_mask = _accumulate(union_mask, counts, img_h, img_w)

                coco["annotations"].append(entry)

            if union_mask is not None:
                png = _mask_png(union_mask)
                if png is not None:
                    stem = sample.filename.rsplit(".", 1)[0]
                    w.files.upload(
                        f"{export_dir}/masks/{stem}.png",
                        io.BytesIO(png),
                        overwrite=True,
                    )
                    mask_count += 1
        else:
            label = sample_anns[0].label if sample_anns else "unknown"
            csv_rows.append(f"{sample.filename},{label}")
            annotation_count += 1

    if image_count == 0:
        detail = "No images could be exported."
        if first_error:
            detail += f" First error: {first_error}"
        raise HTTPException(status_code=400, detail=detail)

    if is_coco:
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
        "format": "coco" if is_coco else "csv",
        "mask_count": mask_count,
        # The `masks/` PNGs are a convenience for MVTec-AD / anomalib style
        # pipelines: one single-channel 0/255 image per sample, the union of
        # that sample's masks. Class and instance information is deliberately
        # not encoded there -- annotations.json carries it losslessly as
        # per-instance COCO RLE, and a label-map PNG would silently drop
        # overlapping instances.
        "mask_format": "binary_union_png_0_255" if mask_count else None,
        # UC Lineage: track which samples/annotations produced this export
        "lineage": {
            "source_volume_uc": p.source_volume,
            "export_volume_uc": export_path,
            "sample_ids": [s.id for s in samples],
            "sample_count": len(samples),
            "annotation_ids": [
                a.id for s in samples
                for a in ann_by_sample.get(s.id, []) if not a.is_draft
            ],
        },
    }
    w.files.upload(
        f"{export_dir}/metadata.json",
        io.BytesIO(json.dumps(metadata, indent=2).encode("utf-8")),
        overwrite=True,
    )

    return {
        "export_path": export_dir,
        "format": "coco" if is_coco else "csv",
        "images": image_count,
        "annotations": annotation_count,
        "masks": mask_count,
    }


def _mask_counts(ann, img_h: int, img_w: int, filename: str):
    """Uncompressed runs for an annotation's mask, or None if unusable.

    A mask whose grid does not match the image it is being exported against
    is skipped rather than written out wrong -- a missing `segmentation` is
    recoverable, a misaligned one silently poisons training.
    """
    if not ann.mask_json:
        return None
    wire = mask_utils.to_wire(ann.mask_json)
    if not wire:
        log.warning("Unreadable mask on annotation %s, skipping", ann.id)
        return None
    if wire["size"] != [img_h, img_w]:
        log.warning(
            "Mask size %s on annotation %s does not match image %s (%dx%d), skipping",
            wire["size"], ann.id, filename, img_h, img_w,
        )
        return None
    return wire["counts"]


def _accumulate(union, counts, img_h: int, img_w: int):
    """OR a mask into a running union bitmap."""
    try:
        import numpy as np
        bitmap = mask_utils.bitmap_from_counts(counts, img_h, img_w)
        return bitmap if union is None else np.maximum(union, bitmap)
    except Exception as e:
        log.warning("Could not rasterise mask for PNG export: %s", e)
        return union


def _mask_png(bitmap):
    """Encode a 0/1 bitmap as a single-channel 0/255 PNG."""
    try:
        from PIL import Image as PILImage
        buf = io.BytesIO()
        PILImage.fromarray((bitmap * 255).astype("uint8"), mode="L").save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        log.warning("Could not encode mask PNG: %s", e)
        return None
