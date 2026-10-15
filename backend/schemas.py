"""
Pydantic schemas for the CV Explorer API.
"""

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

from backend import masks


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------
class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    task_type: str  # 'classification', 'detection' or 'segmentation'
    class_list: list[str]
    source_volume: str = ""  # required when source_type='volume'
    source_type: str = "volume"  # 'volume' or 'table'
    source_table: Optional[str] = None
    image_path_column: Optional[str] = None
    source_filter: Optional[str] = None
    serving_endpoint: Optional[str] = None
    endpoint_config: Optional[dict] = None
    consensus_enabled: bool = False
    consensus_panel_size: int = 3
    consensus_coverage_percent: int = 100
    consensus_quorum: int = 2
    consensus_iou_threshold: float = 0.5
    consensus_agreement_threshold: float = 0.5
    consensus_reviewer_emails: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_source(self):
        st = (self.source_type or "volume").strip().lower()
        if st not in ("volume", "table"):
            raise ValueError("source_type must be 'volume' or 'table'")
        self.source_type = st
        if st == "volume":
            if not (self.source_volume or "").strip():
                raise ValueError("source_volume is required when source_type is 'volume'")
        else:
            if not (self.source_table or "").strip():
                raise ValueError("source_table is required when source_type is 'table'")
            col = (self.image_path_column or "image_path").strip()
            self.image_path_column = col or "image_path"
        if not 1 <= self.consensus_panel_size <= 15:
            raise ValueError("consensus_panel_size must be between 1 and 15")
        if not 0 <= self.consensus_coverage_percent <= 100:
            raise ValueError("consensus_coverage_percent must be between 0 and 100")
        if not 1 <= self.consensus_quorum <= self.consensus_panel_size:
            raise ValueError("consensus_quorum must be between 1 and consensus_panel_size")
        if not 0 <= self.consensus_iou_threshold <= 1:
            raise ValueError("consensus_iou_threshold must be between 0 and 1")
        if not 0 <= self.consensus_agreement_threshold <= 1:
            raise ValueError("consensus_agreement_threshold must be between 0 and 1")
        self.consensus_reviewer_emails = sorted({
            email.strip().lower()
            for email in self.consensus_reviewer_emails
            if email and email.strip()
        })
        return self


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    source_volume: Optional[str] = None
    source_type: Optional[str] = None
    source_table: Optional[str] = None
    image_path_column: Optional[str] = None
    source_filter: Optional[str] = None
    class_list: Optional[list[str]] = None
    confirm_source_change: bool = False
    serving_endpoint: Optional[str] = None
    endpoint_config: Optional[dict] = None
    consensus_enabled: Optional[bool] = None
    consensus_panel_size: Optional[int] = None
    consensus_coverage_percent: Optional[int] = None
    consensus_quorum: Optional[int] = None
    consensus_iou_threshold: Optional[float] = None
    consensus_agreement_threshold: Optional[float] = None
    consensus_reviewer_emails: Optional[list[str]] = None
    reset_consensus_unstarted: bool = False

    @model_validator(mode="after")
    def _source_type_ok(self):
        if self.source_type is None:
            return self
        st = self.source_type.strip().lower()
        if st not in ("volume", "table"):
            raise ValueError("source_type must be 'volume' or 'table'")
        self.source_type = st
        return self

    @model_validator(mode="after")
    def _consensus_values_ok(self):
        if self.consensus_panel_size is not None and not 1 <= self.consensus_panel_size <= 15:
            raise ValueError("consensus_panel_size must be between 1 and 15")
        if (
            self.consensus_coverage_percent is not None
            and not 0 <= self.consensus_coverage_percent <= 100
        ):
            raise ValueError("consensus_coverage_percent must be between 0 and 100")
        if self.consensus_quorum is not None and self.consensus_quorum < 1:
            raise ValueError("consensus_quorum must be at least 1")
        if (
            self.consensus_iou_threshold is not None
            and not 0 <= self.consensus_iou_threshold <= 1
        ):
            raise ValueError("consensus_iou_threshold must be between 0 and 1")
        if (
            self.consensus_agreement_threshold is not None
            and not 0 <= self.consensus_agreement_threshold <= 1
        ):
            raise ValueError("consensus_agreement_threshold must be between 0 and 1")
        if self.consensus_reviewer_emails is not None:
            self.consensus_reviewer_emails = sorted({
                email.strip().lower()
                for email in self.consensus_reviewer_emails
                if email and email.strip()
            })
        return self


