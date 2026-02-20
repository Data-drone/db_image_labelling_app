"""
Image loading, thumbnail generation, and bounding-box overlay drawing.
Works with SQLAlchemy Annotation objects (replacing FiftyOne Detection).
"""

import io
import json
import os
from typing import Optional

import streamlit as st

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore


# ---------------------------------------------------------------------------
# Colour palette for class labels
# ---------------------------------------------------------------------------
_PALETTE = [
    (235, 22, 0),     # Databricks red
    (0, 200, 0),
    (0, 100, 255),
    (255, 165, 0),
    (148, 0, 211),
    (0, 206, 209),
    (255, 20, 147),
    (128, 128, 0),
    (0, 128, 128),
    (220, 20, 60),
]


def class_color(label: str, classes: list[str]) -> tuple[int, int, int]:
    """Deterministic colour for a class label."""
    idx = classes.index(label) if label in classes else hash(label)
    return _PALETTE[idx % len(_PALETTE)]


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------

def load_image(filepath: str) -> Optional["Image.Image"]:
    """Load an image from disk and return a PIL Image, or None."""
    if Image is None:
        st.error("Pillow is not installed.")
        return None
    if not os.path.exists(filepath):
        return None
    try:
        return Image.open(filepath).convert("RGB")
    except Exception as exc:
        st.error(f"Cannot open image: {exc}")
        return None


def load_thumbnail(filepath: str, size: tuple[int, int] = (300, 300)) -> Optional["Image.Image"]:
    """Load and resize an image for gallery display."""
    img = load_image(filepath)
    if img is None:
        return None
    img.thumbnail(size, Image.Resampling.LANCZOS)
    return img


# ---------------------------------------------------------------------------
# Bounding-box overlay
# ---------------------------------------------------------------------------

def draw_detections(
    img: "Image.Image",
    detections: list,
    classes: Optional[list[str]] = None,
    line_width: int = 3,
    font_size: int = 14,
) -> "Image.Image":
    """
    Draw bounding boxes + labels on a copy of *img*.

    *detections* can be either:
    - SQLAlchemy Annotation objects with .label and .bbox_json
    - Any object with .label and .bounding_box (list [x, y, w, h])
    """
    if Image is None:
        return img

    result = img.copy()
    draw = ImageDraw.Draw(result)
    w, h = result.size
    all_classes = classes or []

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size
        )
    except Exception:
        font = ImageFont.load_default()

    for det in detections:
        # Handle both SQLAlchemy Annotation and FiftyOne Detection objects
        label = det.label if hasattr(det, "label") else "unknown"

        # Get bounding box
        if hasattr(det, "bbox_json") and det.bbox_json:
            # SQLAlchemy Annotation
            bbox_data = json.loads(det.bbox_json)
            bx, by, bw, bh = bbox_data["x"], bbox_data["y"], bbox_data["w"], bbox_data["h"]
        elif hasattr(det, "bounding_box") and det.bounding_box:
            # FiftyOne-style [x, y, w, h]
            bx, by, bw, bh = det.bounding_box
        else:
            continue  # Skip annotations without bounding boxes

        x1 = int(bx * w)
        y1 = int(by * h)
        x2 = int((bx + bw) * w)
        y2 = int((by + bh) * h)

        color = class_color(label, all_classes)
        draw.rectangle([x1, y1, x2, y2], outline=color, width=line_width)

        # Label background
        label_text = label
        confidence = getattr(det, "confidence", None)
        if confidence is not None:
            label_text += f" {confidence:.0%}"
        bbox = font.getbbox(label_text)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        draw.rectangle(
            [x1, y1 - text_h - 4, x1 + text_w + 6, y1],
            fill=color,
        )
        draw.text((x1 + 3, y1 - text_h - 2), label_text, fill="white", font=font)

    return result


# ---------------------------------------------------------------------------
# Polygon overlay
# ---------------------------------------------------------------------------

def draw_polygons(
    img: "Image.Image",
    segmentations: list,
    classes: Optional[list[str]] = None,
    line_width: int = 2,
    font_size: int = 14,
    fill_alpha: int = 50,
) -> "Image.Image":
    """
    Draw filled polygons + labels on a copy of *img*.

    *segmentations* should be SQLAlchemy Annotation objects with
    .label and .polygon_json (list of normalised [x, y] pairs).
    """
    if Image is None:
        return img

    result = img.copy().convert("RGBA")
    overlay = Image.new("RGBA", result.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    draw_outline = ImageDraw.Draw(result)
    w, h = result.size
    all_classes = classes or []

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size
        )
    except Exception:
        font = ImageFont.load_default()

    for seg in segmentations:
        label = seg.label if hasattr(seg, "label") else "unknown"

        # Get polygon points
        points_norm = None
        if hasattr(seg, "polygon_json") and seg.polygon_json:
            import json as _json
            points_norm = _json.loads(seg.polygon_json)
        elif hasattr(seg, "polygon_points") and seg.polygon_points:
            points_norm = seg.polygon_points

        if not points_norm or len(points_norm) < 3:
            continue

        # Convert normalised coords to pixel coords
        pixel_points = [(p[0] * w, p[1] * h) for p in points_norm]

        color = class_color(label, all_classes)
        fill_color = (*color, fill_alpha)

        # Draw filled polygon on overlay
        draw_overlay.polygon(pixel_points, fill=fill_color, outline=(*color, 255))

        # Draw outline on the main image
        draw_outline.polygon(pixel_points, outline=color, width=line_width)

        # Label at centroid
        cx = sum(p[0] for p in pixel_points) / len(pixel_points)
        cy = sum(p[1] for p in pixel_points) / len(pixel_points)

        label_text = label
        confidence = getattr(seg, "confidence", None)
        if confidence is not None:
            label_text += f" {confidence:.0%}"
        bbox = font.getbbox(label_text)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        tx = int(cx - text_w / 2)
        ty = int(cy - text_h / 2)
        draw_overlay.rectangle(
            [tx - 2, ty - 2, tx + text_w + 4, ty + text_h + 4],
            fill=(*color, 180),
        )
        draw_overlay.text((tx, ty), label_text, fill=(255, 255, 255, 255), font=font)

    result = Image.alpha_composite(result, overlay).convert("RGB")
    return result


# ---------------------------------------------------------------------------
# PIL Image -> bytes (for st.image / st.download_button)
# ---------------------------------------------------------------------------

def image_to_bytes(img: "Image.Image", fmt: str = "PNG") -> bytes:
    """Convert a PIL Image to bytes."""
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()
