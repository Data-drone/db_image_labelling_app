"""
Dataset helpers: create, list, load, delete, import, export.
Backed by SQLAlchemy (SQLite or PostgreSQL).
"""

import json
import os
from typing import Optional

import streamlit as st

from utils.config import IMAGE_EXTENSIONS, COCO_EXPORT_PATH
from utils.database import get_session, Dataset, Sample, Annotation, Tag


# ---------------------------------------------------------------------------
# Dataset CRUD
# ---------------------------------------------------------------------------

def list_datasets() -> list[str]:
    """Return all existing dataset names."""
    try:
        session = get_session()
        datasets = session.query(Dataset).order_by(Dataset.name).all()
        names = [d.name for d in datasets]
        session.close()
        return names
    except Exception as exc:
        st.error(f"Error listing datasets: {exc}")
        return []


def load_dataset(name: str) -> Optional[Dataset]:
    """Load and return a dataset by name (returns the ORM object)."""
    try:
        session = get_session()
        ds = session.query(Dataset).filter_by(name=name).first()
        if ds is None:
            st.error(f"Dataset '{name}' not found.")
        return ds
    except Exception as exc:
        st.error(f"Error loading dataset '{name}': {exc}")
        return None


def get_dataset_session(dataset: Dataset):
    """Return the session bound to a dataset object."""
    from sqlalchemy import inspect
    insp = inspect(dataset)
    return insp.session or get_session()


def delete_dataset(name: str) -> bool:
    """Delete a dataset and all its samples/annotations. Returns True on success."""
    try:
        session = get_session()
        ds = session.query(Dataset).filter_by(name=name).first()
        if ds:
            session.delete(ds)
            session.commit()
            session.close()
            return True
        session.close()
        return False
    except Exception as exc:
        st.error(f"Error deleting dataset '{name}': {exc}")
        return False


def create_dataset_from_directory(
    name: str,
    image_dir: str,
    dataset_type: str = "image",
) -> bool:
    """
    Import images from *image_dir* into a new dataset.
    Scans the directory for image files and creates Sample rows.
    If a COCO labels.json exists, imports annotations too.
    """
    try:
        session = get_session()

        # Check if already exists
        existing = session.query(Dataset).filter_by(name=name).first()
        if existing:
            st.warning(f"Dataset '{name}' already exists.")
            session.close()
            return False

        ds = Dataset(name=name, image_dir=image_dir)
        session.add(ds)
        session.flush()  # get ds.id

        # Scan for images
        image_files = []
        for fname in sorted(os.listdir(image_dir)):
            ext = os.path.splitext(fname)[1].lower()
            if ext in IMAGE_EXTENSIONS:
                image_files.append(fname)

        # Create samples
        for fname in image_files:
            sample = Sample(
                dataset_id=ds.id,
                filepath=os.path.join(image_dir, fname),
                filename=fname,
            )
            session.add(sample)

        session.flush()

        # Check for COCO annotations
        coco_path = os.path.join(image_dir, "labels.json")
        if os.path.exists(coco_path):
            _import_coco_annotations(session, ds, coco_path)

        session.commit()
        st.success(
            f"Created dataset **{name}** with {len(image_files)} samples.",
            icon="✅",
        )
        session.close()
        return True
    except Exception as exc:
        st.error(f"Error creating dataset: {exc}")
        return False


def _import_coco_annotations(session, dataset: Dataset, coco_path: str):
    """Import COCO-format annotations from a labels.json file."""
    try:
        with open(coco_path) as f:
            coco = json.load(f)

        images = {img["id"]: img for img in coco.get("images", [])}
        categories = {cat["id"]: cat["name"] for cat in coco.get("categories", [])}

        samples = session.query(Sample).filter_by(dataset_id=dataset.id).all()
        fname_to_sample = {s.filename: s for s in samples}

        for ann in coco.get("annotations", []):
            img_info = images.get(ann.get("image_id"))
            if not img_info:
                continue
            fname = img_info.get("file_name", "")
            sample = fname_to_sample.get(fname)
            if not sample:
                continue

            cat_name = categories.get(ann.get("category_id"), "unknown")
            bbox = ann.get("bbox", [])

            if bbox and len(bbox) == 4:
                img_w = img_info.get("width", 1)
                img_h = img_info.get("height", 1)
                bbox_norm = {
                    "x": bbox[0] / img_w,
                    "y": bbox[1] / img_h,
                    "w": bbox[2] / img_w,
                    "h": bbox[3] / img_h,
                }
                annotation = Annotation(
                    sample_id=sample.id,
                    ann_type="detection",
                    label=cat_name,
                    bbox_json=json.dumps(bbox_norm),
                )
            else:
                annotation = Annotation(
                    sample_id=sample.id,
                    ann_type="classification",
                    label=cat_name,
                )
            session.add(annotation)
    except Exception as exc:
        st.warning(f"Could not import COCO annotations: {exc}")


