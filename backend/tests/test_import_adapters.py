"""
Adapter-level tests. Pure functions, no DB, no HTTP.

Uses stdlib ``unittest`` (pytest not guaranteed in the container).
"""
import json
import unittest

from backend.import_adapters import get_adapter
from backend.import_adapters.coco import parse as coco_parse
from backend.import_adapters.jsonl import parse as jsonl_parse


class TestGetAdapter(unittest.TestCase):
    def test_known_formats(self):
        self.assertIs(get_adapter("jsonl"), jsonl_parse)
        self.assertIs(get_adapter("coco"), coco_parse)

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            get_adapter("yolo")


class TestJsonlDefensiveParsing(unittest.TestCase):
    def test_non_dict_top_level_produces_error(self):
        raw = b'["bad"]\n{"filename":"a.jpg","annotations":[]}\n'
        items, errors = jsonl_parse(raw)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].row, 1)
        self.assertEqual(len(items), 1)

    def test_annotation_not_dict_produces_error(self):
        raw = b'{"filename":"a.jpg","annotations":["oops"]}\n'
        items, errors = jsonl_parse(raw)
        self.assertEqual(len(errors), 1)
        self.assertEqual(items, [])

    def test_missing_annotations_field_tolerated(self):
        raw = b'{"filename":"a.jpg"}\n'
        items, errors = jsonl_parse(raw)
        self.assertEqual(errors, [])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].annotations, [])


class TestCocoDefensiveParsing(unittest.TestCase):
    def test_top_level_array_produces_error(self):
        raw = b'[1,2,3]'
        items, errors = coco_parse(raw)
        self.assertEqual(items, [])
        self.assertEqual(len(errors), 1)
        reason = errors[0].reason.lower()
        self.assertTrue(
            "top-level" in reason or "object" in reason,
            f"unexpected reason: {errors[0].reason}",
        )

    def test_non_numeric_image_size_tolerated(self):
        raw = json.dumps({
            "images": [{"id": 1, "file_name": "a.jpg", "width": "wide", "height": 100}],
            "categories": [{"id": 1, "name": "cat"}],
            "annotations": [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [0, 0, 10, 10]}],
        }).encode()
        items, errors = coco_parse(raw)
        # Either image rejected outright OR annotation rejected — both OK.
        # The important property: no uncaught exception.
        self.assertIsInstance(items, list)
        self.assertIsInstance(errors, list)

    def test_row_is_sequential_not_coco_id(self):
        """Minor #5: COCO error row should be the sequential index, not the raw COCO id."""
        raw = json.dumps({
            "images": [{"id": 1, "file_name": "a.jpg", "width": 100, "height": 100}],
            "categories": [{"id": 1, "name": "cat"}],
            "annotations": [
                {"id": 999, "image_id": 42, "category_id": 1, "bbox": [0, 0, 10, 10]},
            ],
        }).encode()
        items, errors = coco_parse(raw)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].row, 1)  # 1-based sequential, not 999


def _coco(images, categories, annotations) -> bytes:
    return json.dumps({
        "images": images,
        "categories": categories,
        "annotations": annotations,
    }).encode()


def _tiny_image(iid=1, fname="a.jpg", w=4, h=4):
    return {"id": iid, "file_name": fname, "width": w, "height": h}


class TestCocoMaskImport(unittest.TestCase):
    """COCO ``segmentation`` → uncompressed wire RLE."""

    def test_bbox_only_still_emits_bbox(self):
        raw = _coco(
            [_tiny_image()],
            [{"id": 1, "name": "cat"}],
            [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [0, 0, 2, 2]}],
        )
        items, errors = coco_parse(raw)
        self.assertEqual(errors, [])
        self.assertEqual(len(items), 1)
        ann = items[0].annotations[0]
        self.assertEqual(ann.ann_type, "bbox")
        self.assertIsNone(ann.mask_json)
        self.assertEqual(ann.bbox_json, {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5})

    def test_uncompressed_rle_emits_mask(self):
        counts = [0, 16]
        raw = _coco(
            [_tiny_image()],
            [{"id": 1, "name": "cat"}],
            [{
                "id": 1, "image_id": 1, "category_id": 1,
                "bbox": [0, 0, 4, 4],
                "segmentation": {"size": [4, 4], "counts": counts},
            }],
        )
        items, errors = coco_parse(raw)
        self.assertEqual(errors, [])
        ann = items[0].annotations[0]
        self.assertEqual(ann.ann_type, "mask")
        self.assertEqual(ann.mask_json, {"size": [4, 4], "counts": counts})
        self.assertIsNone(ann.bbox_json)

    def test_compressed_rle_is_uncompressed_on_the_wire(self):
        from backend import masks as mask_utils
        counts = [0, 16]
        raw = _coco(
            [_tiny_image()],
            [{"id": 1, "name": "cat"}],
            [{
                "id": 1, "image_id": 1, "category_id": 1,
                "segmentation": {
                    "size": [4, 4],
                    "counts": mask_utils.rle_to_string(counts),
                },
            }],
        )
        items, errors = coco_parse(raw)
        self.assertEqual(errors, [])
        ann = items[0].annotations[0]
        self.assertEqual(ann.ann_type, "mask")
        self.assertEqual(ann.mask_json, {"size": [4, 4], "counts": counts})

    def test_polygon_list_emits_mask(self):
        raw = _coco(
            [_tiny_image()],
            [{"id": 1, "name": "cat"}],
            [{
                "id": 1, "image_id": 1, "category_id": 1,
                "segmentation": [[0, 0, 4, 0, 4, 4, 0, 4]],
            }],
        )
        items, errors = coco_parse(raw)
        self.assertEqual(errors, [])
        ann = items[0].annotations[0]
        self.assertEqual(ann.ann_type, "mask")
        self.assertEqual(ann.mask_json["size"], [4, 4])
        self.assertEqual(sum(ann.mask_json["counts"]), 16)
        from backend import masks as mask_utils
        self.assertGreater(mask_utils.rle_area(ann.mask_json["counts"]), 0)

    def test_invalid_rle_is_a_row_error(self):
        raw = _coco(
            [_tiny_image()],
            [{"id": 1, "name": "cat"}],
            [{
                "id": 1, "image_id": 1, "category_id": 1,
                "segmentation": {"size": [4, 4], "counts": [1, 2]},
            }],
        )
        items, errors = coco_parse(raw)
        self.assertEqual(items, [])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].row, 1)
        self.assertIn("segmentation", errors[0].reason.lower())

    def test_empty_segmentation_falls_back_to_bbox(self):
        raw = _coco(
            [_tiny_image()],
            [{"id": 1, "name": "cat"}],
            [{
                "id": 1, "image_id": 1, "category_id": 1,
                "bbox": [0, 0, 2, 2],
                "segmentation": [],
            }],
        )
        items, errors = coco_parse(raw)
        self.assertEqual(errors, [])
        self.assertEqual(items[0].annotations[0].ann_type, "bbox")

    def test_bad_polygon_is_a_row_error_not_a_crash(self):
        raw = _coco(
            [_tiny_image()],
            [{"id": 1, "name": "cat"}],
            [{
                "id": 1, "image_id": 1, "category_id": 1,
                "segmentation": [[0, 0, 1]],
            }],
        )
        items, errors = coco_parse(raw)
        self.assertEqual(items, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("polygon", errors[0].reason.lower())


if __name__ == "__main__":
    unittest.main()
