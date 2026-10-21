"""
SQLAlchemy models for CV Explorer — Phase 1 (Lakebase).

Tables:
- labeling_projects: project metadata
- project_samples: images within a project
- annotations: labels applied to samples
- consensus_cases / labeling_tasks / task_annotations: blind independent work
- consensus_resolutions: append-only voting and reviewer outcomes
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Float,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, deferred, relationship

log = logging.getLogger(__name__)

EMBEDDING_DIM = 1024  # DINOv3 output dimension


class Base(DeclarativeBase):
    pass


class LabelingProject(Base):
    __tablename__ = "labeling_projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(Text, default="")
    task_type = Column(String(50), nullable=False)  # 'classification', 'detection' or 'segmentation'
    class_list = Column(JSON, nullable=False)  # e.g. ["cat", "dog", "car"]
    source_volume = Column(Text, nullable=False)  # UC Volume path (hint prefix for table sources)
    source_type = Column(String(20), nullable=False, default="volume")  # 'volume' or 'table'
    source_table = Column(Text, nullable=True)  # catalog.schema.table when source_type='table'
    image_path_column = Column(String(255), nullable=True)
    source_filter = Column(Text, nullable=True)  # optional WHERE body for table scan
    # Volume-scan recursion. Depth 0 (or include_subfolders=false) is the
    # selected directory only. Table-backed projects ignore these flags.
    scan_include_subfolders = Column(Boolean, nullable=False, default=False)
    scan_max_depth = Column(Integer, nullable=False, default=3)
    serving_endpoint = Column(String(255), nullable=True)  # Model Serving endpoint name
    endpoint_config = Column(JSON, nullable=True)  # response parsing overrides
    consensus_enabled = Column(Boolean, nullable=False, default=False)
    consensus_panel_size = Column(Integer, nullable=False, default=3)
    consensus_coverage_percent = Column(Integer, nullable=False, default=100)
    consensus_quorum = Column(Integer, nullable=False, default=2)
    consensus_iou_threshold = Column(Float, nullable=False, default=0.5)
    consensus_agreement_threshold = Column(Float, nullable=False, default=0.5)
    consensus_reviewer_emails = Column(JSON, nullable=True)
    # Empty means every authenticated App user who is not a reviewer may vote.
    consensus_labeller_emails = Column(JSON, nullable=True)
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    archived_at = Column(DateTime(timezone=True), nullable=True)
    version = Column(Integer, default=1, nullable=False)
    parent_project_id = Column(Integer, nullable=True)  # FK to parent version (null = original)

    samples = relationship(
        "ProjectSample", back_populates="project", cascade="all, delete-orphan",
    )
    annotations = relationship(
        "Annotation", back_populates="project", cascade="all, delete-orphan",
    )
    consensus_cases = relationship(
        "ConsensusCase", back_populates="project", cascade="all, delete-orphan",
    )


class ProjectSample(Base):
    __tablename__ = "project_samples"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("labeling_projects.id"), nullable=False)
    filepath = Column(Text, nullable=False)
    filename = Column(String(512), nullable=False)
    locked_by = Column(String(255), nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), default="unlabeled", nullable=False)  # unlabeled, pre_labeled, labeled, skipped
    embedding = deferred(Column(JSON, nullable=True))
    # Native pgvector column for indexed similarity search (Postgres only).
    # On SQLite this column is skipped; the JSON `embedding` column is used as fallback.
    embedding_vec = deferred(Column(JSON, nullable=True))  # placeholder type; overridden at runtime for Postgres
    prediction_confidence = Column(Float, nullable=True)
    # Pixel dimensions in the orientation the browser renders, i.e. after
    # PIL.ImageOps.exif_transpose. Masks are stored against these numbers, so
    # storing the pre-rotation size here would misplace every mask on an
    # EXIF-rotated photo. Nullable: samples imported before segmentation
    # existed are backfilled lazily on first mask save.
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)

    # Legacy umap_x/umap_y columns may exist in the DB but are unused; Cluster
    # Map persists coordinates in cluster_map_points.

    project = relationship("LabelingProject", back_populates="samples")
    annotations = relationship(
        "Annotation", back_populates="sample", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_project_samples_project_status", "project_id", "status"),
    )


class Annotation(Base):
    __tablename__ = "annotations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sample_id = Column(Integer, ForeignKey("project_samples.id"), nullable=False)
    project_id = Column(Integer, ForeignKey("labeling_projects.id"), nullable=False)
    label = Column(String(255), nullable=False)
    ann_type = Column(String(50), nullable=False)  # 'classification', 'bbox' or 'mask'
    bbox_json = Column(JSON, nullable=True)  # {"x":..,"y":..,"w":..,"h":..}
    # COCO *compressed* RLE: {"size": [h, w], "counts": "<ascii>"}. The API
    # speaks the uncompressed counts-array form; see backend/masks.py for why
    # the compression lives on this side of the wire.
    mask_json = Column(JSON, nullable=True)
    is_draft = Column(Boolean, nullable=False, default=False)  # model suggestions vs human-confirmed
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    consensus_resolution_id = Column(Integer, nullable=True)

    sample = relationship("ProjectSample", back_populates="annotations")
    project = relationship("LabelingProject", back_populates="annotations")

    __table_args__ = (
        Index("ix_annotations_project", "project_id"),
        Index("ix_annotations_sample", "sample_id"),
    )


class PreannotateRun(Base):
    """Tracks async (Databricks Job) pre-annotation batch work."""

    __tablename__ = "preannotate_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("labeling_projects.id"), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    # queued = job submitted; running = worker executing; terminal: succeeded | failed | cancelled
    max_samples = Column(Integer, default=0, nullable=False)  # 0 = all matching samples
    include_pre_labeled = Column(Boolean, default=False, nullable=False)
    min_confidence = Column(Float, nullable=True)
    text_prompt = Column(Text, nullable=True)
    completed = Column(Integer, default=0, nullable=False)
    failed = Column(Integer, default=0, nullable=False)
    skipped = Column(Integer, default=0, nullable=False)
    total_planned = Column(Integer, default=0, nullable=False)
    databricks_run_id = Column(BigInteger, nullable=True)  # jobs.run_now response (run id)
    error_message = Column(Text, nullable=True)
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_preannotate_runs_project", "project_id"),
        Index("ix_preannotate_runs_status", "status"),
    )


class EmbeddingRun(Base):
    """Tracks in-app embedding generation runs so progress survives page reloads."""

    __tablename__ = "embedding_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("labeling_projects.id"), nullable=False)
    status = Column(String(32), nullable=False, default="running")
    completed = Column(Integer, default=0, nullable=False)
    failed = Column(Integer, default=0, nullable=False)
    skipped = Column(Integer, default=0, nullable=False)
    total_planned = Column(Integer, default=0, nullable=False)
    force = Column(Boolean, default=False, nullable=False)
    error_message = Column(Text, nullable=True)
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_embedding_runs_project", "project_id"),
    )


class ClusterMapRun(Base):
    """Tracks in-app UMAP projection so Cluster Map GET never blocks on compute."""

    __tablename__ = "cluster_map_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("labeling_projects.id"), nullable=False)
    status = Column(String(32), nullable=False, default="running")
    sample_count = Column(Integer, default=0, nullable=False)
    sample_fingerprint = Column(String(64), default="", nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_cluster_map_runs_project", "project_id"),
        Index("ix_cluster_map_runs_status", "status"),
    )


class ClusterMapPointCache(Base):
    """Cached 2D projection coordinates, independent of optional umap_x/umap_y."""

    __tablename__ = "cluster_map_points"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("labeling_projects.id"), nullable=False)
    sample_id = Column(Integer, ForeignKey("project_samples.id"), nullable=False)
    x = Column(Float, nullable=False)
    y = Column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "sample_id", name="uq_cluster_map_points_sample"),
        Index("ix_cluster_map_points_project", "project_id"),
    )


class FinetuneRun(Base):
    """Tracks async (Databricks Job) finetuning runs triggered after dataset export."""

    __tablename__ = "finetune_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("labeling_projects.id"), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    export_path = Column(Text, nullable=False)
    # Training config
    base_model = Column(String(255), nullable=True)
    adapter_type = Column(String(50), nullable=True)
    epochs = Column(Integer, nullable=True)
    learning_rate = Column(Float, nullable=True)
    # UC lineage: where to register the output model
    uc_model_name = Column(String(512), nullable=True)  # e.g. "brian_gen_ai.cv_explorer.my_sam_model"
    # Results / metrics
    mlflow_run_id = Column(String(255), nullable=True)
    mlflow_experiment_id = Column(String(255), nullable=True)
    metrics_json = Column(JSON, nullable=True)
    # Databricks job tracking
    databricks_run_id = Column(BigInteger, nullable=True)
    databricks_run_url = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_finetune_runs_project", "project_id"),
        Index("ix_finetune_runs_status", "status"),
    )


class AnnotationHistory(Base):
    __tablename__ = "annotation_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sample_id = Column(Integer, ForeignKey("project_samples.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(Integer, ForeignKey("labeling_projects.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(50), nullable=False)  # 'create', 'update', 'delete'
    old_label = Column(String(255), nullable=True)
    new_label = Column(String(255), nullable=True)
    old_ann_type = Column(String(50), nullable=True)
    new_ann_type = Column(String(50), nullable=True)
    old_bbox_json = Column(JSON, nullable=True)
    new_bbox_json = Column(JSON, nullable=True)
    old_mask_json = Column(JSON, nullable=True)
    new_mask_json = Column(JSON, nullable=True)
    changed_by = Column(String(255), default="")
    changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_annotation_history_sample", "sample_id"),
        Index("ix_annotation_history_project", "project_id"),
    )


class AppUser(Base):
    """A Databricks user who has authenticated through the Apps proxy."""

    __tablename__ = "app_users"

    email = Column(String(255), primary_key=True)
    display_name = Column(String(255), nullable=True)
    last_seen_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class ConsensusCase(Base):
    """Per-sample state for an opt-in multi-labeller consensus project."""

    __tablename__ = "consensus_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(
        Integer, ForeignKey("labeling_projects.id", ondelete="CASCADE"), nullable=False,
    )
    sample_id = Column(
        Integer, ForeignKey("project_samples.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    selected_for_consensus = Column(Boolean, nullable=False, default=True)
    required_votes = Column(Integer, nullable=False, default=3)
    quorum = Column(Integer, nullable=False, default=2)
    iou_threshold = Column(Float, nullable=False, default=0.5)
    agreement_threshold = Column(Float, nullable=False, default=0.5)
    state = Column(String(32), nullable=False, default="pending")
    active_resolution_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    project = relationship("LabelingProject", back_populates="consensus_cases")
    sample = relationship("ProjectSample")
    tasks = relationship(
        "LabelingTask", back_populates="case", cascade="all, delete-orphan",
    )
    resolutions = relationship(
        "ConsensusResolution", back_populates="case", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_consensus_cases_project_state", "project_id", "state"),
    )


class LabelingTask(Base):
    """One private, independently submitted vote for a consensus case."""

    __tablename__ = "labeling_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(
        Integer, ForeignKey("consensus_cases.id", ondelete="CASCADE"), nullable=False,
    )
    project_id = Column(
        Integer, ForeignKey("labeling_projects.id", ondelete="CASCADE"), nullable=False,
    )
    sample_id = Column(
        Integer, ForeignKey("project_samples.id", ondelete="CASCADE"), nullable=False,
    )
    slot_index = Column(Integer, nullable=False)
    assigned_to = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    lock_token = Column(String(64), nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    assigned_at = Column(DateTime(timezone=True), nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    # Submitted votes can be revised while the case is still collecting
    # votes. Each save advances this pointer; old TaskAnnotation rows remain
    # as an append-only audit trail.
    annotation_revision = Column(Integer, nullable=False, default=0)
    # Once the required panel is complete, every submitted vote is frozen
    # before automatic resolution or reviewer adjudication begins.
    locked_for_review = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    case = relationship("ConsensusCase", back_populates="tasks")
    annotations = relationship(
        "TaskAnnotation", back_populates="task", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_labeling_tasks_project_status", "project_id", "status"),
        Index("ix_labeling_tasks_assignee_status", "assigned_to", "status"),
        Index(
            "uq_labeling_tasks_case_slot", "case_id", "slot_index", unique=True,
        ),
        Index(
            "uq_labeling_tasks_case_assignee", "case_id", "assigned_to", unique=True,
        ),
    )


class TaskAnnotation(Base):
    """An annotation in an immutable submitted task."""

    __tablename__ = "task_annotations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(
        Integer, ForeignKey("labeling_tasks.id", ondelete="CASCADE"), nullable=False,
    )
    label = Column(String(255), nullable=False)
    ann_type = Column(String(50), nullable=False, default="classification")
    bbox_json = Column(JSON, nullable=True)
    mask_json = Column(JSON, nullable=True)
    revision = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    task = relationship("LabelingTask", back_populates="annotations")

    __table_args__ = (
        Index("ix_task_annotations_task", "task_id"),
        Index("ix_task_annotations_task_revision", "task_id", "revision"),
    )


class ConsensusResolution(Base):
    """Append-only record of automatic and reviewer consensus decisions."""

    __tablename__ = "consensus_resolutions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(
        Integer, ForeignKey("consensus_cases.id", ondelete="CASCADE"), nullable=False,
    )
    project_id = Column(
        Integer, ForeignKey("labeling_projects.id", ondelete="CASCADE"), nullable=False,
    )
    sample_id = Column(
        Integer, ForeignKey("project_samples.id", ondelete="CASCADE"), nullable=False,
    )
    method = Column(String(32), nullable=False)
    outcome = Column(String(32), nullable=False, default="resolved")
    final_labels = Column(JSON, nullable=True)
    final_annotations = Column(JSON, nullable=True)
    match_report = Column(JSON, nullable=True)
    agreement_score = Column(Float, nullable=True)
    vote_count = Column(Integer, nullable=False, default=0)
    resolver = Column(String(255), nullable=False, default="")
    source_task_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    case = relationship("ConsensusCase", back_populates="resolutions")

    __table_args__ = (
        Index("ix_consensus_resolutions_case", "case_id"),
        Index("ix_consensus_resolutions_project", "project_id"),
    )


# ---------------------------------------------------------------------------
# Table management
# ---------------------------------------------------------------------------
TABLE_NAMES = [
    "labeling_projects",
    "project_samples",
    "annotations",
    "annotation_history",
    "preannotate_runs",
    "embedding_runs",
    "cluster_map_runs",
    "cluster_map_points",
    "finetune_runs",
    "consensus_cases",
    "labeling_tasks",
    "task_annotations",
    "consensus_resolutions",
    "app_users",
]


def ensure_preannotate_runs_table(engine) -> None:
    """Create ``preannotate_runs`` on existing DBs if missing (no Alembic)."""
    from sqlalchemy import inspect

    insp = inspect(engine)
    if "preannotate_runs" in insp.get_table_names():
        return
    PreannotateRun.__table__.create(bind=engine, checkfirst=True)
    log.info("preannotate_runs table created")


def ensure_annotations_is_draft_column(engine) -> None:
    """Additive migration: ``annotations.is_draft`` for model vs human labels (no Alembic)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "annotations" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("annotations")}
    if "is_draft" in cols:
        return

    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "postgresql":
            conn.execute(
                text(
                    "ALTER TABLE annotations ADD COLUMN IF NOT EXISTS is_draft "
                    "BOOLEAN NOT NULL DEFAULT false"
                )
            )
        else:
            conn.execute(
                text(
                    "ALTER TABLE annotations ADD COLUMN is_draft INTEGER NOT NULL DEFAULT 0"
                )
            )
    with engine.begin() as conn:
        if dialect == "postgresql":
            conn.execute(
                text(
                    "UPDATE annotations SET is_draft = true "
                    "WHERE created_by LIKE 'model:%'"
                )
            )
        else:
            conn.execute(
                text(
                    "UPDATE annotations SET is_draft = 1 "
                    "WHERE created_by LIKE 'model:%'"
                )
            )
    log.info("annotations.is_draft column added and model rows backfilled")


