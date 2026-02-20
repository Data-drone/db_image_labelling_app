"""
Page 1: Browse Unity Catalog Volumes

Pick Catalog -> Schema -> Volume, navigate folders, preview images,
and create FiftyOne datasets from image directories.
"""

import os

import streamlit as st

from utils.config import ON_DATABRICKS, volume_path, is_image
from utils.volumes import (
    list_catalogs,
    list_schemas,
    list_volumes,
    list_directory,
    count_images_in,
    get_local_folders,
)
from utils.datasets import create_dataset_from_directory
from utils.drawing import load_thumbnail, image_to_bytes

st.set_page_config(page_title="Browse Volumes", page_icon="📁", layout="wide")

st.title("📁 Browse Volumes")
st.caption("Navigate Unity Catalog Volumes to find image folders.")

# -------------------------------------------------------------------------
# Databricks mode: Catalog -> Schema -> Volume pickers
# -------------------------------------------------------------------------
if ON_DATABRICKS:
    col1, col2, col3 = st.columns(3)

    with col1:
        catalogs = list_catalogs()
        catalog = st.selectbox(
            "Catalog",
            options=catalogs,
            index=0 if catalogs else None,
            help="Select a Unity Catalog.",
        )

    with col2:
        schemas = list_schemas(catalog) if catalog else []
        schema = st.selectbox(
            "Schema",
            options=schemas,
            index=0 if schemas else None,
            help="Select a schema inside the catalog.",
        )

    with col3:
        volumes = list_volumes(catalog, schema) if catalog and schema else []
        vol = st.selectbox(
            "Volume",
            options=volumes,
            index=0 if volumes else None,
            help="Select a volume to browse.",
        )

    if catalog and schema and vol:
        base_path = volume_path(catalog, schema, vol)
    else:
        base_path = None

else:
    # Local fallback
    st.info(
        "Not connected to Databricks. Enter a local folder path to browse.",
        icon="ℹ️",
    )
    base_path = st.text_input(
        "Local folder path",
        value=os.path.expanduser("~"),
        help="Full path to a folder containing images.",
    )

# -------------------------------------------------------------------------
# Breadcrumb navigation
# -------------------------------------------------------------------------
if base_path and os.path.isdir(base_path):
    # Track sub-path in session state
    if "browse_subpath" not in st.session_state:
        st.session_state.browse_subpath = ""

    current_path = os.path.join(base_path, st.session_state.browse_subpath)

    # Breadcrumb
    parts = st.session_state.browse_subpath.split("/") if st.session_state.browse_subpath else []
    crumbs = ["Root"] + parts
    breadcrumb_cols = st.columns(len(crumbs) + 1)
    for i, crumb in enumerate(crumbs):
        with breadcrumb_cols[i]:
            if st.button(f"📂 {crumb}", key=f"crumb_{i}"):
                st.session_state.browse_subpath = "/".join(parts[:i])
                st.rerun()

    st.markdown("---")

    # List contents
    folders, files = list_directory(current_path)

    # Folder cards
    if folders:
        st.subheader("Folders")
        folder_cols = st.columns(min(len(folders), 6))
        for i, folder in enumerate(folders):
            with folder_cols[i % len(folder_cols)]:
                img_count = count_images_in(os.path.join(current_path, folder))
                if st.button(
                    f"📂 {folder}\n({img_count} images)",
                    key=f"folder_{folder}",
                    use_container_width=True,
                ):
                    sub = st.session_state.browse_subpath
                    st.session_state.browse_subpath = (
                        f"{sub}/{folder}" if sub else folder
                    )
                    st.rerun()

    # Image previews
    if files:
        st.subheader(f"Files ({len(files)})")

        # File type filter
        exts = sorted({os.path.splitext(f)[1].lower() for f in files})
        selected_exts = st.multiselect(
            "Filter by extension",
            options=exts,
            default=exts,
            help="Show only files with these extensions.",
        )
        filtered_files = [
            f for f in files if os.path.splitext(f)[1].lower() in selected_exts
        ]

        # Thumbnail grid
        n_cols = st.slider("Columns", 3, 8, 5, key="browse_cols")
        img_cols = st.columns(n_cols)
        for i, fname in enumerate(filtered_files):
            fpath = os.path.join(current_path, fname)
            with img_cols[i % n_cols]:
                thumb = load_thumbnail(fpath, size=(250, 250))
                if thumb:
                    st.image(
                        image_to_bytes(thumb),
                        caption=fname,
                        use_container_width=True,
                    )
                else:
                    st.text(fname)

        # Create dataset button
        st.markdown("---")
        st.subheader("Create Dataset from This Folder")
        dataset_name = st.text_input(
            "Dataset name",
            value=os.path.basename(current_path),
            help="Name for the new FiftyOne dataset.",
        )
        if st.button("Create Dataset", type="primary"):
            if dataset_name:
                success = create_dataset_from_directory(dataset_name, current_path)
                if success:
                    st.session_state.current_dataset = dataset_name
                    st.balloons()
            else:
                st.warning("Please enter a dataset name.")

    if not folders and not files:
        st.info("This folder is empty.", icon="📭")

elif base_path:
    st.warning(f"Path does not exist or is not a directory: `{base_path}`")
else:
    st.info("Select a catalog, schema, and volume above to start browsing.", icon="👆")
