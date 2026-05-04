"""
Pydantic schemas for the CV Explorer API.
"""

from datetime import datetime
from typing import Literal, Optional
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
    serving_endpoint: Optional[str] = None
    endpoint_config: Optional[dict] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    source_volume: Optional[str] = None
    class_list: Optional[list[str]] = None
    confirm_source_change: bool = False
    serving_endpoint: Optional[str] = None
    endpoint_config: Optional[dict] = None


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str
    task_type: str
    class_list: list[str]
    source_volume: str
    serving_endpoint: Optional[str] = None
    endpoint_config: Optional[dict] = None
    created_by: str
    created_at: datetime
    sample_count: int = 0
    labeled_count: int = 0
    version: int = 1
    parent_project_id: Optional[int] = None

    model_config = {"from_attributes": True}


class ProjectStats(BaseModel):
    total: int
    labeled: int
    unlabeled: int
    skipped: int
    pre_labeled: int = 0
    embedded: int = 0
    per_user: list[dict]  # [{"user": "...", "labeled": N, "skipped": N}]


class ClassCount(BaseModel):
    label: str
    count: int


class DailyVelocity(BaseModel):
    date: str
    count: int


class DetailedProjectStats(BaseModel):
    total: int
    labeled: int
    unlabeled: int
    skipped: int
    per_class: list[ClassCount]
    daily_velocity: list[DailyVelocity]
    per_user: list[dict]
    avg_daily_rate: float
    estimated_completion_date: Optional[str]


# ---------------------------------------------------------------------------
# Pre-annotation / Inference
# ---------------------------------------------------------------------------
class PredictionOut(BaseModel):
    label: str
    ann_type: str
    bbox_json: Optional[dict] = None
    confidence: Optional[float] = None


class PreAnnotateRequest(BaseModel):
    max_samples: int = 0  # 0 = all matching samples
    min_confidence: Optional[float] = None
    include_pre_labeled: bool = False  # also re-run on pre_labeled (replaces model drafts)
    text_prompt: Optional[str] = None  # override SAM text prompt (default: class list)


class PreAnnotateProgress(BaseModel):
    completed: int
    failed: int
    skipped: int
    total: int


class PreAnnotateAsyncRequest(BaseModel):
    """Same options as synchronous pre-annotate, executed by a Databricks Job."""

    max_samples: int = 0
    min_confidence: Optional[float] = None
    include_pre_labeled: bool = False
    text_prompt: Optional[str] = None


class PreannotateRunOut(BaseModel):
    id: int
    project_id: int
    status: str
    max_samples: int
    include_pre_labeled: bool
    min_confidence: Optional[float] = None
    text_prompt: Optional[str] = None
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    total_planned: int = 0
    databricks_run_id: Optional[int] = None
    error_message: Optional[str] = None
    created_by: str = ""
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class EndpointStatus(BaseModel):
    status: str  # ready, not_ready, not_found, error, not_configured
    endpoint: Optional[str] = None
    state: Optional[str] = None
    error: Optional[str] = None


class InferenceDefaultsOut(BaseModel):
    """Workspace-level inference defaults (from env), for forms before a project exists."""

    default_serving_endpoint: Optional[str] = None


class EmbeddingGenerateRequest(BaseModel):
    max_samples: int = 0
    force: bool = False


class EmbeddingGenerateProgress(BaseModel):
    completed: int
    failed: int
    skipped: int
    total: int


class EmbeddingRunOut(BaseModel):
    id: int
    project_id: int
    status: str
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    total_planned: int = 0
    force: bool = False
    error_message: Optional[str] = None
    created_by: str = ""
    created_at: datetime
    finished_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


class SimilarSampleOut(BaseModel):
    sample_id: int
    filename: str
    similarity: float
    status: str


class BulkDraftSampleIds(BaseModel):
    sample_ids: list[int]


class DraftMutationResult(BaseModel):
    annotations_affected: int = 0
    samples_touched: int = 0


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
    labels: list[str] = []

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


