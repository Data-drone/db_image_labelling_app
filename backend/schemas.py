"""
Pydantic schemas for the CV Explorer API.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------
class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    task_type: str  # 'classification' or 'detection'
    class_list: list[str]
    source_volume: str  # UC Volume path


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str
    task_type: str
    class_list: list[str]
    source_volume: str
    created_by: str
    created_at: datetime
    sample_count: int = 0
    labeled_count: int = 0

    model_config = {"from_attributes": True}


class ProjectStats(BaseModel):
    total: int
    labeled: int
    unlabeled: int
    skipped: int
    per_user: list[dict]  # [{"user": "...", "labeled": N, "skipped": N}]


# ---------------------------------------------------------------------------
# Sample
# ---------------------------------------------------------------------------
class SampleOut(BaseModel):
    id: int
    project_id: int
    filepath: str
    filename: str
    status: str
    locked_by: Optional[str] = None
    locked_at: Optional[datetime] = None
    annotations: list["AnnotationOut"] = []

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
    label: str
    ann_type: str  # 'classification' or 'bbox'
    bbox_json: Optional[dict] = None  # {"x":..,"y":..,"w":..,"h":..}


class AnnotationOut(BaseModel):
    id: int
    sample_id: int
    project_id: int
    label: str
    ann_type: str
    bbox_json: Optional[dict] = None
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}
