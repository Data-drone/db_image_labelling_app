"""
SQLAlchemy models for CV Dataset Explorer.
Reuses the same schema as the Streamlit app (utils/database.py).
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Float, Text, DateTime, ForeignKey,
    create_engine, event,
)
from sqlalchemy.orm import (
    DeclarativeBase, Session, relationship, sessionmaker,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite:////tmp/cv_explorer.db",
)

engine = create_engine(DATABASE_URL, echo=False)

# Enable WAL mode for SQLite (better concurrent reads)
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(Text, default="")
    image_dir = Column(Text, default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    samples = relationship(
        "Sample", back_populates="dataset", cascade="all, delete-orphan"
    )


class Sample(Base):
    __tablename__ = "samples"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id"), nullable=False, index=True)
    filepath = Column(Text, nullable=False)
    filename = Column(String(512), nullable=False)
    metadata_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    dataset = relationship("Dataset", back_populates="samples")
    annotations = relationship(
        "Annotation", back_populates="sample", cascade="all, delete-orphan",
        lazy="subquery",
    )
    tags = relationship(
        "Tag", back_populates="sample", cascade="all, delete-orphan",
        lazy="subquery",
    )

    @property
    def tag_list(self) -> list[str]:
        return [t.tag for t in self.tags]

    def has_tag(self, tag: str) -> bool:
        return any(t.tag == tag for t in self.tags)


class Annotation(Base):
    __tablename__ = "annotations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sample_id = Column(Integer, ForeignKey("samples.id"), nullable=False, index=True)
    ann_type = Column(String(50), nullable=False)
    label = Column(String(255), nullable=False, index=True)
    bbox_json = Column(Text, default=None)
    polygon_json = Column(Text, default=None)
    confidence = Column(Float, default=None)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    sample = relationship("Sample", back_populates="annotations")

    @property
    def bounding_box(self) -> Optional[list[float]]:
        if self.bbox_json:
            data = json.loads(self.bbox_json)
            return [data["x"], data["y"], data["w"], data["h"]]
        return None

    @property
    def polygon_points(self) -> Optional[list[list[float]]]:
        if self.polygon_json:
            return json.loads(self.polygon_json)
        return None


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sample_id = Column(Integer, ForeignKey("samples.id"), nullable=False, index=True)
    tag = Column(String(255), nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    sample = relationship("Sample", back_populates="tags")


# ---------------------------------------------------------------------------
# Create tables
# ---------------------------------------------------------------------------
def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(engine)


def get_session() -> Session:
    """Return a new database session."""
    init_db()
    return SessionLocal()
