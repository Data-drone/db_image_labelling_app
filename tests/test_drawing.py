"""
Tests for utils/drawing.py — image loading, thumbnails, bbox and polygon overlays.
"""

import io
import json
import os

import pytest
from PIL import Image

from utils.drawing import (
    class_color,
    load_image,
    load_thumbnail,
    draw_detections,
    draw_polygons,
    image_to_bytes,
)


class TestClassColor:
    def test_returns_tuple(self):
        color = class_color("car", ["car", "truck"])
        assert isinstance(color, tuple)
        assert len(color) == 3

    def test_deterministic(self):
        classes = ["car", "truck", "person"]
        c1 = class_color("truck", classes)
        c2 = class_color("truck", classes)
        assert c1 == c2

    def test_different_classes_different_colors(self):
        classes = ["car", "truck", "person"]
        c1 = class_color("car", classes)
        c2 = class_color("truck", classes)
        assert c1 != c2

    def test_unknown_class_uses_hash(self):
        color = class_color("unknown_class", ["car", "truck"])
        assert isinstance(color, tuple)
        assert len(color) == 3


class TestLoadImage:
    def test_loads_valid_image(self, tmp_image_dir):
        path = os.path.join(tmp_image_dir, "test_image_000.jpg")
        img = load_image(path)
        assert img is not None
        assert img.mode == "RGB"
        assert img.size == (100, 100)

    def test_returns_none_for_missing_file(self):
        img = load_image("/nonexistent/path/image.jpg")
        assert img is None

    def test_returns_none_for_non_image(self, tmp_image_dir):
        path = os.path.join(tmp_image_dir, "readme.txt")
        img = load_image(path)
        assert img is None


class TestLoadThumbnail:
    def test_thumbnail_respects_max_size(self, tmp_image_dir):
        path = os.path.join(tmp_image_dir, "test_image_000.jpg")
        thumb = load_thumbnail(path, size=(50, 50))
        assert thumb is not None
        assert thumb.width <= 50
        assert thumb.height <= 50

    def test_thumbnail_preserves_aspect_ratio(self):
        """Create a non-square image and verify thumbnail keeps ratio."""
        import tempfile
        img = Image.new("RGB", (200, 100), color=(255, 0, 0))
        tmpfile = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        img.save(tmpfile.name)
        thumb = load_thumbnail(tmpfile.name, size=(100, 100))
        assert thumb is not None
        # Width should be 100, height should be 50 (2:1 ratio)
        assert thumb.width == 100
        assert thumb.height == 50
        os.unlink(tmpfile.name)

    def test_returns_none_for_missing(self):
        assert load_thumbnail("/nonexistent.jpg") is None


class TestDrawDetections:
    def test_draws_on_copy(self, tmp_image_dir):
        """Original image should not be modified."""
        path = os.path.join(tmp_image_dir, "test_image_000.jpg")
        img = load_image(path)
        original_bytes = image_to_bytes(img)

        # Create mock detection
        class MockDet:
            label = "car"
            bbox_json = json.dumps({"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4})
            confidence = 0.9

        result = draw_detections(img, [MockDet()], ["car"])
        assert result is not img
        assert image_to_bytes(img) == original_bytes

    def test_result_is_valid_image(self, tmp_image_dir):
        path = os.path.join(tmp_image_dir, "test_image_000.jpg")
        img = load_image(path)

        class MockDet:
            label = "car"
            bbox_json = json.dumps({"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5})
            confidence = None

        result = draw_detections(img, [MockDet()], ["car"])
        assert isinstance(result, Image.Image)
        assert result.size == img.size

    def test_handles_empty_detections(self, tmp_image_dir):
        path = os.path.join(tmp_image_dir, "test_image_000.jpg")
        img = load_image(path)
        result = draw_detections(img, [], ["car"])
        assert isinstance(result, Image.Image)

    def test_skips_annotations_without_bbox(self, tmp_image_dir):
        path = os.path.join(tmp_image_dir, "test_image_000.jpg")
        img = load_image(path)

        class MockClassification:
            label = "car"
            bbox_json = None

        # Should not crash
        result = draw_detections(img, [MockClassification()], ["car"])
        assert isinstance(result, Image.Image)


class TestDrawPolygons:
    def test_draws_polygon_overlay(self, tmp_image_dir):
        path = os.path.join(tmp_image_dir, "test_image_000.jpg")
        img = load_image(path)

        class MockSeg:
            label = "car"
            polygon_json = json.dumps([
                [0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]
            ])
            confidence = None

        result = draw_polygons(img, [MockSeg()], ["car"])
        assert isinstance(result, Image.Image)
        assert result.mode == "RGB"

    def test_skips_polygon_with_too_few_points(self, tmp_image_dir):
        path = os.path.join(tmp_image_dir, "test_image_000.jpg")
        img = load_image(path)

        class MockSeg:
            label = "car"
            polygon_json = json.dumps([[0.1, 0.1], [0.5, 0.1]])  # Only 2 points
            confidence = None

        result = draw_polygons(img, [MockSeg()], ["car"])
        assert isinstance(result, Image.Image)

    def test_handles_empty_list(self, tmp_image_dir):
        path = os.path.join(tmp_image_dir, "test_image_000.jpg")
        img = load_image(path)
        result = draw_polygons(img, [], [])
        assert isinstance(result, Image.Image)


class TestImageToBytes:
    def test_returns_bytes(self):
        img = Image.new("RGB", (10, 10), color="red")
        result = image_to_bytes(img)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_png_format(self):
        img = Image.new("RGB", (10, 10), color="blue")
        result = image_to_bytes(img, fmt="PNG")
        # PNG magic bytes
        assert result[:8] == b'\x89PNG\r\n\x1a\n'

    def test_roundtrip(self):
        """Bytes should be loadable as an image."""
        original = Image.new("RGB", (50, 50), color=(128, 64, 32))
        data = image_to_bytes(original)
        loaded = Image.open(io.BytesIO(data))
        assert loaded.size == (50, 50)
