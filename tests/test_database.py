"""
Tests for utils/database.py — ORM models, relationships, and properties.
"""

import json

import pytest

from utils.database import Base, Dataset, Sample, Annotation, Tag


class TestDatasetModel:
    def test_create_dataset(self, db_session):
        ds = Dataset(name="my_dataset", description="A test", image_dir="/tmp/imgs")
        db_session.add(ds)
        db_session.commit()

        loaded = db_session.query(Dataset).filter_by(name="my_dataset").first()
        assert loaded is not None
        assert loaded.name == "my_dataset"
        assert loaded.description == "A test"
        assert loaded.image_dir == "/tmp/imgs"
        assert loaded.created_at is not None

    def test_dataset_name_unique(self, db_session):
        db_session.add(Dataset(name="dup", image_dir="/tmp"))
        db_session.commit()
        db_session.add(Dataset(name="dup", image_dir="/tmp2"))
        with pytest.raises(Exception):
            db_session.commit()

    def test_cascade_delete_samples(self, db_session, sample_dataset):
        ds, samples = sample_dataset
        assert db_session.query(Sample).count() == 5
        db_session.delete(ds)
        db_session.commit()
        assert db_session.query(Sample).count() == 0

    def test_cascade_delete_annotations_and_tags(self, db_session, annotated_dataset):
        ds, _ = annotated_dataset
        assert db_session.query(Annotation).count() > 0
        assert db_session.query(Tag).count() > 0
        db_session.delete(ds)
        db_session.commit()
        assert db_session.query(Annotation).count() == 0
        assert db_session.query(Tag).count() == 0


class TestSampleModel:
    def test_create_sample(self, db_session, sample_dataset):
        ds, samples = sample_dataset
        assert len(samples) == 5
        assert samples[0].filename == "test_image_000.jpg"
        assert samples[0].dataset_id == ds.id

    def test_tag_list_property(self, db_session, sample_dataset):
        ds, samples = sample_dataset
        db_session.add(Tag(sample_id=samples[0].id, tag="good"))
        db_session.add(Tag(sample_id=samples[0].id, tag="labeled"))
        db_session.commit()
        db_session.refresh(samples[0])
        assert set(samples[0].tag_list) == {"good", "labeled"}

    def test_has_tag(self, db_session, sample_dataset):
        ds, samples = sample_dataset
        db_session.add(Tag(sample_id=samples[0].id, tag="flagged"))
        db_session.commit()
        db_session.refresh(samples[0])
        assert samples[0].has_tag("flagged")
        assert not samples[0].has_tag("nonexistent")

    def test_sample_annotations_relationship(self, db_session, sample_dataset):
        ds, samples = sample_dataset
        db_session.add(Annotation(
            sample_id=samples[0].id,
            ann_type="classification",
            label="car",
        ))
        db_session.commit()
        db_session.refresh(samples[0])
        assert len(samples[0].annotations) == 1
        assert samples[0].annotations[0].label == "car"


class TestAnnotationModel:
    def test_classification_annotation(self, db_session, sample_dataset):
        _, samples = sample_dataset
        ann = Annotation(
            sample_id=samples[0].id,
            ann_type="classification",
            label="truck",
        )
        db_session.add(ann)
        db_session.commit()
        assert ann.bounding_box is None
        assert ann.polygon_points is None

    def test_detection_annotation_bbox(self, db_session, sample_dataset):
        _, samples = sample_dataset
        bbox = {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}
        ann = Annotation(
            sample_id=samples[0].id,
            ann_type="detection",
            label="car",
            bbox_json=json.dumps(bbox),
            confidence=0.85,
        )
        db_session.add(ann)
        db_session.commit()

        assert ann.bounding_box == [0.1, 0.2, 0.3, 0.4]
        assert ann.confidence == 0.85

    def test_segmentation_annotation_polygon(self, db_session, sample_dataset):
        _, samples = sample_dataset
        points = [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]]
        ann = Annotation(
            sample_id=samples[0].id,
            ann_type="segmentation",
            label="person",
            polygon_json=json.dumps(points),
        )
        db_session.add(ann)
        db_session.commit()

        assert ann.polygon_points == points
        assert ann.bounding_box is None


class TestTagModel:
    def test_create_tag(self, db_session, sample_dataset):
        _, samples = sample_dataset
        tag = Tag(sample_id=samples[0].id, tag="review")
        db_session.add(tag)
        db_session.commit()

        loaded = db_session.query(Tag).filter_by(tag="review").first()
        assert loaded is not None
        assert loaded.sample_id == samples[0].id