class AnnotationBatchCreate(BaseModel):
    annotations: list[AnnotationCreate]


class AnnotationOut(BaseModel):
    id: int
    sample_id: int
    project_id: int
    label: str
    ann_type: str
    bbox_json: Optional[dict] = None
    is_draft: bool = False
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Annotation History
# ---------------------------------------------------------------------------
class AnnotationHistoryOut(BaseModel):
    id: int
    sample_id: int
    project_id: int
    action: str
    old_label: Optional[str] = None
    new_label: Optional[str] = None
    old_ann_type: Optional[str] = None
    new_ann_type: Optional[str] = None
    old_bbox_json: Optional[dict] = None
    new_bbox_json: Optional[dict] = None
    changed_by: str
    changed_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Finetuning
# ---------------------------------------------------------------------------
class FinetuneRunOut(BaseModel):
    id: int
    project_id: int
    status: str
    export_path: str
    databricks_run_id: Optional[int] = None
    error_message: Optional[str] = None
    created_by: str = ""
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
class ImportRequest(BaseModel):
    volume_path: str
    format: Literal["coco", "jsonl"]
    on_missing_sample: Literal["error", "skip", "create"] = "error"
    on_existing_annotations: Literal["replace", "append", "skip"] = "replace"
    dry_run: bool = False


class ImportErrorItem(BaseModel):
    row: Optional[int] = None
    filename: Optional[str] = None
    reason: str


class ImportResponse(BaseModel):
    dry_run: bool
    samples_touched: int = 0
    annotations_created: int = 0
    annotations_replaced: int = 0
    samples_skipped: int = 0
    samples_created: int = 0
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Label Propagation
# ---------------------------------------------------------------------------
class LabelPropagateRequest(BaseModel):
    """Propagate labels from labeled samples to similar unlabeled ones."""

    similarity_threshold: float = 0.85
    max_targets: int = 0  # 0 = all eligible
    source_statuses: list[str] = ["labeled"]  # which statuses to copy from


class LabelPropagateResult(BaseModel):
    propagated: int = 0
    skipped: int = 0
    total_candidates: int = 0


# ---------------------------------------------------------------------------
# Near-Duplicate Detection
# ---------------------------------------------------------------------------
class NearDuplicateGroup(BaseModel):
    representative_id: int
    representative_filename: str
    members: list[SimilarSampleOut]


class NearDuplicateResult(BaseModel):
    groups: list[NearDuplicateGroup]
    total_duplicates: int = 0
    threshold: float


# ---------------------------------------------------------------------------
# Diversity Sampling (Smart Queue)
# ---------------------------------------------------------------------------
class DiversitySampleOut(BaseModel):
    sample_id: int
    filename: str
    diversity_score: float
    status: str


class DiversitySamplingResult(BaseModel):
    items: list[DiversitySampleOut]
    total: int


# ---------------------------------------------------------------------------
# Active Learning Queue
# ---------------------------------------------------------------------------
class ActiveLearningSampleOut(BaseModel):
    sample_id: int
    filename: str
    uncertainty_score: float
    diversity_score: float
    combined_score: float
    status: str


class ActiveLearningQueueResult(BaseModel):
    items: list[ActiveLearningSampleOut]
    total: int
    alpha: float
    beta: float
    has_predictions: bool


# ---------------------------------------------------------------------------
# Outlier Detection
# ---------------------------------------------------------------------------
class OutlierSampleOut(BaseModel):
    sample_id: int
    filename: str
    outlier_score: float
    is_outlier: bool
    status: str


class OutlierDetectionResult(BaseModel):
    items: list[OutlierSampleOut]
    total: int
    threshold: float
    outlier_count: int


# ---------------------------------------------------------------------------
# Cluster Map (2D Embedding Projection)
# ---------------------------------------------------------------------------
class ClusterMapPoint(BaseModel):
    sample_id: int
    filename: str
    x: float
    y: float
    status: str
    labels: list[str] = []


class ClusterMapResult(BaseModel):
    points: list[ClusterMapPoint]
    total: int
    cached: bool = False
