"""
Page 3: Labeling

Annotate images with classification labels, bounding boxes, polygons, or tags.
Supports drawable canvas for bounding-box and polygon modes.
Autosave: annotations are saved automatically when drawn.
"""

# ---------------------------------------------------------------------------
# Patch streamlit-drawable-canvas to work with Streamlit >= 1.39
# (image_to_url was removed from streamlit.elements.image)
# ---------------------------------------------------------------------------
import base64 as _b64
import io as _io

import streamlit.elements.image as _st_image

if not hasattr(_st_image, "image_to_url"):
    def _image_to_url(image, width, clamp, channels, output_format, image_id, **_kw):
        """Convert a PIL Image to a base64 data URL (drop-in replacement)."""
        buf = _io.BytesIO()
        image.save(buf, format=output_format or "PNG")
        b64 = _b64.b64encode(buf.getvalue()).decode()
        mime = f"image/{(output_format or 'png').lower()}"
        return f"data:{mime};base64,{b64}"

    _st_image.image_to_url = _image_to_url
# ---------------------------------------------------------------------------

import streamlit as st

from utils.config import QUICK_TAGS
from utils.datasets import list_datasets, load_dataset
from utils.drawing import load_image, draw_detections, draw_polygons, image_to_bytes
from utils.labeling import (
    init_labeling_state,
    current_sample,
    go_next,
    go_prev,
    go_skip,
    go_flag,
    save_classification,
    save_detections,
    save_polygons,
    save_tags,
    labeling_progress,
)

st.set_page_config(page_title="Labeling", page_icon="🏷️", layout="wide")

st.title("🏷️ Labeling")
st.caption("Annotate images one at a time: classify, draw boxes/polygons, or tag.")

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

init_labeling_state(dataset)

# -------------------------------------------------------------------------
# Progress bar
# -------------------------------------------------------------------------
labeled, total = labeling_progress(dataset)
st.progress(labeled / max(total, 1), text=f"{labeled} of {total} labeled")

# -------------------------------------------------------------------------
# Sidebar: mode selector + class manager + autosave toggle
# -------------------------------------------------------------------------
ALL_MODES = ["Classification", "Bounding Box", "Polygon", "Tagging"]

with st.sidebar:
    st.subheader("Labeling Mode")
    mode = st.radio(
        "Mode",
        ALL_MODES,
        index=ALL_MODES.index(
            st.session_state.get("labeling_mode", "Classification")
        ),
        help="Choose how to annotate each image.",
    )
    st.session_state.labeling_mode = mode

    st.markdown("---")

    # Autosave toggle
    autosave = st.toggle(
        "Autosave",
        value=st.session_state.get("autosave", True),
        help="Automatically save annotations when drawn. Disable to review before saving.",
    )
    st.session_state.autosave = autosave

    st.markdown("---")
    st.subheader("Class Manager")

    # Display current classes
    classes = st.session_state.label_classes
    st.write(f"**{len(classes)} classes:** {', '.join(classes)}")

    # Add class
    new_class = st.text_input("Add new class", key="new_class_input")
    if st.button("Add Class") and new_class:
        if new_class not in classes:
            classes.append(new_class)
            st.session_state.label_classes = classes
            st.rerun()
        else:
            st.warning("Class already exists.")

    # Remove class
    if classes:
        remove_class = st.selectbox("Remove class", options=classes, key="remove_cls")
        if st.button("Remove Selected"):
            classes.remove(remove_class)
            st.session_state.label_classes = classes
            st.rerun()

# -------------------------------------------------------------------------
# Current sample
# -------------------------------------------------------------------------
sample = current_sample(dataset)
if sample is None:
    st.warning("No samples in this dataset.", icon="⚠️")
    st.stop()

idx = st.session_state.labeling_index
st.markdown(f"**Sample {idx + 1} / {total}** — `{sample.filename}`")

