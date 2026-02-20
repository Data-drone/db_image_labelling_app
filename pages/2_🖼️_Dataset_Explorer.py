"""
Page 2: Dataset Explorer

Gallery view with bounding-box overlays, class/tag/confidence filters,
pagination, and COCO export.
"""

import json

import streamlit as st

from utils.config import COLUMN_OPTIONS, DEFAULT_COLUMNS, DEFAULT_PAGE_SIZE
from utils.datasets import (
    list_datasets,
    load_dataset,
    get_classes,
    get_tags,
    get_samples,
    filtered_samples,
    count_samples,
    export_coco,
)
from utils.drawing import load_image, draw_detections, image_to_bytes, load_thumbnail

st.set_page_config(page_title="Dataset Explorer", page_icon="🖼️", layout="wide")

st.title("🖼️ Dataset Explorer")
st.caption("Browse, filter, and inspect images with bounding-box overlays.")

# -------------------------------------------------------------------------
# Dataset selector
# -------------------------------------------------------------------------
datasets = list_datasets()

if not datasets:
    st.info(
        "No datasets found. Go to **Browse Volumes** to create one.",
        icon="ℹ️",
    )
    st.stop()

selected = st.selectbox(
    "Dataset",
    options=datasets,
    index=(
        datasets.index(st.session_state.current_dataset)
        if st.session_state.get("current_dataset") in datasets
        else 0
    ),
    help="Pick a dataset to explore.",
)
st.session_state.current_dataset = selected
dataset = load_dataset(selected)

if dataset is None:
    st.error("Failed to load dataset.")
    st.stop()

# -------------------------------------------------------------------------
# Sidebar filters
# -------------------------------------------------------------------------
with st.sidebar:
    st.subheader("Filters")

    all_classes = get_classes(dataset)
    if all_classes:
        selected_labels = st.multiselect(
            "Class labels",
            options=all_classes,
            default=[],
            help="Show only samples containing these labels.",
        )
    else:
        selected_labels = []

    all_tags = get_tags(dataset)
    if all_tags:
        selected_tags = st.multiselect(
            "Tags",
            options=all_tags,
            default=[],
            help="Show only samples with these tags.",
        )
    else:
        selected_tags = []

    confidence = st.slider(
        "Min confidence",
        0.0, 1.0, 0.0, 0.05,
        help="Hide detections below this confidence score.",
    )

    st.markdown("---")

    st.subheader("Gallery Settings")
    n_cols = st.select_slider("Columns", options=COLUMN_OPTIONS, value=DEFAULT_COLUMNS)
    page_size = st.select_slider(
        "Page size", options=[12, 24, 48, 96], value=DEFAULT_PAGE_SIZE
    )

    st.markdown("---")

    if st.button("Export filtered view to COCO JSON"):
        path = export_coco(dataset)
        if path:
            st.success(f"Exported to `{path}`")

# -------------------------------------------------------------------------
# Apply filters
# -------------------------------------------------------------------------
if selected_labels or selected_tags or confidence > 0:
    samples = filtered_samples(dataset, selected_labels, selected_tags, confidence)
else:
    samples = get_samples(dataset)

total = len(samples)

st.markdown(
    f"**{total}** samples"
    f"{' (filtered)' if selected_labels or selected_tags or confidence > 0 else ''}"
)

if total == 0:
    st.info("No samples match the current filters.", icon="🔍")
    st.stop()

# -------------------------------------------------------------------------
# Pagination
# -------------------------------------------------------------------------
max_page = max(0, (total - 1) // page_size)
page = st.session_state.get("gallery_page", 0)
page = min(page, max_page)

nav_cols = st.columns([1, 3, 1])
with nav_cols[0]:
    if st.button("◀ Prev", disabled=page == 0):
        page = max(page - 1, 0)
        st.session_state.gallery_page = page
        st.rerun()
with nav_cols[1]:
    st.markdown(
        f"<div style='text-align:center'>Page {page + 1} of {max_page + 1}</div>",
        unsafe_allow_html=True,
    )
with nav_cols[2]:
    if st.button("Next ▶", disabled=page >= max_page):
        page = min(page + 1, max_page)
        st.session_state.gallery_page = page
        st.rerun()

# -------------------------------------------------------------------------
# Gallery grid
# -------------------------------------------------------------------------
start = page * page_size
end = min(start + page_size, total)
page_samples = samples[start:end]

cols = st.columns(n_cols)
for i, sample in enumerate(page_samples):
    with cols[i % n_cols]:
        thumb = load_thumbnail(sample.filepath, size=(400, 400))
        if thumb is None:
            st.text(f"Cannot load: {sample.filepath}")
            continue

        # Draw detections if present
        detections = [a for a in sample.annotations if a.ann_type == "detection"]
        if detections:
            thumb = draw_detections(thumb, detections, all_classes)

        st.image(image_to_bytes(thumb), use_container_width=True)

        # Caption
        caption_parts = [sample.filename]
        tags = sample.tag_list
        if tags:
            caption_parts.append(f"Tags: {', '.join(tags)}")
        st.caption(" | ".join(caption_parts))

        # Expander for full details
        with st.expander("Details"):
            full_img = load_image(sample.filepath)
            if full_img and detections:
                full_img = draw_detections(full_img, detections, all_classes)
            if full_img:
                st.image(image_to_bytes(full_img), use_container_width=True)

            st.text(f"Path: {sample.filepath}")
            for ann in sample.annotations:
                if ann.bbox_json:
                    bbox = json.loads(ann.bbox_json)
                    conf_str = f" ({ann.confidence:.1%})" if ann.confidence else ""
                    st.text(
                        f"  - {ann.label}{conf_str}  "
                        f"bbox=[{bbox['x']:.3f}, {bbox['y']:.3f}, {bbox['w']:.3f}, {bbox['h']:.3f}]"
                    )
                elif ann.ann_type == "classification":
                    st.text(f"  Classification: {ann.label}")
