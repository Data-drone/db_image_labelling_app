"""
Tests for utils/datasets.py — dataset CRUD, querying, filtering, and export.
"""

import json
import os
import tempfile

import pytest

from utils.database import Dataset, Sample, Annotation, Tag


class TestListDatasets:
    def test_empty(self, db_session, patch_get_session):
        from utils.datasets import list_datasets
        assert list_datasets() == []

    def test_returns_names_sorted(self, db_session, patch_get_session):
        from utils.datasets import list_datasets
        db_session.add(Dataset(name="zebra", image_dir="/tmp"))
        db_session.add(Dataset(name="alpha", image_dir="/tmp"))
        db_session.commit()
        assert list_datasets() == ["alpha", "zebra"]


class TestLoadDataset:
    def test_load_existing(self, db_session, patch_get_session):
        from utils.datasets import load_dataset
        db_session.add(Dataset(name="test_ds", image_dir="/tmp"))
        db_session.commit()
        ds = load_dataset("test_ds")
        assert ds is not None
        assert ds.name == "test_ds"

    def test_load_nonexistent(self, db_session, patch_get_session):
        from utils.datasets import load_dataset
        ds = load_dataset("does_not_exist")
        assert ds is None


class TestCreateDatasetFromDirectory:
    def test_creates_dataset_with_images(self, db_session, patch_get_session, tmp_image_dir):
        from utils.datasets import create_dataset_from_directory
        result = create_dataset_from_directory("new_ds", tmp_image_dir)
        assert result is True

        ds = db_session.query(Dataset).filter_by(name="new_ds").first()
        assert ds is not None
        samples = db_session.query(Sample).filter_by(dataset_id=ds.id).all()
        assert len(samples) == 5  # 5 jpg images, readme.txt excluded

    def test_ignores_non_image_files(self, db_session, patch_get_session, tmp_image_dir):
        from utils.datasets import create_dataset_from_directory
        create_dataset_from_directory("ds", tmp_image_dir)
        ds = db_session.query(Dataset).filter_by(name="ds").first()
        filenames = [s.filename for s in ds.samples]
        assert "readme.txt" not in filenames

    def test_refuses_duplicate_name(self, db_session, patch_get_session, tmp_image_dir):
        from utils.datasets import create_dataset_from_directory
        create_dataset_from_directory("dup", tmp_image_dir)
        result = create_dataset_from_directory("dup", tmp_image_dir)
        assert result is False

    def test_imports_coco_annotations(self, db_session, patch_get_session, tmp_image_dir_with_coco):
        from utils.datasets import create_dataset_from_directory
        create_dataset_from_directory("coco_ds", tmp_image_dir_with_coco)
        ds = db_session.query(Dataset).filter_by(name="coco_ds").first()
        anns = db_session.query(Annotation).join(Sample).filter(Sample.dataset_id == ds.id).all()
        assert len(anns) == 3
        labels = {a.label for a in anns}
        assert labels == {"car", "person"}


class TestDeleteDataset:
    def test_delete_existing(self, db_session, patch_get_session, sample_dataset):
        from utils.datasets import delete_dataset
        ds, _ = sample_dataset
        result = delete_dataset(ds.name)
        assert result is True
        assert db_session.query(Dataset).filter_by(name=ds.name).first() is None

    def test_delete_nonexistent(self, db_session, patch_get_session):
        from utils.datasets import delete_dataset
        result = delete_dataset("nope")
        assert result is False


class TestGetSamples:
    def test_returns_all(self, db_session, sample_dataset):
        from utils.datasets import get_samples
        ds, _ = sample_dataset
        samples = get_samples(ds, db_session)
        assert len(samples) == 5

    def test_ordered_by_id(self, db_session, sample_dataset):
        from utils.datasets import get_samples
        ds, _ = sample_dataset
        samples = get_samples(ds, db_session)
        ids = [s.id for s in samples]
        assert ids == sorted(ids)


class TestCountSamples:
    def test_count(self, db_session, sample_dataset):
        from utils.datasets import count_samples
        ds, _ = sample_dataset
        assert count_samples(ds, db_session) == 5


class TestGetSampleAtIndex:
    def test_valid_index(self, db_session, sample_dataset):
        from utils.datasets import get_sample_at_index
        ds, samples = sample_dataset
        s = get_sample_at_index(ds, 0, db_session)
        assert s is not None
        assert s.id == samples[0].id

    def test_out_of_range(self, db_session, sample_dataset):
        from utils.datasets import get_sample_at_index
        ds, _ = sample_dataset
        s = get_sample_at_index(ds, 999, db_session)
        assert s is None


class TestGetClasses:
    def test_empty_dataset(self, db_session, sample_dataset):
        from utils.datasets import get_classes
        ds, _ = sample_dataset
        assert get_classes(ds, db_session) == []

    def test_with_annotations(self, db_session, annotated_dataset):
        from utils.datasets import get_classes
        ds, _ = annotated_dataset
        classes = get_classes(ds, db_session)
        assert "car" in classes
        assert "person" in classes


class TestGetTags:
    def test_empty_dataset(self, db_session, sample_dataset):
        from utils.datasets import get_tags
        ds, _ = sample_dataset
        assert get_tags(ds, db_session) == []

    def test_with_tags(self, db_session, annotated_dataset):
        from utils.datasets import get_tags
        ds, _ = annotated_dataset
        tags = get_tags(ds, db_session)
        assert "labeled" in tags
        assert "good" in tags
        assert "flagged" in tags


class TestFilteredSamples:
    def test_filter_by_label(self, db_session, annotated_dataset):
        from utils.datasets import filtered_samples
        ds, _ = annotated_dataset
        results = filtered_samples(ds, labels=["car"], session=db_session)
        assert len(results) >= 1
        # All returned samples should have a "car" annotation
        for s in results:
            labels = [a.label for a in s.annotations]
            assert "car" in labels

    def test_filter_by_tag(self, db_session, annotated_dataset):
        from utils.datasets import filtered_samples
        ds, _ = annotated_dataset
        results = filtered_samples(ds, tags=["flagged"], session=db_session)
        assert len(results) == 1

    def test_filter_by_confidence(self, db_session, annotated_dataset):
        from utils.datasets import filtered_samples
        ds, _ = annotated_dataset
        results = filtered_samples(ds, confidence=0.9, session=db_session)
        assert len(results) >= 1

    def test_no_match(self, db_session, annotated_dataset):
        from utils.datasets import filtered_samples
        ds, _ = annotated_dataset
        results = filtered_samples(ds, labels=["airplane"], session=db_session)
        assert len(results) == 0


class TestExportCoco:
    def test_export_creates_json(self, db_session, annotated_dataset):
        from utils.datasets import export_coco
        ds, _ = annotated_dataset
        tmpdir = tempfile.mkdtemp(prefix="coco_export_test_")
        result = export_coco(ds, export_dir=tmpdir, session=db_session)
        assert result == tmpdir
        labels_path = os.path.join(tmpdir, "labels.json")
        assert os.path.exists(labels_path)

        with open(labels_path) as f:
            coco = json.load(f)

        assert "images" in coco
        assert "annotations" in coco
        assert "categories" in coco
        assert len(coco["images"]) == 5
        assert len(coco["categories"]) >= 2