def _reassign_table_ownership(engine, table_names: list[str]) -> None:
    """Reassign ownership of tables to the current Postgres user.

    On Lakebase, tables created by a previous service principal identity
    are not owned by the current one. ALTER TABLE requires ownership.
    Tries multiple strategies: direct OWNER TO, then REASSIGN OWNED BY
    for each discovered previous owner.
    """
    if engine.dialect.name != "postgresql":
        return

    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT current_user")).fetchone()
            current_user = row[0]
    except Exception:
        return

    failed_tables = []
    for table in table_names:
        try:
            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE IF EXISTS "{table}" OWNER TO "{current_user}"'))
            log.info("Reassigned ownership of %s to %s", table, current_user)
        except Exception:
            failed_tables.append(table)

    if not failed_tables:
        return

    # Strategy 2: find distinct owners of tables we couldn't reassign, then
    # REASSIGN OWNED BY <old_owner> TO <current_user>
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DISTINCT tableowner FROM pg_tables "
                "WHERE schemaname = current_schema() "
                "AND tablename = ANY(:tables) AND tableowner != :me"
            ), {"tables": failed_tables, "me": current_user}).fetchall()
            old_owners = [r[0] for r in rows]
    except Exception:
        old_owners = []

    for old_owner in old_owners:
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    f'REASSIGN OWNED BY "{old_owner}" TO "{current_user}"'
                ))
            log.info("Reassigned all objects owned by %s to %s", old_owner, current_user)
        except Exception as e:
            log.warning("Could not REASSIGN OWNED BY %s: %s", old_owner, e)

    # Retry failed tables after REASSIGN
    for table in failed_tables:
        try:
            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE IF EXISTS "{table}" OWNER TO "{current_user}"'))
            log.info("Reassigned ownership of %s to %s (after REASSIGN OWNED BY)", table, current_user)
        except Exception as e:
            log.warning("Could not reassign ownership of %s: %s", table, e)