# -------------------------------------------------------------------------
# Image display — consistent sizing across all modes
# -------------------------------------------------------------------------
img = load_image(sample.filepath)
if img is None:
    st.error(f"Cannot load image: {sample.filepath}")
    st.stop()

# Calculate display dimensions — scale to fit within max bounds while
# keeping the aspect ratio, and use the SAME size for all modes.
MAX_DISPLAY_W = 900
MAX_DISPLAY_H = 700
scale = min(MAX_DISPLAY_W / img.width, MAX_DISPLAY_H / img.height, 1.0)
display_w = int(img.width * scale)
display_h = int(img.height * scale)

img_col, ctrl_col = st.columns([3, 1])

with img_col:
    # Show existing annotations on the display image
    display_img = img.copy()
    det_anns = [a for a in sample.annotations if a.ann_type == "detection"]
    seg_anns = [a for a in sample.annotations if a.ann_type == "segmentation"]
    if det_anns:
        display_img = draw_detections(display_img, det_anns, classes)
    if seg_anns:
        display_img = draw_polygons(display_img, seg_anns, classes)

    # --- Bounding Box mode ---
    if mode == "Bounding Box":
        try:
            from streamlit_drawable_canvas import st_canvas
            from PIL import Image as PILImage

            bg_img = display_img.resize(
                (display_w, display_h), PILImage.Resampling.LANCZOS
            )

            # Reset counter — changing the key forces a fresh canvas
            reset_key = st.session_state.get("bbox_reset", 0)

            # Build initial_drawing from previous canvas state (for undo)
            init_drawing = st.session_state.get("bbox_canvas_state", None)

            canvas_result = st_canvas(
                fill_color="rgba(235, 22, 0, 0.12)",
                stroke_width=2,
                stroke_color="#EB1600",
                background_image=bg_img,
                drawing_mode="rect",
                initial_drawing=init_drawing,
                display_toolbar=True,
                height=display_h,
                width=display_w,
                key=f"canvas_rect_{idx}_{reset_key}",
            )

            # Undo / Clear
            undo_col, clear_col, _ = st.columns([1, 1, 3])
            with undo_col:
                if st.button("↩ Undo", key="undo_box"):
                    # Remove last object from the canvas JSON state
                    if canvas_result.json_data is not None:
                        state = canvas_result.json_data.copy()
                        objs = state.get("objects", [])
                        if objs:
                            state["objects"] = objs[:-1]
                            st.session_state.bbox_canvas_state = state
                            st.session_state.bbox_reset = reset_key + 1
                            st.session_state.pending_boxes = []
                            st.rerun()
            with clear_col:
                if st.button("🗑 Clear", key="clear_boxes"):
                    st.session_state.pending_boxes = []
                    st.session_state.bbox_canvas_state = None
                    st.session_state.bbox_reset = reset_key + 1
                    st.rerun()

            # Extract drawn rectangles
            if canvas_result.json_data is not None:
                objects = canvas_result.json_data.get("objects", [])
                new_boxes = []
                for obj in objects:
                    if obj["type"] == "rect":
                        new_boxes.append(
                            {
                                "x": obj["left"] / scale,
                                "y": obj["top"] / scale,
                                "width": (obj["width"] * obj.get("scaleX", 1)) / scale,
                                "height": (obj["height"] * obj.get("scaleY", 1)) / scale,
                                "label": "unknown",
                            }
                        )
                if new_boxes:
                    st.session_state.pending_boxes = new_boxes
                    if not autosave:
                        st.info(
                            f"{len(new_boxes)} box(es) drawn. "
                            "Assign classes in the right panel, then click Save."
                        )

        except ImportError:
            st.warning(
                "Install `streamlit-drawable-canvas` for drawing. "
                "Falling back to image display."
            )
            st.image(image_to_bytes(display_img), width=display_w)

    # --- Polygon mode ---
    elif mode == "Polygon":
        try:
            from streamlit_drawable_canvas import st_canvas
            from PIL import Image as PILImage

            bg_img = display_img.resize(
                (display_w, display_h), PILImage.Resampling.LANCZOS
            )

            st.caption(
                "Click to place vertices. Double-click or click near the "
                "start point to close the polygon."
            )

            # Reset counter — changing the key forces a fresh canvas
            reset_key = st.session_state.get("poly_reset", 0)

            # Build initial_drawing from previous canvas state (for undo)
            init_drawing = st.session_state.get("poly_canvas_state", None)

            canvas_result = st_canvas(
                fill_color="rgba(235, 22, 0, 0.15)",
                stroke_width=2,
                stroke_color="#EB1600",
                background_image=bg_img,
                drawing_mode="polygon",
                initial_drawing=init_drawing,
                display_toolbar=True,
                height=display_h,
                width=display_w,
                key=f"canvas_poly_{idx}_{reset_key}",
            )

            # Undo / Clear
            undo_col, clear_col, _ = st.columns([1, 1, 3])
            with undo_col:
                if st.button("↩ Undo", key="undo_poly"):
                    if canvas_result.json_data is not None:
                        state = canvas_result.json_data.copy()
                        objs = state.get("objects", [])
                        if objs:
                            state["objects"] = objs[:-1]
                            st.session_state.poly_canvas_state = state
                            st.session_state.poly_reset = reset_key + 1
                            st.session_state.pending_polygons = []
                            st.rerun()
            with clear_col:
                if st.button("🗑 Clear", key="clear_polys"):
                    st.session_state.pending_polygons = []
                    st.session_state.poly_canvas_state = None
                    st.session_state.poly_reset = reset_key + 1
                    st.rerun()

            # Extract drawn polygons from canvas
            if canvas_result.json_data is not None:
                objects = canvas_result.json_data.get("objects", [])
                new_polys = []
                for obj in objects:
                    if obj["type"] == "path":
                        # Fabric.js paths have a 'path' key with SVG-style commands
                        # Extract points from path commands
                        points = []
                        path_cmds = obj.get("path", [])
                        ox = obj.get("left", 0)
                        oy = obj.get("top", 0)
                        sx = obj.get("scaleX", 1)
                        sy = obj.get("scaleY", 1)
                        for cmd in path_cmds:
                            if len(cmd) >= 3 and cmd[0] in ("M", "L"):
                                px = (cmd[1] * sx + ox) / scale
                                py = (cmd[2] * sy + oy) / scale
                                points.append([px, py])
                        if len(points) >= 3:
                            new_polys.append(
                                {"points": points, "label": "unknown"}
                            )
                if new_polys:
                    st.session_state.pending_polygons = new_polys
                    if not autosave:
                        st.info(
                            f"{len(new_polys)} polygon(s) drawn. "
                            "Assign classes in the right panel, then click Save."
                        )

        except ImportError:
            st.warning(
                "Install `streamlit-drawable-canvas` for polygon drawing. "
                "Falling back to image display."
            )
            st.image(image_to_bytes(display_img), width=display_w)

    # --- Classification / Tagging mode ---
    else:
        from PIL import Image as PILImage

        # Resize to same fixed dimensions as canvas modes for consistency
        sized_img = display_img.resize(
            (display_w, display_h), PILImage.Resampling.LANCZOS
        )
        st.image(image_to_bytes(sized_img), use_container_width=False)

    # Show existing tags
    if sample.tags:
        st.caption(f"Tags: {', '.join(sample.tag_list)}")

