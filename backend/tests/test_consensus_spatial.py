import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from backend import masks
from backend import deps
from backend.models import Annotation, ConsensusCase, ConsensusResolution
from backend.tests.conftest import make_image_volume, test_client


class TestSpatialConsensus(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.volume = make_image_volume(self.root)
        self.client_context = test_client(self.root)
        self.client, _, _ = self.client_context.__enter__()
        self.addCleanup(self.client_context.__exit__, None, None, None)
        self.owner = {"X-Forwarded-Email": "owner@example.com"}

    def create_project(self, task_type, **overrides):
        payload = {
            "name": f"{task_type}-consensus",
            "task_type": task_type,
            "class_list": ["cat", "dog"],
            "source_volume": str(self.volume),
            "consensus_enabled": True,
            "consensus_panel_size": 3,
            "consensus_coverage_percent": 100,
            "consensus_quorum": 2,
            "consensus_iou_threshold": 0.5,
            "consensus_agreement_threshold": 0.5,
            "consensus_reviewer_emails": ["reviewer@example.com"],
        }
        payload.update(overrides)
        response = self.client.post("/api/projects", json=payload, headers=self.owner)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def claim(self, project_id, user):
        response = self.client.post(
            f"/api/projects/{project_id}/consensus/tasks/next",
            headers={"X-Forwarded-Email": user},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def submit(self, project_id, task, user, annotations, allow_empty=False):
        return self.client.post(
            f"/api/projects/{project_id}/consensus/tasks/{task['id']}/submit",
            headers={"X-Forwarded-Email": user},
            json={
                "lock_token": task["lock_token"],
                "annotations": annotations,
                "allow_empty": allow_empty,
            },
        )

    def test_detection_votes_are_blind_and_materialize_medoid(self):
        project_id = self.create_project("detection")["id"]
        boxes = [
            {"x": 0.10, "y": 0.10, "w": 0.40, "h": 0.40},
            {"x": 0.11, "y": 0.10, "w": 0.40, "h": 0.40},
            {"x": 0.12, "y": 0.10, "w": 0.40, "h": 0.40},
        ]
        result = None
        sample_id = None
        for user, box in zip(("a@example.com", "b@example.com", "c@example.com"), boxes):
            task = self.claim(project_id, user)
            sample_id = task["sample_id"]
            self.assertEqual(task["sample"]["annotations"], [])
            result = self.submit(
                project_id,
                task,
                user,
                [{"label": "cat", "ann_type": "bbox", "bbox_json": box}],
            )
            self.assertEqual(result.status_code, 200, result.text)

        resolution = result.json()["resolution"]
        self.assertEqual(resolution["method"], "auto-spatial")
        self.assertEqual(resolution["final_annotations"][0]["bbox_json"], boxes[1])
        sample = self.client.get(
            f"/api/projects/{project_id}/samples/{sample_id}",
        ).json()
        canonical = [row for row in sample["annotations"] if not row["is_draft"]]
        self.assertEqual(canonical[0]["ann_type"], "bbox")
        self.assertEqual(canonical[0]["bbox_json"], boxes[1])

    def test_detection_vote_can_be_revised_before_panel_is_complete(self):
        project_id = self.create_project("detection")["id"]
        first = self.claim(project_id, "a@example.com")
        old_box = {"x": 0.05, "y": 0.05, "w": 0.2, "h": 0.2}
        revised_box = {"x": 0.40, "y": 0.40, "w": 0.3, "h": 0.3}
        response = self.submit(
            project_id,
            first,
            "a@example.com",
            [{"label": "cat", "ann_type": "bbox", "bbox_json": old_box}],
        )
        self.assertEqual(response.status_code, 200, response.text)

        reopened = self.client.post(
            f"/api/projects/{project_id}/consensus/samples/{first['sample_id']}/claim",
            headers={"X-Forwarded-Email": "a@example.com"},
        )
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertEqual(reopened.json()["annotations"][0]["bbox_json"], old_box)
        revised = self.submit(
            project_id,
            reopened.json(),
            "a@example.com",
            [{"label": "cat", "ann_type": "bbox", "bbox_json": revised_box}],
        )
        self.assertEqual(revised.status_code, 200, revised.text)

        result = None
        for user in ("b@example.com", "c@example.com"):
            task = self.claim(project_id, user)
            result = self.submit(
                project_id,
                task,
                user,
                [{"label": "cat", "ann_type": "bbox", "bbox_json": revised_box}],
            )
            self.assertEqual(result.status_code, 200, result.text)

        self.assertEqual(
            result.json()["resolution"]["final_annotations"][0]["bbox_json"],
            revised_box,
        )

    def test_detection_conflict_enters_visual_review_and_pick_copies_geometry(self):
        project_id = self.create_project("detection")["id"]
        for user, label in (
            ("a@example.com", "cat"),
            ("b@example.com", "dog"),
            ("c@example.com", "cat"),
        ):
            task = self.claim(project_id, user)
            response = self.submit(
                project_id,
                task,
                user,
                [{
                    "label": label,
                    "ann_type": "bbox",
                    "bbox_json": {"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4},
                }],
            )
            self.assertEqual(response.status_code, 200, response.text)

        queue = self.client.get(
            f"/api/projects/{project_id}/consensus/review",
            headers=self.owner,
        ).json()
        self.assertEqual(queue["total"], 1)
        detail = self.client.get(
            f"/api/projects/{project_id}/consensus/review/{queue['items'][0]['case_id']}",
            headers=self.owner,
        ).json()
        self.assertEqual(detail["task_type"], "detection")
        self.assertTrue(detail["match_report"]["conflicts"])
        self.assertTrue(detail["submissions"][0]["annotations"])
        selected = detail["submissions"][0]
        resolution = self.client.post(
            f"/api/projects/{project_id}/consensus/review/{detail['case_id']}",
            headers=self.owner,
            json={"action": "pick", "source_task_id": selected["task_id"]},
        )
        self.assertEqual(resolution.status_code, 200, resolution.text)
        self.assertEqual(
            resolution.json()["final_annotations"][0]["bbox_json"],
            selected["annotations"][0]["bbox_json"],
        )

    def test_segmentation_votes_round_trip_and_empty_votes_resolve(self):
        project_id = self.create_project("segmentation")["id"]
        bitmap = np.zeros((12, 16), dtype=np.uint8)
        bitmap[2:8, 3:10] = 1
        _, _, counts = masks.counts_from_array(bitmap)
        wire = {"size": [12, 16], "counts": counts}
        result = None
        sample_id = None
        for user in ("a@example.com", "b@example.com", "c@example.com"):
            task = self.claim(project_id, user)
            sample_id = task["sample_id"]
            result = self.submit(
                project_id,
                task,
                user,
                [{"label": "cat", "ann_type": "mask", "mask_json": wire}],
            )
            self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["resolution"]["method"], "auto-spatial")
        sample = self.client.get(
            f"/api/projects/{project_id}/samples/{sample_id}",
        ).json()
        self.assertEqual(sample["annotations"][0]["mask_json"], wire)

        empty_project = self.create_project(
            "segmentation", name="empty-segmentation-consensus",
        )["id"]
        for user in ("d@example.com", "e@example.com", "f@example.com"):
            task = self.claim(empty_project, user)
            result = self.submit(
                empty_project, task, user, [], allow_empty=True,
            )
            self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["resolution"]["final_annotations"], [])
        self.assertEqual(result.json()["resolution"]["agreement_score"], 1.0)

    def test_concurrent_last_spatial_votes_create_one_active_resolution(self):
        project_id = self.create_project("detection")["id"]
        tasks = [
            self.claim(project_id, user)
            for user in ("a@example.com", "b@example.com", "c@example.com")
        ]
        annotation = [{
            "label": "cat",
            "ann_type": "bbox",
            "bbox_json": {"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4},
        }]
        first = self.submit(project_id, tasks[0], "a@example.com", annotation)
        self.assertEqual(first.status_code, 200, first.text)

        def submit_last(index):
            return self.submit(
                project_id,
                tasks[index],
                f"{'b' if index == 1 else 'c'}@example.com",
                annotation,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(submit_last, (1, 2)))
        self.assertTrue(all(response.status_code == 200 for response in responses))

        with deps.get_session_factory()() as db:
            case = db.query(ConsensusCase).filter_by(id=tasks[0]["case_id"]).one()
            resolutions = db.query(ConsensusResolution).filter_by(case_id=case.id).all()
            canonical = db.query(Annotation).filter_by(
                project_id=project_id, sample_id=case.sample_id, is_draft=False,
            ).all()
            self.assertEqual(case.state, "resolved")
            self.assertEqual(len([row for row in resolutions if row.outcome == "resolved"]), 1)
            self.assertEqual(len(canonical), 1)


if __name__ == "__main__":
    unittest.main()
