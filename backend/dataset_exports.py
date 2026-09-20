"""Training snapshots that reference existing UC Volume images."""

import io
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_

from .models import Annotation, ProjectSample

log = logging.getLogger(__name__)
CLASSIFICATION_REFERENCE_FORMAT = "hf_jsonl"
DETECTION_REFERENCE_FORMAT = "coco_reference"
REFERENCE_FORMAT = CLASSIFICATION_REFERENCE_FORMAT  # Backward-compatible import.
REFERENCE_FORMATS = {CLASSIFICATION_REFERENCE_FORMAT, DETECTION_REFERENCE_FORMAT}


def default_export_volume(project):
    configured = os.environ.get("EXPORT_VOLUME_PATH", "").strip().rstrip("/")
    if configured:
        return configured
    source = os.environ.get("DEMO_VOLUME_PATH", "").strip() or project.source_volume
    return source.rstrip("/") + "/exports"


def validate_volume_path(path):
    if not isinstance(path, str):
        raise HTTPException(400, "Use a UC Volume path: /Volumes/catalog/schema/volume/...")
    parts = path.split("/")
    if (
        not path.startswith("/Volumes/")
        or len(parts) < 5
        or any(part in ("", ".", "..") for part in parts[1:])
        or "\\" in path
        or any(ord(char) < 32 for char in path)
    ):
        raise HTTPException(400, "Use a UC Volume path: /Volumes/catalog/schema/volume/...")


def _is_source_image_path(path, source_volume):
    """Return whether path is a valid child of the project's source directory."""
    try:
        validate_volume_path(path)
        validate_volume_path(source_volume)
    except HTTPException:
        return False
    return path.startswith(source_volume.rstrip("/") + "/")


def huggingface_loading_code(export_path):
    return f'''import json
from pathlib import Path
from datasets import ClassLabel, Image, load_dataset

export_dir = Path({export_path!r})
classes = json.loads((export_dir / "classes.json").read_text())
dataset = load_dataset("json", data_files=str(export_dir / "train.jsonl"), split="train")
dataset = dataset.cast_column("label", ClassLabel(names=classes["names"]))
dataset = dataset.cast_column("image", Image())
'''


def coco_loading_code(export_path):
    """Return code that materializes pixel COCO from normalized reference boxes."""
    return f'''import copy
import json
from pathlib import Path
from PIL import Image

export_dir = Path({export_path!r})
reference_coco = json.loads((export_dir / "annotations.json").read_text())
coco = copy.deepcopy(reference_coco)
image_sizes = {{}}
for image in coco["images"]:
    with Image.open(image["file_name"]) as source:
        image["width"], image["height"] = source.size
    image_sizes[image["id"]] = (image["width"], image["height"])

for annotation in coco["annotations"]:
    width, height = image_sizes[annotation["image_id"]]
    x, y, box_width, box_height = annotation.pop("bbox_normalized")
    annotation["bbox"] = [x * width, y * height, box_width * width, box_height * height]
    annotation["area"] = annotation["bbox"][2] * annotation["bbox"][3]

# Optional pycocotools index without writing or copying images:
# from pycocotools.coco import COCO
# coco_api = COCO()
# coco_api.dataset = coco
# coco_api.createIndex()
'''


def reference_loading_code(export_path, task_type):
    if task_type == "detection":
        return coco_loading_code(export_path)
    return huggingface_loading_code(export_path)


def supports_reference_training():
    return os.environ.get("FINETUNE_SUPPORTS_REFERENCE_DATASETS", "false").lower() == "true"


def _validated_classes(project):
    classes = list(project.class_list)
    if (
        not classes
        or any(not isinstance(label, str) or not label.strip() for label in classes)
        or len(set(classes)) != len(classes)
    ):
        raise HTTPException(422, "Project classes must be non-empty and unique.")
    return classes