class ProjectOut(BaseModel):
    id: int
    name: str
    description: str
    task_type: str
    class_list: list[str]
    source_volume: str
    source_type: str = "volume"
    source_table: Optional[str] = None
    image_path_column: Optional[str] = None
    source_filter: Optional[str] = None
    serving_endpoint: Optional[str] = None
    endpoint_config: Optional[dict] = None
    consensus_enabled: bool = False
    consensus_panel_size: int = 3
    consensus_coverage_percent: int = 100
    consensus_quorum: int = 2
    consensus_iou_threshold: float = 0.5
    consensus_agreement_threshold: float = 0.5
    consensus_reviewer_emails: list[str] = Field(default_factory=list)
    consensus_role: str = "labeller"
    created_by: str
    created_at: datetime
    sample_count: int = 0
    labeled_count: int = 0
    version: int = 1
    parent_project_id: Optional[int] = None

    model_config = {"from_attributes": True}


class SourceSyncOut(BaseModel):
    added: int
    skipped_existing: int = 0
    sample_count: int
    labeled_count: int = 0
    source_type: str
    source_table: Optional[str] = None
    source_volume: str = ""


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
    mask_json: Optional[dict] = None  # uncompressed RLE, see backend/masks.py
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
    width: Optional[int] = None
    height: Optional[int] = None
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
    ann_type: str  # 'classification', 'bbox' or 'mask'
    bbox_json: Optional[dict] = None  # {"x":..,"y":..,"w":..,"h":..}
    # Uncompressed COCO RLE: {"size": [h, w], "counts": [int, ...]}. Validated
    # and compressed server-side; the submitted size is checked against the
    # image's real dimensions rather than trusted.
    mask_json: Optional[dict] = None
    # annotate-batch replaces every annotation on the sample, so a client that
    # edits one annotation type has to hand the others back. Without this field
    # it cannot: every round trip silently promotes a model draft to accepted.
    # Defaults to False, which is what an ordinary human annotation is.
    is_draft: bool = False
    # Provenance, only honoured for a `model:` marker on a draft (see
    # routes.labeling._resolve_created_by). It exists because the draft-clearing
    # endpoints match on that marker, so a preserved draft that lost it would
    # show a "Clear drafts" button that does nothing.
    created_by: Optional[str] = None


class AnnotationBatchCreate(BaseModel):
    annotations: list[AnnotationCreate]
    # An empty list is a legitimate edit -- "this image has nothing on it", or
    # the user erased the last mask -- but it is also what a bug looks like, and
    # this endpoint deletes whatever it replaces. Callers say which one they
    # mean rather than having the server guess.
    allow_empty: bool = False


class AnnotationOut(BaseModel):
    id: int
    sample_id: int
    project_id: int
    label: str
    ann_type: str
    bbox_json: Optional[dict] = None
    mask_json: Optional[dict] = None
    is_draft: bool = False
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def _mask_to_wire(self):
        # The DB holds the compressed string form; clients get the
        # uncompressed counts array so no JS ever has to implement the
        # compression. Doing it here covers every route that returns an
        # annotation, present and future.
        if self.mask_json is not None:
            self.mask_json = masks.to_wire(self.mask_json)
        return self


# ---------------------------------------------------------------------------
# Blind consensus
# ---------------------------------------------------------------------------
class ConsensusTaskAnnotationOut(BaseModel):
    id: Optional[int] = None
    label: str
    ann_type: str = "classification"
    bbox_json: Optional[dict] = None
    mask_json: Optional[dict] = None
    cluster_id: Optional[int] = None
    agreement_score: Optional[float] = None
    support: Optional[int] = None

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def _mask_to_wire(self):
        if self.mask_json is not None:
            self.mask_json = masks.to_wire(self.mask_json)
        return self


class ConsensusTaskOut(BaseModel):
    id: int
    case_id: int
    sample_id: int
    status: str
    assigned_to: str
    lock_token: str
    locked_at: datetime
    sample: SampleOut
    annotations: list[ConsensusTaskAnnotationOut] = Field(default_factory=list)


class ConsensusTaskSubmit(BaseModel):
    lock_token: str
    annotations: list[AnnotationCreate]
    allow_empty: bool = False


class ConsensusLockRequest(BaseModel):
    lock_token: str