def _ensure_missing_columns(engine) -> None:
    """Add any columns defined in the models but absent from the DB (no Alembic).

    Only handles simple ADD COLUMN — does not change types or drop columns.
    """
    import warnings
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    dialect = engine.dialect.name
    existing_tables = set(insp.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Did not recognize type")
            existing_cols = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing_cols:
                continue
            if col.name == "embedding_vec":
                continue
            col_type = col.type.compile(dialect=engine.dialect)
            nullable = "NULL" if col.nullable else "NOT NULL"
            default_clause = ""
            if col.default is not None and col.default.is_scalar:
                val = col.default.arg
                if isinstance(val, str):
                    default_clause = f" DEFAULT '{val}'"
                elif isinstance(val, bool):
                    if dialect == "postgresql":
                        default_clause = f" DEFAULT {'true' if val else 'false'}"
                    else:
                        default_clause = f" DEFAULT {1 if val else 0}"
                elif isinstance(val, (int, float)):
                    default_clause = f" DEFAULT {val}"
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type} {nullable}{default_clause}'
            try:
                with engine.begin() as conn:
                    conn.execute(text(ddl))
                log.info("Added column %s.%s (%s)", table.name, col.name, col_type)
            except Exception as e:
                log.warning("Could not add column %s.%s: %s", table.name, col.name, e)


