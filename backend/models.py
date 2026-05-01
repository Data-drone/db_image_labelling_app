"""
SQLAlchemy models for CV Explorer — Phase 1 (Lakebase).

Tables:
- labeling_projects: project metadata
- project_samples: images within a project
- annotations: labels applied to samples
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import (
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
)
from sqlalchemy.orm import DeclarativeBase, relationship

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class LabelingProject(Base):
    __tablename__ = "labeling_projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(Text, default="")
    task_type = Column(String(50), nullable=False)  # 'classification' or 'detection'
    class_list = Column(JSON, nullable=False)  # e.g. ["cat", "dog", "car"]
    source_volume = Column(Text, nullable=False)  # UC Volume path
    serving_endpoint = Column(String(255), nullable=True)  # Model Serving endpoint name
    endpoint_config = Column(JSON, nullable=True)  # response parsing overrides
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    version = Column(Integer, default=1, nullable=False)
    parent_project_id = Column(Integer, nullable=True)  # FK to parent version (null = original)

    samples = relationship(
        "ProjectSample", back_populates="project", cascade="all, delete-orphan",
    )
    annotations = relationship(
        "Annotation", back_populates="project", cascade="all, delete-orphan",
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
    ann_type = Column(String(50), nullable=False)  # 'classification' or 'bbox'
    bbox_json = Column(JSON, nullable=True)  # {"x":..,"y":..,"w":..,"h":..}
    is_draft = Column(Boolean, nullable=False, default=False)  # model suggestions vs human-confirmed
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

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
    completed = Column(Integer, default=0, nullable=False)
    failed = Column(Integer, default=0, nullable=False)
    skipped = Column(Integer, default=0, nullable=False)
    total_planned = Column(Integer, default=0, nullable=False)
    databricks_run_id = Column(Integer, nullable=True)  # jobs.run_now response (run id)
    error_message = Column(Text, nullable=True)
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_preannotate_runs_project", "project_id"),
        Index("ix_preannotate_runs_status", "status"),
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
    changed_by = Column(String(255), default="")
    changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("ix_annotation_history_sample", "sample_id"),
        Index("ix_annotation_history_project", "project_id"),
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


def init_db(engine):
    """Create all tables and set REPLICA IDENTITY FULL for Lakehouse Sync."""
    Base.metadata.create_all(engine)
    log.info("Database tables created")
    ensure_annotations_is_draft_column(engine)
    ensure_preannotate_runs_table(engine)

    try:
        from .lakebase import setup_replica_identity
        setup_replica_identity(engine, TABLE_NAMES)
    except Exception as e:
        log.warning("Replica identity setup skipped: %s", e)
