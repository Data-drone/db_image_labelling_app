"""
CV Dataset Explorer - Home Page
Databricks App for browsing UC Volumes, exploring CV datasets, and labeling images.
"""

import streamlit as st
import os

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="CV Dataset Explorer",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "current_dataset": None,
    "current_volume_path": None,
    "label_classes": ["car", "truck", "person", "bicycle", "sign"],
    "gallery_columns": 4,
    "gallery_page_size": 24,
    "gallery_page": 0,
    "labeling_index": 0,
    "labeling_mode": "Classification",
    "confidence_threshold": 0.0,
    "selected_labels": [],
    "selected_tags": [],
}

for key, val in _DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = val

# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------
ON_DATABRICKS = bool(os.environ.get("DATABRICKS_HOST"))

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("---")
    if ON_DATABRICKS:
        st.success("Connected to Databricks", icon="✅")
    else:
        st.info("Running locally", icon="ℹ️")
    st.markdown("---")
    st.image(
        "https://upload.wikimedia.org/wikipedia/commons/6/63/Databricks_Logo.png",
        width=120,
    )
    st.caption("CV Dataset Explorer · Built on Databricks")

# ---------------------------------------------------------------------------
# Main content
# ---------------------------------------------------------------------------
st.title("🔬 CV Dataset Explorer")
st.markdown(
    "Welcome! This app lets you **browse image datasets stored in Unity Catalog "
    "Volumes**, explore them with filters and bounding-box overlays, label images, "
    "and track annotation progress — all without writing a single line of code."
)

st.markdown("---")

# Quick-start cards
col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("### 📁 Step 1: Browse")
    st.markdown(
        "Go to **Browse Volumes** to pick a catalog, schema, and volume. "
        "Navigate folders and preview images."
    )
    if st.button("Open Browse Volumes", key="home_browse"):
        st.switch_page("pages/1_📁_Browse_Volumes.py")

with col2:
    st.markdown("### 🖼️ Step 2: Explore")
    st.markdown(
        "Once you have a dataset, head to **Dataset Explorer** to view a "
        "gallery with bounding-box overlays and filters."
    )
    if st.button("Open Dataset Explorer", key="home_explore"):
        st.switch_page("pages/2_🖼️_Dataset_Explorer.py")

with col3:
    st.markdown("### 🏷️ Step 3: Label")
    st.markdown(
        "Use the **Labeling** page to classify images, draw bounding boxes, "
        "or add tags — then export to COCO JSON."
    )
    if st.button("Open Labeling", key="home_label"):
        st.switch_page("pages/3_🏷️_Labeling.py")

st.markdown("---")

# Current dataset status
st.subheader("Current Session")

if st.session_state.current_dataset:
    st.success(
        f"Active dataset: **{st.session_state.current_dataset}**", icon="📂"
    )
else:
    st.warning(
        "No dataset loaded yet. Start by browsing a volume or creating a "
        "dataset on the Explorer page.",
        icon="⚠️",
    )

# Footer
st.markdown("---")
st.caption("Built with Streamlit + SQLAlchemy  |  Deploys as a Databricks App")
