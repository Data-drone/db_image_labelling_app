"""
Tests for utils/labeling.py — annotation saving, navigation, and progress tracking.

These tests mock streamlit.session_state and patch get_session
so labeling functions work against the test database.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from utils.database import Dataset, Sample, Annotation, Tag


# ---------------------------------------------------------------------------
# Helper to setup session_state mock
# ---------------------------------------------------------------------------

def _mock_session_state(index=0, classes=None):
    """Return a dict-like mock for st.session_state."""
    state = {
        "labeling_index": index,
        "label_classes": classes or ["car", "truck", "person"],
        "pending_boxes": [],
        "pending_polygons": [],
        "labeling_mode": "Classification",
        "autosave": True,
        "bbox_reset": 0,
        "poly_reset": 0,
    }
    return state


@pytest.fixture
def mock_st(monkeypatch):
    """Patch streamlit session_state for labeling tests."""
    state = _mock_session_state()

    class FakeSessionState(dict):
        def __getattr__(self, key):
            if key in self:
                return self[key]
            raise AttributeError(key)

        def __setattr__(self, key, value):
            self[key] = value

    fake_state = FakeSessionState(state)

    monkeypatch.setattr("streamlit.session_state", fake_state)
    monkeypatch.setattr("utils.labeling.st.session_state", fake_state)
    return fake_state


class TestInitLabelingState:
    def test_sets_defaults(self, mock_st):
        from utils.labeling import init_labeling_state
        # Clear some keys to test initialization
        del mock_st["pending_boxes"]
        del mock_st["pending_polygons"]
        init_labeling_state(None)
        assert mock_st["pending_boxes"] == []
        assert mock_st["pending_polygons"] == []

    def test_preserves_existing(self, mock_st):
        from utils.labeling import init_labeling_state
        mock_st["labeling_index"] = 3
        init_labeling_state(None)
        assert mock_st["labeling_index"] == 3


class TestCurrentSample:
    def test_returns_sample_at_index(self, db_session, sample_dataset, mock_st, monkeypatch):
        from utils.labeling import current_sample
        ds, samples = sample_dataset
        monkeypatch.setattr("utils.labeling.get_dataset_session", lambda d: db_session)
        monkeypatch.setattr("utils.labeling.count_samples", lambda d, s=None: 5)
        monkeypatch.setattr("utils.labeling.get_sample_at_index",
                            lambda d, idx, s=None: samples[idx])

        mock_st["labeling_index"] = 2
        result = current_sample(ds)
        assert result == samples[2]

    def test_clamps_index_to_range(self, db_session, sample_dataset, mock_st, monkeypatch):
        from utils.labeling import current_sample
        ds, samples = sample_dataset
        monkeypatch.setattr("utils.labeling.get_dataset_session", lambda d: db_session)
        monkeypatch.setattr("utils.labeling.count_samples", lambda d, s=None: 5)
        monkeypatch.setattr("utils.labeling.get_sample_at_index",
                            lambda d, idx, s=None: samples[min(idx, 4)])

        mock_st["labeling_index"] = 999
        current_sample(ds)
        assert mock_st["labeling_index"] == 4

    def test_returns_none_for_empty_dataset(self, db_session, mock_st, monkeypatch):
        from utils.labeling import current_sample
        ds = Dataset(name="empty", image_dir="/tmp")
        monkeypatch.setattr("utils.labeling.count_samples", lambda d, s=None: 0)
        result = current_sample(ds)
        assert result is None


class TestNavigation:
    def test_go_next(self, mock_st, monkeypatch):
        from utils.labeling import go_next
        ds = MagicMock()
        monkeypatch.setattr("utils.labeling.count_samples", lambda d, s=None: 5)
        mock_st["labeling_index"] = 2
        go_next(ds)
        assert mock_st["labeling_index"] == 3

    def test_go_next_clamps_at_end(self, mock_st, monkeypatch):
        from utils.labeling import go_next
        ds = MagicMock()
        monkeypatch.setattr("utils.labeling.count_samples", lambda d, s=None: 5)
        mock_st["labeling_index"] = 4
        go_next(ds)
        assert mock_st["labeling_index"] == 4

    def test_go_prev(self, mock_st):
        from utils.labeling import go_prev
        mock_st["labeling_index"] = 3
        go_prev()
        assert mock_st["labeling_index"] == 2

    def test_go_prev_clamps_at_zero(self, mock_st):
        from utils.labeling import go_prev
        mock_st["labeling_index"] = 0
        go_prev()
        assert mock_st["labeling_index"] == 0

    def test_go_next_clears_canvas_state(self, mock_st, monkeypatch):
        from utils.labeling import go_next
        ds = MagicMock()
        monkeypatch.setattr("utils.labeling.count_samples", lambda d, s=None: 5)
        mock_st["pending_boxes"] = [{"x": 1}]
        mock_st["bbox_canvas_state"] = {"objects": []}
        mock_st["labeling_index"] = 0
        go_next(ds)
        assert mock_st["pending_boxes"] == []
        assert "bbox_canvas_state" not in mock_st


class TestSaveClassification:
    def test_saves_annotation(self, db_session, sample_dataset, mock_st, monkeypatch):
        from utils.labeling import save_classification
        ds, samples = sample_dataset
        mock_st["labeling_index"] = 0
        monkeypatch.setattr("utils.labeling.get_dataset_session", lambda d: db_session)
        monkeypatch.setattr("utils.labeling.count_samples", lambda d, s=None: 5)
        monkeypatch.setattr("utils.labeling.get_sample_at_index",
                            lambda d, idx, s=None: samples[idx])

        save_classification(ds, "car")

        anns = db_session.query(Annotation).filter_by(sample_id=samples[0].id).all()
        assert any(a.ann_type == "classification" and a.label == "car" for a in anns)

    def test_advances_to_next(self, db_session, sample_dataset, mock_st, monkeypatch):
        from utils.labeling import save_classification
        ds, samples = sample_dataset
        mock_st["labeling_index"] = 0
        monkeypatch.setattr("utils.labeling.get_dataset_session", lambda d: db_session)
        monkeypatch.setattr("utils.labeling.count_samples", lambda d, s=None: 5)
        monkeypatch.setattr("utils.labeling.get_sample_at_index",
                            lambda d, idx, s=None: samples[idx])

        save_classification(ds, "truck")
        assert mock_st["labeling_index"] == 1


class TestSaveDetections:
    def test_saves_boxes_normalised(self, db_session, sample_dataset, mock_st, monkeypatch):
        from utils.labeling import save_detections
        ds, samples = sample_dataset
        mock_st["labeling_index"] = 0
        monkeypatch.setattr("utils.labeling.get_dataset_session", lambda d: db_session)
        monkeypatch.setattr("utils.labeling.count_samples", lambda d, s=None: 5)
        monkeypatch.setattr("utils.labeling.get_sample_at_index",
                            lambda d, idx, s=None: samples[idx])

        boxes = [
            {"x": 10, "y": 20, "width": 30, "height": 40, "label": "car"},
        ]
        save_detections(ds, boxes, advance=False)

        anns = db_session.query(Annotation).filter_by(
            sample_id=samples[0].id, ann_type="detection"
        ).all()
        assert len(anns) == 1
        bbox = json.loads(anns[0].bbox_json)
        # Image is 100x100, so normalised = pixel/100
        assert bbox["x"] == pytest.approx(0.1)
        assert bbox["y"] == pytest.approx(0.2)
        assert bbox["w"] == pytest.approx(0.3)
        assert bbox["h"] == pytest.approx(0.4)

    def test_adds_labeled_tag(self, db_session, sample_dataset, mock_st, monkeypatch):
        from utils.labeling import save_detections
        ds, samples = sample_dataset
        mock_st["labeling_index"] = 0
        monkeypatch.setattr("utils.labeling.get_dataset_session", lambda d: db_session)
        monkeypatch.setattr("utils.labeling.count_samples", lambda d, s=None: 5)
        monkeypatch.setattr("utils.labeling.get_sample_at_index",
                            lambda d, idx, s=None: samples[idx])

        boxes = [{"x": 10, "y": 20, "width": 30, "height": 40, "label": "car"}]
        save_detections(ds, boxes, advance=False)

        tags = db_session.query(Tag).filter_by(sample_id=samples[0].id, tag="labeled").all()
        assert len(tags) == 1


class TestSavePolygons:
    def test_saves_polygon_normalised(self, db_session, sample_dataset, mock_st, monkeypatch):
        from utils.labeling import save_polygons
        ds, samples = sample_dataset
        mock_st["labeling_index"] = 0
        monkeypatch.setattr("utils.labeling.get_dataset_session", lambda d: db_session)
        monkeypatch.setattr("utils.labeling.count_samples", lambda d, s=None: 5)
        monkeypatch.setattr("utils.labeling.get_sample_at_index",
                            lambda d, idx, s=None: samples[idx])

        polys = [
            {"points": [[10, 10], [50, 10], [50, 50], [10, 50]], "label": "car"},
        ]
        save_polygons(ds, polys, advance=False)

        anns = db_session.query(Annotation).filter_by(
            sample_id=samples[0].id, ann_type="segmentation"
        ).all()
        assert len(anns) == 1
        points = json.loads(anns[0].polygon_json)
        assert points[0] == [pytest.approx(0.1), pytest.approx(0.1)]

    def test_skips_polygons_with_too_few_points(self, db_session, sample_dataset, mock_st, monkeypatch):
        from utils.labeling import save_polygons
        ds, samples = sample_dataset
        mock_st["labeling_index"] = 0
        monkeypatch.setattr("utils.labeling.get_dataset_session", lambda d: db_session)
        monkeypatch.setattr("utils.labeling.count_samples", lambda d, s=None: 5)
        monkeypatch.setattr("utils.labeling.get_sample_at_index",
                            lambda d, idx, s=None: samples[idx])

        polys = [
            {"points": [[10, 10], [50, 10]], "label": "car"},  # Only 2 points
        ]
        save_polygons(ds, polys, advance=False)

        anns = db_session.query(Annotation).filter_by(
            sample_id=samples[0].id, ann_type="segmentation"
        ).all()
        assert len(anns) == 0


class TestSaveTags:
    def test_adds_tags(self, db_session, sample_dataset, mock_st, monkeypatch):
        from utils.labeling import save_tags
        ds, samples = sample_dataset
        mock_st["labeling_index"] = 0
        monkeypatch.setattr("utils.labeling.get_dataset_session", lambda d: db_session)
        monkeypatch.setattr("utils.labeling.count_samples", lambda d, s=None: 5)
        monkeypatch.setattr("utils.labeling.get_sample_at_index",
                            lambda d, idx, s=None: samples[idx])

        save_tags(ds, ["good", "review"])

        tags = db_session.query(Tag).filter_by(sample_id=samples[0].id).all()
        tag_names = {t.tag for t in tags}
        assert "good" in tag_names
        assert "review" in tag_names


class TestLabelingProgress:
    def test_counts_labeled(self, db_session, annotated_dataset, monkeypatch):
        from utils.labeling import labeling_progress
        ds, _ = annotated_dataset
        monkeypatch.setattr("utils.labeling.get_dataset_session", lambda d: db_session)
        monkeypatch.setattr("utils.labeling.count_samples", lambda d, s=None: 5)

        labeled, total = labeling_progress(ds)
        assert total == 5
        assert labeled == 2  # samples[0] and samples[1] have "labeled" tag

    def test_empty_dataset(self, monkeypatch):
        from utils.labeling import labeling_progress
        assert labeling_progress(None) == (0, 0)
