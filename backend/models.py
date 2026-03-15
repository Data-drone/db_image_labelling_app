"""
SQLAlchemy models for CV Dataset Explorer.
Reuses the same schema as the Streamlit app (utils/database.py).
"""

import json
import os
import shutil
import threading
import time
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

# Volume path for persistent DB backup (set via env or defaults to None)
DB_BACKUP_VOLUME = os.environ.get("DB_BACKUP_VOLUME", "")


# ---------------------------------------------------------------------------
# DB backup/restore to UC Volume for persistence across deploys
# ---------------------------------------------------------------------------
def _get_local_db_path() -> str:
    """Extract local file path from sqlite:/// URL."""
    if DATABASE_URL.startswith("sqlite:///"):
        return DATABASE_URL.replace("sqlite:///", "", 1)
    return ""


def _restore_db_from_volume():
    """On startup, download DB from UC Volume if it exists."""
    if not DB_BACKUP_VOLUME:
        return
    local_path = _get_local_db_path()
    if not local_path:
        return
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        volume_path = DB_BACKUP_VOLUME.rstrip("/") + "/cv_explorer.db"
        resp = w.files.download(volume_path)
        data = resp.contents.read()
        if len(data) > 0:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(data)
            print(f"Restored DB from {volume_path} ({len(data)} bytes)")
    except Exception as e:
        print(f"No backup DB to restore (this is normal on first run): {e}")


def backup_db_to_volume():
    """Upload local SQLite DB to UC Volume for persistence."""
    if not DB_BACKUP_VOLUME:
        return
    local_path = _get_local_db_path()
    if not local_path or not os.path.exists(local_path):
        return
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        volume_path = DB_BACKUP_VOLUME.rstrip("/") + "/cv_explorer.db"
        with open(local_path, "rb") as f:
            w.files.upload(volume_path, f, overwrite=True)
        print(f"Backed up DB to {volume_path}")
    except Exception as e:
        print(f"DB backup failed: {e}")


# Debounced backup: schedules a backup after writes settle
_backup_timer = None
_backup_lock = threading.Lock()


def schedule_backup():
    """Schedule a DB backup 5 seconds after the last write."""
    global _backup_timer
    if not DB_BACKUP_VOLUME:
        return
    with _backup_lock:
        if _backup_timer:
            _backup_timer.cancel()
        _backup_timer = threading.Timer(5.0, backup_db_to_volume)
        _backup_timer.daemon = True
        _backup_timer.start()


# Restore DB from volume before creating engine
_restore_db_from_volume()

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
