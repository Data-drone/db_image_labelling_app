"""
Shared configuration, constants, and path helpers.
"""

import os

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
DATABRICKS_HOST = os.environ.get("DATABRICKS_HOST", "")
ON_DATABRICKS = bool(DATABRICKS_HOST)

# ---------------------------------------------------------------------------
# Supported file types
# ---------------------------------------------------------------------------
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

# ---------------------------------------------------------------------------
# FiftyOne defaults
# ---------------------------------------------------------------------------
FIFTYONE_DB_DIR = os.environ.get("FIFTYONE_DATABASE_DIR", "/tmp/fiftyone_db")
FIFTYONE_DATA_DIR = os.environ.get("FIFTYONE_DEFAULT_DATASET_DIR", "/tmp/fiftyone_data")

# ---------------------------------------------------------------------------
# Gallery defaults
# ---------------------------------------------------------------------------
DEFAULT_COLUMNS = 4
DEFAULT_PAGE_SIZE = 24
COLUMN_OPTIONS = [3, 4, 5, 6]

# ---------------------------------------------------------------------------
# Labeling
# ---------------------------------------------------------------------------
DEFAULT_CLASSES = ["car", "truck", "person", "bicycle", "sign"]
QUICK_TAGS = ["good", "bad", "review", "skip", "flagged"]

# ---------------------------------------------------------------------------
# COCO export
# ---------------------------------------------------------------------------
COCO_EXPORT_PATH = "/tmp/coco_export"


def volume_path(catalog: str, schema: str, volume: str) -> str:
    """Return the DBFS / Volumes path for a UC Volume."""
    return f"/Volumes/{catalog}/{schema}/{volume}"


def is_image(filename: str) -> bool:
    """Check if a filename has a recognised image extension."""
    return os.path.splitext(filename.lower())[1] in IMAGE_EXTENSIONS


def is_media(filename: str) -> bool:
    """Check if a filename has a recognised media extension."""
    return os.path.splitext(filename.lower())[1] in MEDIA_EXTENSIONS
