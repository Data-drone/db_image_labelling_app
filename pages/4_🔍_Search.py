"""
Page 4: Search

Search images by filename, label, or tag.
"""

import os

import streamlit as st

from utils.datasets import (
    list_datasets, load_dataset, get_samples, get_classes, get_tags,
    get_dataset_session,
)
from utils.database import Sample, Annotation, Tag
from utils.drawing import load_thumbnail, image_to_bytes

st.set_page_config(page_title="Search", page_icon="🔍", layout="wide")

st.title("🔍 Search")
st.caption("Find images by filename, label, or tag.")

# -------------------------------------------------------------------------
# Dataset selector
# -------------------------------------------------------------------------
datasets = list_datasets()
if not datasets:
    st.info("No datasets found. Create one on the Browse Volumes page.", icon="ℹ️")
    st.stop()

selected = st.selectbox(
    "Dataset",
    options=datasets,
    index=(
        datasets.index(st.session_state.current_dataset)
        if st.session_state.get("current_dataset") in datasets
        else 0
    ),
)
st.session_state.current_dataset = selected
dataset = load_dataset(selected)
if dataset is None:
    st.stop()

session = get_dataset_session(dataset)

# -------------------------------------------------------------------------
# Sidebar settings
# -------------------------------------------------------------------------
with st.sidebar:
    st.subheader("Settings")
    n_results = st.slider("Max results", 4, 96, 24)
    n_cols = st.select_slider("Columns", options=[3, 4, 5, 6], value=4)

# -------------------------------------------------------------------------
# Search modes
# -------------------------------------------------------------------------
tab_text, tab_label, tab_tag = st.tabs(["Filename Search", "Label Search", "Tag Search"])

results = None

with tab_text:
    query = st.text_input(
        "Search by filename",
        placeholder="e.g. IMG_001 or .jpg",
        help="Partial match on filenames.",
    )
    if query and st.button("Search", key="text_search"):
        results = (
            session.query(Sample)
            .filter(Sample.dataset_id == dataset.id)
            .filter(Sample.filename.ilike(f"%{query}%"))
            .limit(n_results)
            .all()
        )

with tab_label:
    all_classes = get_classes(dataset, session)
    if all_classes:
        selected_label = st.selectbox("Label", options=all_classes, key="search_label")
        if st.button("Find Samples", key="label_search"):
            results = (
                session.query(Sample)
                .filter(Sample.dataset_id == dataset.id)
                .filter(Sample.annotations.any(Annotation.label == selected_label))
                .limit(n_results)
                .all()
            )
    else:
        st.info("No labels found in this dataset.", icon="📭")

with tab_tag:
    all_tags = get_tags(dataset, session)
    if all_tags:
        selected_tag = st.selectbox("Tag", options=all_tags, key="search_tag")
        if st.button("Find Samples", key="tag_search"):
            results = (
                session.query(Sample)
                .filter(Sample.dataset_id == dataset.id)
                .filter(Sample.tags.any(Tag.tag == selected_tag))
                .limit(n_results)
                .all()
            )
    else:
        st.info("No tags found in this dataset.", icon="📭")

# -------------------------------------------------------------------------
# Display results
# -------------------------------------------------------------------------
if results is not None:
    st.markdown(f"### Results ({len(results)} matches)")
    if not results:
        st.info("No matches found.", icon="🔍")
    else:
        cols = st.columns(n_cols)
        for i, sample in enumerate(results):
            with cols[i % n_cols]:
                thumb = load_thumbnail(sample.filepath, size=(350, 350))
                if thumb:
                    st.image(image_to_bytes(thumb), use_container_width=True)
                st.caption(sample.filename)
                tags = sample.tag_list
                if tags:
                    st.caption(f"Tags: {', '.join(tags)}")