with ctrl_col:
    st.markdown("### Actions")

    # ---------- Classification mode ----------
    if mode == "Classification":
        st.markdown("**Pick a class:**")
        for cls in classes:
            if st.button(cls, key=f"cls_{cls}", use_container_width=True):
                save_classification(dataset, cls)
                st.rerun()

    # ---------- Bounding Box mode ----------
    elif mode == "Bounding Box":
        pending = st.session_state.get("pending_boxes", [])

        # Default label for autosave
        default_label = st.selectbox(
            "Default label for new boxes",
            options=classes,
            key="bbox_default_label",
        )

        if pending:
            if autosave:
                # Autosave: apply default label and save immediately
                for box in pending:
                    box["label"] = default_label
                save_detections(dataset, pending, advance=False)
                st.session_state.pending_boxes = []
                st.success(f"Auto-saved {len(pending)} box(es) as '{default_label}'.")
                st.rerun()
            else:
                # Manual save: let user assign classes per box
                st.markdown(f"**{len(pending)} box(es) — assign classes:**")
                updated_boxes = []
                for i, box in enumerate(pending):
                    label = st.selectbox(
                        f"Box {i + 1}",
                        options=classes,
                        key=f"box_label_{i}",
                    )
                    box["label"] = label
                    updated_boxes.append(box)

                if st.button("Save Detections", type="primary", use_container_width=True):
                    save_detections(dataset, updated_boxes)
                    st.rerun()
        else:
            st.info("Draw rectangles on the image.")

    # ---------- Polygon mode ----------
    elif mode == "Polygon":
        pending = st.session_state.get("pending_polygons", [])

        # Default label for autosave
        default_label = st.selectbox(
            "Default label for new polygons",
            options=classes,
            key="poly_default_label",
        )

        if pending:
            if autosave:
                # Autosave: apply default label and save immediately
                for poly in pending:
                    poly["label"] = default_label
                save_polygons(dataset, pending, advance=False)
                st.session_state.pending_polygons = []
                st.success(f"Auto-saved {len(pending)} polygon(s) as '{default_label}'.")
                st.rerun()
            else:
                # Manual save: let user assign classes per polygon
                st.markdown(f"**{len(pending)} polygon(s) — assign classes:**")
                updated_polys = []
                for i, poly in enumerate(pending):
                    label = st.selectbox(
                        f"Polygon {i + 1} ({len(poly['points'])} pts)",
                        options=classes,
                        key=f"poly_label_{i}",
                    )
                    poly["label"] = label
                    updated_polys.append(poly)

                if st.button("Save Polygons", type="primary", use_container_width=True):
                    save_polygons(dataset, updated_polys)
                    st.rerun()
        else:
            st.info("Draw polygons on the image by clicking vertices.")

    # ---------- Tagging mode ----------
    elif mode == "Tagging":
        st.markdown("**Quick tags:**")
        tag_cols = st.columns(2)
        for i, tag in enumerate(QUICK_TAGS):
            with tag_cols[i % 2]:
                if st.button(tag, key=f"qtag_{tag}", use_container_width=True):
                    save_tags(dataset, [tag])
                    st.rerun()

        custom_tag = st.text_input("Custom tag", key="custom_tag_input")
        if st.button("Add Tag") and custom_tag:
            save_tags(dataset, [custom_tag])
            st.rerun()

    st.markdown("---")

    # Autosave indicator
    if autosave:
        st.caption("🟢 Autosave ON — annotations saved on draw")
    else:
        st.caption("🔴 Autosave OFF — click Save to persist")

    st.markdown("---")

    # Navigation buttons
    st.markdown("### Navigation")
    nav1, nav2 = st.columns(2)
    with nav1:
        if st.button("◀ Prev", use_container_width=True, disabled=idx == 0):
            go_prev()
            st.rerun()
    with nav2:
        if st.button("Next ▶", use_container_width=True, disabled=idx >= total - 1):
            go_next(dataset)
            st.rerun()

    skip_col, flag_col = st.columns(2)
    with skip_col:
        if st.button("⏭ Skip", use_container_width=True):
            go_skip(dataset)
            st.rerun()
    with flag_col:
        if st.button("🚩 Flag", use_container_width=True):
            go_flag(dataset)
            st.rerun()