def _snapshot_context(project, export_path, exported_by, image_count, annotation_count, sample_ids, annotation_ids, format_name):
    exported_at = datetime.now(timezone.utc)
    snapshot_id = uuid4().hex
    safe_name = re.sub(r"[^\w-]", "_", project.name)[:100]
    export_dir = f"{export_path}/{safe_name}_v{project.version}_{snapshot_id}"
    metadata = {
        "project_id": project.id,
        "project_name": project.name,
        "version": project.version,
        "task_type": project.task_type,
        "class_list": list(project.class_list),
        "source_volume": project.source_volume,
        "image_count": image_count,
        "annotation_count": annotation_count,
        "exported_at": exported_at.isoformat(),
        "exported_by": exported_by,
        "format": format_name,
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "images_copied": False,
        "source_images_verified": False,
        "status": "ready",
        "lineage": {
            "source_volume_uc": project.source_volume,
            "export_volume_uc": export_path,
            "sample_ids": sample_ids,
            "sample_count": image_count,
            "annotation_ids": annotation_ids,
        },
    }
    return export_dir, metadata


def _publish_artifacts(workspace, export_dir, artifacts):
    try:
        # Files API uploads require the parent directory to exist. Creating it
        # is metadata-only and does not touch any source image bytes.
        workspace.files.create_directory(export_dir)
        for filename, content in artifacts:
            workspace.files.upload(
                f"{export_dir}/{filename}",
                io.BytesIO(content.encode("utf-8")),
                overwrite=False,
            )
    except Exception as exc:
        log.exception("Could not publish dataset %s", export_dir)
        raise HTTPException(502, "Could not finish writing the dataset. Check export Volume permissions and retry.") from exc