class ConsensusProgressOut(BaseModel):
    total_tasks: int = 0
    submitted_tasks: int = 0
    available_tasks: int = 0
    my_submitted_tasks: int = 0
    resolved_items: int = 0
    review_items: int = 0
    total_items: int = 0


class ConsensusSubmitOut(BaseModel):
    task_id: int
    case_id: int
    case_state: str
    submitted_labels: list[str]
    submitted_annotation_count: int = 0
    resolution: Optional["ConsensusResolutionOut"] = None


class ConsensusReviewSubmissionOut(BaseModel):
    task_id: int
    submitted_by: str
    submitted_at: datetime
    labels: list[str]
    annotations: list[ConsensusTaskAnnotationOut] = Field(default_factory=list)


class ConsensusReviewCaseOut(BaseModel):
    case_id: int
    sample_id: int
    filename: str
    state: str
    task_type: str = "classification"
    required_votes: int
    submissions: list[ConsensusReviewSubmissionOut]
    match_report: dict = Field(default_factory=dict)
    proposed_annotations: list[ConsensusTaskAnnotationOut] = Field(default_factory=list)


class ConsensusReviewPage(BaseModel):
    items: list[ConsensusReviewCaseOut]
    total: int


class ConsensusReviewAction(BaseModel):
    action: Literal["pick", "edit", "reject", "relabel"]
    source_task_id: Optional[int] = None
    labels: list[str] = Field(default_factory=list)
    annotations: list[AnnotationCreate] = Field(default_factory=list)


class ConsensusResolutionOut(BaseModel):
    id: int
    case_id: int
    sample_id: int
    method: str
    outcome: str
    final_labels: list[str] = Field(default_factory=list)
    final_annotations: Optional[list[ConsensusTaskAnnotationOut]] = None
    match_report: Optional[dict] = None
    agreement_score: Optional[float] = None
    vote_count: int
    resolver: str
    created_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def _empty_geometry_defaults(self):
        if self.final_annotations is None:
            self.final_annotations = []
        if self.match_report is None:
            self.match_report = {}
        return self


class ConsensusMetricsOut(BaseModel):
    total_items: int = 0
    pending_items: int = 0
    in_progress_items: int = 0
    resolved_items: int = 0
    review_items: int = 0
    rejected_items: int = 0
    total_tasks: int = 0
    submitted_tasks: int = 0
    auto_resolved: int = 0
    manually_resolved: int = 0
    mean_agreement_score: float = 0.0
    agreement_distribution: list[dict] = Field(default_factory=list)
    per_user: list[dict] = Field(default_factory=list)


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
    old_mask_json: Optional[dict] = None
    new_mask_json: Optional[dict] = None
    changed_by: str
    changed_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def _masks_to_wire(self):
        if self.old_mask_json is not None:
            self.old_mask_json = masks.to_wire(self.old_mask_json)
        if self.new_mask_json is not None:
            self.new_mask_json = masks.to_wire(self.new_mask_json)
        return self


# ---------------------------------------------------------------------------
# Finetuning
# ---------------------------------------------------------------------------
class FinetuneTriggerRequest(BaseModel):
    export_path: str
    base_model: Optional[str] = None
    adapter_type: Optional[str] = None
    epochs: Optional[int] = None
    learning_rate: Optional[float] = None
    uc_model_name: Optional[str] = None  # UC Model Registry: catalog.schema.model_name


class FinetuneRunOut(BaseModel):
    id: int
    project_id: int
    status: str
    export_path: str
    base_model: Optional[str] = None
    adapter_type: Optional[str] = None
    epochs: Optional[int] = None
    learning_rate: Optional[float] = None
    uc_model_name: Optional[str] = None
    mlflow_run_id: Optional[str] = None
    mlflow_experiment_id: Optional[str] = None
    metrics_json: Optional[dict] = None
    databricks_run_id: Optional[int] = None
    databricks_run_url: Optional[str] = None
    error_message: Optional[str] = None
    created_by: str = ""
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ExportRequest(BaseModel):
    export_volume: str = ""
    mode: Literal["copy", "reference"] = "copy"


class ExportInfo(BaseModel):
    """Metadata about an available export in the export volume."""
    export_path: str
    project_name: str
    version: int
    task_type: str
    class_list: list[str]
    image_count: int
    annotation_count: int
    exported_at: str
    exported_by: str
    format: str
    huggingface_code: Optional[str] = None
    loading_code: Optional[str] = None


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
