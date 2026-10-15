import unittest

import numpy as np

from backend import masks
from backend.consensus_matching import (
    bbox_iou,
    mask_iou,
    match_spatial_annotations,
)


def bbox(annotation_id, label, x, y, w, h):
    return {
        "id": annotation_id,
        "label": label,
        "ann_type": "bbox",
        "bbox_json": {"x": x, "y": y, "w": w, "h": h},
    }


def mask(annotation_id, label, bitmap):
    height, width, counts = masks.counts_from_array(np.asarray(bitmap, dtype=np.uint8))
    return {
        "id": annotation_id,
        "label": label,
        "ann_type": "mask",
        "mask_json": masks.to_storage(counts, height, width),
    }


class ConsensusMatchingTests(unittest.TestCase):
    def test_bbox_iou_boundaries(self):
        left = {"x": 0, "y": 0, "w": 0.5, "h": 0.5}
        self.assertEqual(bbox_iou(left, left), 1.0)
        self.assertEqual(
            bbox_iou(left, {"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5}),
            0.0,
        )
        self.assertAlmostEqual(
            bbox_iou(left, {"x": 0.25, "y": 0, "w": 0.5, "h": 0.5}),
            1 / 3,
        )
        self.assertEqual(bbox_iou(left, {"x": 0, "y": 0, "w": 0, "h": 1}), 0)

    def test_mask_iou_accepts_storage_and_wire_rle(self):
        first = mask(1, "cat", [[1, 1], [0, 0]])["mask_json"]
        second = mask(2, "cat", [[1, 0], [0, 0]])["mask_json"]
        self.assertEqual(mask_iou(first, first), 1.0)
        self.assertEqual(mask_iou(first, masks.to_wire(second)), 0.5)

    def test_detection_matching_selects_medoid_and_is_order_invariant(self):
        submissions = [
            {"task_id": 10, "annotations": [bbox(101, "cat", 0.1, 0.1, 0.4, 0.4)]},
            {"task_id": 20, "annotations": [bbox(201, "cat", 0.11, 0.1, 0.4, 0.4)]},
            {"task_id": 30, "annotations": [bbox(301, "cat", 0.12, 0.1, 0.4, 0.4)]},
        ]
        kwargs = {
            "task_type": "detection",
            "required_votes": 3,
            "quorum": 2,
            "iou_threshold": 0.5,
            "agreement_threshold": 0.5,
        }
        result = match_spatial_annotations(submissions, **kwargs)
        reversed_result = match_spatial_annotations(list(reversed(submissions)), **kwargs)
        self.assertFalse(result["requires_review"])
        self.assertEqual(result["final_annotations"][0]["bbox_json"], submissions[1]["annotations"][0]["bbox_json"])
        self.assertEqual(result, reversed_result)

    def test_unmatched_and_class_conflicts_require_review(self):
        result = match_spatial_annotations(
            [
                {"task_id": 10, "annotations": [bbox(101, "cat", 0.1, 0.1, 0.4, 0.4)]},
                {"task_id": 20, "annotations": [bbox(201, "dog", 0.1, 0.1, 0.4, 0.4)]},
                {"task_id": 30, "annotations": []},
            ],
            task_type="detection",
            required_votes=3,
            quorum=2,
            iou_threshold=0.5,
            agreement_threshold=0.5,
        )
        conflict_types = {
            conflict["type"] for conflict in result["match_report"]["conflicts"]
        }
        self.assertTrue(result["requires_review"])
        self.assertIn("class_conflict", conflict_types)
        self.assertIn("unmatched", conflict_types)

    def test_empty_spatial_votes_resolve_as_no_objects(self):
        result = match_spatial_annotations(
            [{"task_id": task_id, "annotations": []} for task_id in (1, 2, 3)],
            task_type="segmentation",
            required_votes=3,
            quorum=2,
            iou_threshold=0.5,
            agreement_threshold=0.5,
        )
        self.assertFalse(result["requires_review"])
        self.assertEqual(result["final_annotations"], [])
        self.assertEqual(result["agreement_score"], 1.0)
        self.assertEqual(result["vote_count"], 3)

    def test_segmentation_cluster_returns_submitted_medoid(self):
        first = mask(1, "part", [[1, 1, 0], [1, 1, 0], [0, 0, 0]])
        second = mask(2, "part", [[1, 1, 0], [1, 0, 0], [0, 0, 0]])
        third = mask(3, "part", [[1, 1, 0], [1, 1, 0], [0, 0, 0]])
        result = match_spatial_annotations(
            [
                {"task_id": 1, "annotations": [first]},
                {"task_id": 2, "annotations": [second]},
                {"task_id": 3, "annotations": [third]},
            ],
            task_type="segmentation",
            required_votes=3,
            quorum=2,
            iou_threshold=0.5,
            agreement_threshold=0.5,
        )
        self.assertFalse(result["requires_review"])
        self.assertEqual(result["final_annotations"][0]["mask_json"], first["mask_json"])
        self.assertEqual(result["match_report"]["clusters"][0]["support"], 3)


if __name__ == "__main__":
    unittest.main()