def _ensure_pgvector(engine) -> bool:
    """Install the pgvector extension and upgrade the embedding_vec column.

    Returns True if pgvector is available (Postgres with extension), False otherwise.
    """
    if engine.dialect.name != "postgresql":
        return False

    from sqlalchemy import inspect, text

    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        log.info("pgvector extension enabled")
    except Exception as e:
        log.warning("Could not enable pgvector extension: %s", e)
        return False

    # Check if embedding_vec column exists and has the right type using
    # information_schema (avoids SQLAlchemy not recognizing the vector type).
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'project_samples' "
            "AND column_name = 'embedding_vec'"
        )).fetchone()

    if row is None:
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    f'ALTER TABLE "project_samples" ADD COLUMN "embedding_vec" vector({EMBEDDING_DIM})'
                ))
            log.info("Added project_samples.embedding_vec vector(%d) column", EMBEDDING_DIM)
        except Exception as e:
            log.warning("Could not add embedding_vec column: %s", e)
            return False
    elif row[0] != "vector":
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    'ALTER TABLE "project_samples" DROP COLUMN "embedding_vec"'
                ))
                conn.execute(text(
                    f'ALTER TABLE "project_samples" ADD COLUMN "embedding_vec" vector({EMBEDDING_DIM})'
                ))
            log.info("Replaced embedding_vec column with vector(%d) type", EMBEDDING_DIM)
        except Exception as e:
            log.warning("Could not replace embedding_vec column: %s", e)
            return False
    else:
        log.info("project_samples.embedding_vec already has vector type")

    return True


