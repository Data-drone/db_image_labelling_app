"""
Labeling state management, navigation, and annotation persistence.
Backed by SQLAlchemy (replaces FiftyOne).
"""

import json
from typing import Optional

import streamlit as st

from utils.config import DEFAULT_CLASSES, QUICK_TAGS
from utils.database import get_session, Dataset, Sample, Annotation, Tag
from utils.datasets import get_dataset_session, get_sample_at_index, count_samples


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def init_labeling_state(dataset) -> None:
    """Ensure all labeling session-state keys exist."""
    if "labeling_index" not in st.session_state:
        st.session_state.labeling_index = 0
    if "label_classes" not in st.session_state:
        st.session_state.label_classes = list(DEFAULT_CLASSES)
    if "pending_boxes" not in st.session_state:
        st.session_state.pending_boxes = []
    if "pending_polygons" not in st.session_state:
        st.session_state.pending_polygons = []
    if "labeling_mode" not in st.session_state:
        st.session_state.labeling_mode = "Classification"
    if "autosave" not in st.session_state:
        st.session_state.autosave = True


def current_sample(dataset) -> Optional[Sample]:
    """Return the sample at the current labeling index, or None."""
    if dataset is None:
        return None
    total = count_samples(dataset)
    if total == 0:
        return None
    idx = st.session_state.get("labeling_index", 0)
    idx = max(0, min(idx, total - 1))
    st.session_state.labeling_index = idx
    return get_sample_at_index(dataset, idx)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def _clear_canvas_state():
    """Reset all canvas-related session state for a fresh canvas on the next sample."""
    st.session_state.pending_boxes = []
    st.session_state.pending_polygons = []
    st.session_state.pop("bbox_canvas_state", None)
    st.session_state.pop("poly_canvas_state", None)
    # Increment reset counters so the canvas key changes → fresh component
    st.session_state["bbox_reset"] = st.session_state.get("bbox_reset", 0) + 1
    st.session_state["poly_reset"] = st.session_state.get("poly_reset", 0) + 1


def go_next(dataset) -> None:
    """Advance to the next sample."""
    if dataset is None:
        return
    total = count_samples(dataset)
    st.session_state.labeling_index = min(
        st.session_state.labeling_index + 1, total - 1
    )
    _clear_canvas_state()


def go_prev() -> None:
    """Go to the previous sample."""
    st.session_state.labeling_index = max(st.session_state.labeling_index - 1, 0)
    _clear_canvas_state()


def go_skip(dataset) -> None:
    """Tag current sample as 'skip' and advance."""
    sample = current_sample(dataset)
    if sample:
        _add_tag(sample, "skip")
    go_next(dataset)


def go_flag(dataset) -> None:
    """Tag current sample as 'flagged' and advance."""
    sample = current_sample(dataset)
    if sample:
        _add_tag(sample, "flagged")
    go_next(dataset)


# ---------------------------------------------------------------------------
# Annotation persistence
# ---------------------------------------------------------------------------

def save_classification(dataset, label: str) -> None:
    """Save a classification label on the current sample."""
    sample = current_sample(dataset)
    if sample is None:
        return
    session = get_dataset_session(dataset)
    annotation = Annotation(
        sample_id=sample.id,
        ann_type="classification",
        label=label,
    )
    session.add(annotation)
    _add_tag(sample, "labeled", session=session)
    session.commit()
    go_next(dataset)


def save_detections(dataset, boxes: list[dict], advance: bool = True) -> None:
    """
    Save bounding-box detections on the current sample.

    Each item in *boxes* should have keys:
        x, y, width, height  (pixel coords relative to image size)
        label                 (class name)
    """
    sample = current_sample(dataset)
    if sample is None or not boxes:
        return
    session = get_dataset_session(dataset)

    # Load image to get dimensions
    try:
        from PIL import Image
        img = Image.open(sample.filepath)
        img_w, img_h = img.size
    except Exception:
        st.error("Cannot read image dimensions.")
        return

    for box in boxes:
        # Normalise to [0, 1]
        bbox_norm = {
            "x": box["x"] / img_w,
            "y": box["y"] / img_h,
            "w": box["width"] / img_w,
            "h": box["height"] / img_h,
        }
        annotation = Annotation(
            sample_id=sample.id,
            ann_type="detection",
            label=box.get("label", "unknown"),
            bbox_json=json.dumps(bbox_norm),
        )
        session.add(annotation)

    _add_tag(sample, "labeled", session=session)
    session.commit()
    st.session_state.pending_boxes = []
    if advance:
        go_next(dataset)


def save_polygons(dataset, polygons: list[dict], advance: bool = True) -> None:
    """
    Save polygon/segmentation annotations on the current sample.

    Each item in *polygons* should have keys:
        points   list of [x, y] in pixel coords
        label    class name
    """
    sample = current_sample(dataset)
    if sample is None or not polygons:
        return
    session = get_dataset_session(dataset)

    # Load image to get dimensions
    try:
        from PIL import Image
        img = Image.open(sample.filepath)
        img_w, img_h = img.size
    except Exception:
        st.error("Cannot read image dimensions.")
        return

    for poly in polygons:
        points = poly.get("points", [])
        if len(points) < 3:
            continue
        # Normalise to [0, 1]
        norm_points = [[p[0] / img_w, p[1] / img_h] for p in points]
        annotation = Annotation(
            sample_id=sample.id,
            ann_type="segmentation",
            label=poly.get("label", "unknown"),
            polygon_json=json.dumps(norm_points),
        )
        session.add(annotation)

    _add_tag(sample, "labeled", session=session)
    session.commit()
    st.session_state.pending_polygons = []
    if advance:
        go_next(dataset)


def save_tags(dataset, tags: list[str]) -> None:
    """Add tags to the current sample."""
    sample = current_sample(dataset)
    if sample is None:
        return
    session = get_dataset_session(dataset)
    for t in tags:
        if t:
            _add_tag(sample, t, session=session)
    session.commit()
    go_next(dataset)


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

def labeling_progress(dataset) -> tuple[int, int]:
    """Return (labeled_count, total_count)."""
    if dataset is None:
        return 0, 0
    session = get_dataset_session(dataset)
    total = count_samples(dataset, session)
    labeled = (
        session.query(Sample)
        .filter(Sample.dataset_id == dataset.id)
        .filter(Sample.tags.any(Tag.tag == "labeled"))
        .count()
    )
    return labeled, total


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _add_tag(sample: Sample, tag: str, session=None):
    """Add a tag to a sample if it doesn't already exist."""
    if session is None:
        from sqlalchemy import inspect
        session = inspect(sample).session or get_session()
    # Check if tag already exists
    existing = (
        session.query(Tag)
        .filter_by(sample_id=sample.id, tag=tag)
        .first()
    )
    if not existing:
        session.add(Tag(sample_id=sample.id, tag=tag))
