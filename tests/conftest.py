"""
Shared fixtures for CV Dataset Explorer tests.

Provides an in-memory SQLite database, pre-populated datasets,
and sample images for testing.
"""

import json
import os
import shutil
import tempfile

import pytest
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from utils.database import Base, Dataset, Sample, Annotation, Tag


# ---------------------------------------------------------------------------
# In-memory database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_engine():
    """Create an in-memory SQLite engine with tables."""
    engine = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Create a fresh database session for each test."""
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# Test image fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_image_dir():
    """Create a temporary directory with sample test images."""
    tmpdir = tempfile.mkdtemp(prefix="cv_test_images_")
    # Create sample images
    for i in range(5):
        img = Image.new("RGB", (100, 100), color=(i * 50, 100, 200))
        img.save(os.path.join(tmpdir, f"test_image_{i:03d}.jpg"))
    # Create a non-image file (should be ignored)
    with open(os.path.join(tmpdir, "readme.txt"), "w") as f:
        f.write("not an image")
    yield tmpdir
    shutil.rmtree(tmpdir)


@pytest.fixture
def tmp_image_dir_with_coco(tmp_image_dir):
    """Extend tmp_image_dir with a COCO labels.json file."""
    coco = {
        "images": [
            {"id": 1, "file_name": "test_image_000.jpg", "width": 100, "height": 100},
            {"id": 2, "file_name": "test_image_001.jpg", "width": 100, "height": 100},
        ],
        "categories": [
            {"id": 1, "name": "car"},
            {"id": 2, "name": "person"},
        ],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 1, "bbox": [10, 20, 30, 40]},
            {"id": 2, "image_id": 1, "category_id": 2, "bbox": [50, 50, 20, 20]},
            {"id": 3, "image_id": 2, "category_id": 1, "bbox": [5, 5, 80, 80]},
        ],
    }
    with open(os.path.join(tmp_image_dir, "labels.json"), "w") as f:
        json.dump(coco, f)
    return tmp_image_dir


# ---------------------------------------------------------------------------
# Pre-populated dataset fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_dataset(db_session, tmp_image_dir):
    """Create a dataset with 5 samples in the test database."""
    ds = Dataset(name="test_dataset", image_dir=tmp_image_dir)
    db_session.add(ds)
    db_session.flush()

    samples = []
    for i in range(5):
        fname = f"test_image_{i:03d}.jpg"
        s = Sample(
            dataset_id=ds.id,
            filepath=os.path.join(tmp_image_dir, fname),
            filename=fname,
        )
        db_session.add(s)
        samples.append(s)

    db_session.flush()
    return ds, samples


@pytest.fixture
def annotated_dataset(db_session, sample_dataset):
    """A dataset with some annotations and tags already applied."""
    ds, samples = sample_dataset

    # Add classification to sample 0
    db_session.add(Annotation(
        sample_id=samples[0].id,
        ann_type="classification",
        label="car",
    ))

    # Add detection to sample 1
    db_session.add(Annotation(
        sample_id=samples[1].id,
        ann_type="detection",
        label="person",
        bbox_json=json.dumps({"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}),
        confidence=0.95,
    ))

    # Add segmentation to sample 2
    db_session.add(Annotation(
        sample_id=samples[2].id,
        ann_type="segmentation",
        label="car",
        polygon_json=json.dumps([[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]]),
    ))

    # Add tags
    db_session.add(Tag(sample_id=samples[0].id, tag="labeled"))
    db_session.add(Tag(sample_id=samples[1].id, tag="labeled"))
    db_session.add(Tag(sample_id=samples[0].id, tag="good"))
    db_session.add(Tag(sample_id=samples[3].id, tag="flagged"))

    db_session.commit()
    return ds, samples


# ---------------------------------------------------------------------------
# Monkeypatch helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def patch_get_session(db_session, monkeypatch):
    """Patch get_session everywhere it's imported to return the test session."""
    monkeypatch.setattr("utils.database.get_session", lambda: db_session)
    monkeypatch.setattr("utils.database.init_db", lambda: None)
    # Also patch the imported reference in datasets.py and labeling.py
    monkeypatch.setattr("utils.datasets.get_session", lambda: db_session)
    monkeypatch.setattr("utils.labeling.get_session", lambda: db_session)
    return db_session