# ---------------------------------------------------------------------------
# Querying helpers
# ---------------------------------------------------------------------------

def get_samples(dataset: Dataset, session=None) -> list[Sample]:
    """Return all samples in a dataset."""
    session = session or get_dataset_session(dataset)
    return (
        session.query(Sample)
        .filter_by(dataset_id=dataset.id)
        .order_by(Sample.id)
        .all()
    )


def count_samples(dataset: Dataset, session=None) -> int:
    """Return total number of samples in a dataset."""
    session = session or get_dataset_session(dataset)
    return session.query(Sample).filter_by(dataset_id=dataset.id).count()


def get_sample_at_index(dataset: Dataset, index: int, session=None) -> Optional[Sample]:
    """Return sample at the given index (0-based)."""
    session = session or get_dataset_session(dataset)
    return (
        session.query(Sample)
        .filter_by(dataset_id=dataset.id)
        .order_by(Sample.id)
        .offset(index)
        .first()
    )


def get_classes(dataset: Dataset, session=None) -> list[str]:
    """Return distinct annotation labels in the dataset."""
    session = session or get_dataset_session(dataset)
    try:
        results = (
            session.query(Annotation.label)
            .join(Sample)
            .filter(Sample.dataset_id == dataset.id)
            .distinct()
            .all()
        )
        return sorted([r[0] for r in results])
    except Exception:
        return []


def get_tags(dataset: Dataset, session=None) -> list[str]:
    """Return distinct sample-level tags."""
    session = session or get_dataset_session(dataset)
    try:
        results = (
            session.query(Tag.tag)
            .join(Sample)
            .filter(Sample.dataset_id == dataset.id)
            .distinct()
            .all()
        )
        return sorted([r[0] for r in results])
    except Exception:
        return []


def filtered_samples(
    dataset: Dataset,
    labels: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    confidence: float = 0.0,
    session=None,
) -> list[Sample]:
    """Return samples filtered by labels, tags, and minimum confidence."""
    session = session or get_dataset_session(dataset)
    query = session.query(Sample).filter(Sample.dataset_id == dataset.id)

    if tags:
        query = query.filter(Sample.tags.any(Tag.tag.in_(tags)))

    if labels:
        query = query.filter(
            Sample.annotations.any(Annotation.label.in_(labels))
        )

    if confidence > 0:
        query = query.filter(
            Sample.annotations.any(Annotation.confidence >= confidence)
        )

    return query.order_by(Sample.id).all()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_coco(dataset: Dataset, export_dir: Optional[str] = None, session=None) -> str:
    """Export a dataset to COCO JSON format. Returns the export directory."""
    export_dir = export_dir or COCO_EXPORT_PATH
    os.makedirs(export_dir, exist_ok=True)
    session = session or get_dataset_session(dataset)

    try:
        samples = get_samples(dataset, session)

        coco = {"images": [], "annotations": [], "categories": []}

        all_labels = get_classes(dataset, session)
        cat_map = {}
        for i, label in enumerate(all_labels):
            cat_id = i + 1
            cat_map[label] = cat_id
            coco["categories"].append({"id": cat_id, "name": label})

        ann_id = 1
        for img_idx, sample in enumerate(samples):
            img_id = img_idx + 1
            try:
                from PIL import Image
                img = Image.open(sample.filepath)
                w, h = img.size
            except Exception:
                w, h = 0, 0

            coco["images"].append({
                "id": img_id,
                "file_name": sample.filename,
                "width": w,
                "height": h,
            })

            for ann in sample.annotations:
                coco_ann = {
                    "id": ann_id,
                    "image_id": img_id,
                    "category_id": cat_map.get(ann.label, 0),
                }
                if ann.bbox_json:
                    bbox_data = json.loads(ann.bbox_json)
                    coco_ann["bbox"] = [
                        bbox_data["x"] * w,
                        bbox_data["y"] * h,
                        bbox_data["w"] * w,
                        bbox_data["h"] * h,
                    ]
                    coco_ann["area"] = coco_ann["bbox"][2] * coco_ann["bbox"][3]
                if ann.confidence is not None:
                    coco_ann["score"] = ann.confidence
                coco["annotations"].append(coco_ann)
                ann_id += 1

        output_path = os.path.join(export_dir, "labels.json")
        with open(output_path, "w") as f:
            json.dump(coco, f, indent=2)

        return export_dir
    except Exception as exc:
        st.error(f"Export failed: {exc}")
        return ""