def _export_classification_reference(project, export_path, exported_by, db, workspace, classes):
    label_to_id = {label: index for index, label in enumerate(classes)}
    rows = (
        db.query(
            ProjectSample.id, ProjectSample.filepath,
            Annotation.id.label("annotation_id"), Annotation.label,
            Annotation.ann_type,
        )
        .outerjoin(Annotation, and_(
            Annotation.sample_id == ProjectSample.id,
            Annotation.project_id == project.id,
            Annotation.is_draft.is_(False),
        ))
        .filter(ProjectSample.project_id == project.id, ProjectSample.status == "labeled")
        .order_by(ProjectSample.id, Annotation.id)
        .all()
    )
    if not rows:
        raise HTTPException(400, "No labeled samples to prepare. Confirm labels first.")

    records = {}
    invalid = set()
    annotation_ids = []
    for row in rows:
        if row.id in records or row.annotation_id is None or row.ann_type != "classification" or row.label not in label_to_id:
            invalid.add(row.id)
        if not _is_source_image_path(row.filepath, project.source_volume):
            invalid.add(row.id)
        records[row.id] = {
            "sample_id": row.id,
            "image": row.filepath,
            "label": label_to_id.get(row.label),
        }
        if row.annotation_id is not None:
            annotation_ids.append(row.annotation_id)
    if invalid:
        raise HTTPException(422, {
            "message": f"Cannot prepare dataset: {len(invalid)} labeled samples need attention. "
                       "Each needs one confirmed classification label from the project classes "
                       "and a UC Volume image path. "
                       f"Sample IDs: {', '.join(map(str, sorted(invalid)[:10]))}.",
            "invalid_sample_count": len(invalid),
        })

    export_dir, metadata = _snapshot_context(
        project, export_path, exported_by, len(records), len(records),
        list(records), annotation_ids, CLASSIFICATION_REFERENCE_FORMAT,
    )
    _publish_artifacts(workspace, export_dir, [
        ("train.jsonl", "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records.values())),
        ("classes.json", json.dumps({"names": classes, "label2id": label_to_id}, ensure_ascii=False, indent=2)),
        ("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2)),
    ])

    return {
        "export_path": export_dir,
        "format": CLASSIFICATION_REFERENCE_FORMAT,
        "images": len(records),
        "annotations": len(records),
        "exported_at": metadata["exported_at"],
        "version": metadata["version"],
        "snapshot_id": metadata["snapshot_id"],
        "huggingface_code": huggingface_loading_code(export_dir),
        "loading_code": huggingface_loading_code(export_dir),
    }


def _export_detection_reference(project, export_path, exported_by, db, workspace, classes):
    # COCO datasets conventionally use positive category IDs; several common
    # training adapters reserve 0 for background even though pycocotools can
    # index arbitrary integers.
    class_to_id = {label: index for index, label in enumerate(classes, start=1)}
    rows = (
        db.query(
            ProjectSample.id,
            ProjectSample.filepath,
            Annotation.id.label("annotation_id"),
            Annotation.label,
            Annotation.ann_type,
            Annotation.bbox_json,
        )
        .outerjoin(Annotation, and_(
            Annotation.sample_id == ProjectSample.id,
            Annotation.project_id == project.id,
            Annotation.is_draft.is_(False),
        ))
        .filter(ProjectSample.project_id == project.id, ProjectSample.status == "labeled")
        .order_by(ProjectSample.id, Annotation.id)
        .all()
    )
    if not rows:
        raise HTTPException(400, "No labeled samples to prepare. Confirm boxes first.")

    invalid = set()
    sample_ids = []
    coco_images = []
    coco_annotations = []
    annotation_ids = []
    seen_samples = set()
    for row in rows:
        if row.id not in seen_samples:
            seen_samples.add(row.id)
            sample_ids.append(row.id)
            if not _is_source_image_path(row.filepath, project.source_volume):
                invalid.add(row.id)
            coco_images.append({"id": row.id, "file_name": row.filepath})

        # A labeled detection image with no boxes is a valid negative sample.
        if row.annotation_id is None:
            continue
        bbox = row.bbox_json
        if (
            row.ann_type != "bbox"
            or row.label not in class_to_id
            or not isinstance(bbox, dict)
            or any(key not in bbox for key in ("x", "y", "w", "h"))
        ):
            invalid.add(row.id)
            continue
        values = [bbox[key] for key in ("x", "y", "w", "h")]
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in values
            )
            or values[0] < 0 or values[1] < 0
            or values[2] <= 0 or values[3] <= 0
            or values[0] + values[2] > 1
            or values[1] + values[3] > 1
        ):
            invalid.add(row.id)
            continue
        coco_annotations.append({
            "id": row.annotation_id,
            "image_id": row.id,
            "category_id": class_to_id[row.label],
            "bbox_normalized": values,
            "iscrowd": 0,
        })
        annotation_ids.append(row.annotation_id)

    if invalid:
        raise HTTPException(422, {
            "message": f"Cannot prepare dataset: {len(invalid)} labeled samples have invalid boxes, labels, or image paths. "
                       f"Sample IDs: {', '.join(map(str, sorted(invalid)[:10]))}.",
            "invalid_sample_count": len(invalid),
        })

    coco = {
        "info": {
            "description": f"CV Explorer reference export: {project.name} v{project.version}",
            "version": "1.0",
            "reference_images": True,
            "bbox_format": "relative_xywh",
        },
        "images": coco_images,
        "annotations": coco_annotations,
        "categories": [
            {"id": index, "name": label}
            for index, label in enumerate(classes, start=1)
        ],
    }
    export_dir, metadata = _snapshot_context(
        project, export_path, exported_by, len(sample_ids), len(coco_annotations),
        sample_ids, annotation_ids, DETECTION_REFERENCE_FORMAT,
    )
    metadata["bbox_format"] = "relative_xywh"
    metadata["requires_dimension_materialization"] = True
    _publish_artifacts(workspace, export_dir, [
        ("annotations.json", json.dumps(coco, ensure_ascii=False, separators=(",", ":"), allow_nan=False)),
        ("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2)),
    ])
    loading_code = coco_loading_code(export_dir)
    return {
        "export_path": export_dir,
        "format": DETECTION_REFERENCE_FORMAT,
        "images": len(sample_ids),
        "annotations": len(coco_annotations),
        "exported_at": metadata["exported_at"],
        "version": metadata["version"],
        "snapshot_id": metadata["snapshot_id"],
        "loading_code": loading_code,
    }


def export_reference_dataset(project, export_path, exported_by, db, workspace):
    classes = _validated_classes(project)
    if project.task_type == "classification":
        return _export_classification_reference(project, export_path, exported_by, db, workspace, classes)
    if project.task_type == "detection":
        return _export_detection_reference(project, export_path, exported_by, db, workspace, classes)
    raise HTTPException(400, f"Unsupported project task type: {project.task_type}.")