def _backfill_embedding_vec(engine) -> int:
    """Copy existing JSON embeddings into the native vector column.

    Returns the number of rows backfilled.
    """
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, embedding FROM project_samples "
            "WHERE embedding IS NOT NULL AND embedding_vec IS NULL"
        )).fetchall()

        if not rows:
            return 0

        count = 0
        for row_id, emb_json in rows:
            if isinstance(emb_json, str):
                import json
                emb_json = json.loads(emb_json)
            if not isinstance(emb_json, list) or len(emb_json) != EMBEDDING_DIM:
                continue
            vec_literal = "[" + ",".join(str(float(v)) for v in emb_json) + "]"
            conn.execute(
                text("UPDATE project_samples SET embedding_vec = :vec WHERE id = :id"),
                {"vec": vec_literal, "id": row_id},
            )
            count += 1
            if count % 500 == 0:
                conn.commit()
        conn.commit()

    log.info("Backfilled %d embedding_vec rows from JSON embeddings", count)
    return count


def _ensure_embedding_hnsw_index(engine) -> None:
    """Create an HNSW index on embedding_vec for fast cosine similarity search."""
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_project_samples_embedding_hnsw "
                "ON project_samples USING hnsw (embedding_vec vector_cosine_ops)"
            ))
        log.info("HNSW cosine index on project_samples.embedding_vec ready")
    except Exception as e:
        log.warning("Could not create HNSW index: %s", e)


