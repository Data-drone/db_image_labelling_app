"""
Page 5: Dashboard

Dataset statistics, class distributions, and labeling progress.
"""

import streamlit as st

try:
    import plotly.express as px
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

from utils.datasets import (
    list_datasets, load_dataset, get_classes, get_tags,
    get_samples, get_dataset_session, count_samples,
)
from utils.database import Sample, Annotation, Tag
from utils.labeling import labeling_progress

st.set_page_config(page_title="Dashboard", page_icon="📊", layout="wide")

st.title("📊 Dashboard")
st.caption("Dataset statistics, class distributions, and annotation progress.")

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
# Metric cards
# -------------------------------------------------------------------------
total = count_samples(dataset, session)
labeled, _ = labeling_progress(dataset)
unlabeled = total - labeled
all_classes = get_classes(dataset, session)
all_tags = get_tags(dataset, session)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Samples", f"{total:,}")
col2.metric("Labeled", f"{labeled:,}", delta=f"{labeled/max(total,1)*100:.0f}%")
col3.metric("Unlabeled", f"{unlabeled:,}")
col4.metric("Classes", len(all_classes))

st.markdown("---")

# -------------------------------------------------------------------------
# Labeling progress
# -------------------------------------------------------------------------
st.subheader("Labeling Progress")
progress_pct = labeled / max(total, 1)
st.progress(progress_pct, text=f"{labeled} of {total} ({progress_pct:.0%})")

if unlabeled > 0:
    st.info(
        f"**{unlabeled:,}** samples still need labeling. "
        "Head to the **Labeling** page to annotate them.",
        icon="🏷️",
    )

st.markdown("---")

# -------------------------------------------------------------------------
# Class distribution
# -------------------------------------------------------------------------
st.subheader("Class Distribution")

if all_classes:
    # Count annotations per class
    class_counts = {}
    for cls in all_classes:
        count = (
            session.query(Annotation)
            .join(Sample)
            .filter(Sample.dataset_id == dataset.id)
            .filter(Annotation.label == cls)
            .count()
        )
        class_counts[cls] = count

    if HAS_PLOTLY and class_counts:
        fig = px.bar(
            x=list(class_counts.keys()),
            y=list(class_counts.values()),
            labels={"x": "Class", "y": "Count"},
            title="Annotations per Class",
            color=list(class_counts.keys()),
        )
        fig.update_layout(showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
    elif class_counts:
        for cls, count in sorted(class_counts.items(), key=lambda x: -x[1]):
            st.text(f"  {cls}: {count}")
else:
    st.info("No class labels found in this dataset yet.", icon="📭")

st.markdown("---")

# -------------------------------------------------------------------------
# Tag distribution
# -------------------------------------------------------------------------
st.subheader("Tag Distribution")

if all_tags:
    tag_counts = {}
    for tag in all_tags:
        count = (
            session.query(Tag)
            .join(Sample)
            .filter(Sample.dataset_id == dataset.id)
            .filter(Tag.tag == tag)
            .count()
        )
        tag_counts[tag] = count

    if HAS_PLOTLY:
        fig = px.pie(
            names=list(tag_counts.keys()),
            values=list(tag_counts.values()),
            title="Samples by Tag",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        for tag, count in sorted(tag_counts.items(), key=lambda x: -x[1]):
            st.text(f"  {tag}: {count}")
else:
    st.info("No tags found. Tags are added during labeling.", icon="📭")

st.markdown("---")

# -------------------------------------------------------------------------
# Annotations per image histogram
# -------------------------------------------------------------------------
st.subheader("Annotations per Image")

samples = get_samples(dataset, session)
ann_counts = [len(s.annotations) for s in samples]

if ann_counts and any(c > 0 for c in ann_counts):
    if HAS_PLOTLY:
        fig = px.histogram(
            x=ann_counts,
            nbins=max(max(ann_counts), 1),
            labels={"x": "Number of Annotations", "y": "Image Count"},
            title="Distribution of Annotations per Image",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        avg = sum(ann_counts) / len(ann_counts)
        st.metric("Avg annotations per image", f"{avg:.1f}")
else:
    st.info("No annotations found.", icon="📭")

# -------------------------------------------------------------------------
# Confidence distribution
# -------------------------------------------------------------------------
confidences = []
labels_for_conf = []
for s in samples:
    for ann in s.annotations:
        if ann.confidence is not None:
            confidences.append(ann.confidence)
            labels_for_conf.append(ann.label)

if confidences:
    st.subheader("Confidence Distribution")
    if HAS_PLOTLY:
        import pandas as pd
        df = pd.DataFrame({"confidence": confidences, "class": labels_for_conf})
        fig = px.histogram(
            df,
            x="confidence",
            color="class",
            nbins=50,
            title="Confidence Score Distribution by Class",
            barmode="overlay",
            opacity=0.7,
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        avg_conf = sum(confidences) / len(confidences)
        st.metric("Avg confidence", f"{avg_conf:.2f}")
