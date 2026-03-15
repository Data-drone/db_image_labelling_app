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
    Column, Integer, String, Text, DateTime, ForeignKey, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    class_list = Column(JSONB, nullable=False)  # e.g. ["cat", "dog", "car"]
    source_volume = Column(Text, nullable=False)  # UC Volume path
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

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
    status = Column(String(50), default="unlabeled", nullable=False)  # unlabeled, labeled, skipped

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
    bbox_json = Column(JSONB, nullable=True)  # {"x":..,"y":..,"w":..,"h":..}
    created_by = Column(String(255), default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    sample = relationship("ProjectSample", back_populates="annotations")
    project = relationship("LabelingProject", back_populates="annotations")

    __table_args__ = (
        Index("ix_annotations_project", "project_id"),
        Index("ix_annotations_sample", "sample_id"),
    )


# ---------------------------------------------------------------------------
# Table management
# ---------------------------------------------------------------------------
TABLE_NAMES = ["labeling_projects", "project_samples", "annotations"]


def init_db(engine):
    """Create all tables and set REPLICA IDENTITY FULL for Lakehouse Sync."""
    from backend.lakebase import setup_replica_identity

    Base.metadata.create_all(engine)
    log.info("Database tables created")

    setup_replica_identity(engine, TABLE_NAMES)