def _ensure_umap_columns(engine) -> None:
    """Add umap_x/umap_y cache columns to project_samples if possible.

    These columns are not in the ORM model to avoid breaking queries when
    they can't be created (e.g. insufficient table ownership on Lakebase).
    """
    from sqlalchemy import text

    for col in ("umap_x", "umap_y"):
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    f'ALTER TABLE "project_samples" ADD COLUMN "{col}" FLOAT NULL'
                ))
            log.info("Added project_samples.%s column", col)
        except Exception:
            pass  # Column already exists or insufficient privileges


def init_db(engine):
    """Create all tables and set REPLICA IDENTITY FULL for Lakehouse Sync."""
    _reassign_table_ownership(engine, TABLE_NAMES)

    Base.metadata.create_all(engine)
    log.info("Database tables created")
    pgvector_available = _ensure_pgvector(engine)
    _ensure_missing_columns(engine)
    ensure_annotations_is_draft_column(engine)
    ensure_preannotate_runs_table(engine)
    _ensure_umap_columns(engine)

    if pgvector_available:
        _ensure_pgvector(engine)
        _backfill_embedding_vec(engine)
        _ensure_embedding_hnsw_index(engine)

    try:
        from .lakebase import setup_replica_identity
        setup_replica_identity(engine, TABLE_NAMES)
    except Exception as e:
        log.warning("Replica identity setup skipped: %s", e)
