"""
Pydantic schemas for API request/response models.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------
class DatasetCreate(BaseModel):
    name: str
    description: str = ""
    image_dir: str = ""


class DatasetOut(BaseModel):
    id: int
    name: str
    description: str
    image_dir: str
    created_at: datetime
    sample_count: int = 0

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Sample
# ---------------------------------------------------------------------------
class SampleOut(BaseModel):
    id: int
    dataset_id: int
    filepath: str
    filename: str
    metadata_json: str = "{}"
    created_at: datetime
    annotations: list["AnnotationOut"] = []
    tags: list["TagOut"] = []

    model_config = {"from_attributes": True}


class SamplePage(BaseModel):
    items: list[SampleOut]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Annotation
# ---------------------------------------------------------------------------
class AnnotationCreate(BaseModel):
    sample_id: int
    ann_type: str  # "classification", "detection", "segmentation"
    label: str
    bbox_json: Optional[str] = None
    polygon_json: Optional[str] = None
    confidence: Optional[float] = None


class AnnotationOut(BaseModel):
    id: int
    sample_id: int
    ann_type: str
    label: str
    bbox_json: Optional[str] = None
    polygon_json: Optional[str] = None
    confidence: Optional[float] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Tag
# ---------------------------------------------------------------------------
class TagCreate(BaseModel):
    sample_id: int
    tag: str


class TagOut(BaseModel):
    id: int
    sample_id: int
    tag: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
class DatasetStats(BaseModel):
    total_samples: int
    labeled_count: int
    unlabeled_count: int
    class_count: int
    classes: list[str]
    tags: list[str]
    class_distribution: dict[str, int]
    tag_distribution: dict[str, int]
