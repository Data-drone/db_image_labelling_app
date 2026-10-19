"""HTTP tests for Project Dashboard sample filters."""

import tempfile
import unittest
from pathlib import Path

from backend.tests.conftest import make_sample_volume, test_client


class TestSampleFilters(unittest.TestCase):
    def test_label_and_labeler_filters_match_the_same_annotation(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            volume = make_sample_volume(tmp)
            with test_client(tmp) as (client, _main, _tmp):
                response = client.post(
                    "/api/projects",
                    json={
                        "name": "filters",
                        "description": "",
                        "task_type": "classification",
                        "class_list": ["hardhat", "vest"],
                        "source_volume": str(volume),
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                project_id = response.json()["id"]

                samples = client.get(
                    f"/api/projects/{project_id}/samples",
                    params={"page_size": 100},
                ).json()["items"]
                labeled_sample = samples[0]
                response = client.post(
                    f"/api/projects/{project_id}/samples/{labeled_sample['id']}/annotate-batch",
                    headers={"X-Forwarded-Email": "labeler@example.com"},
                    json={
                        "annotations": [
                            {"label": "hardhat", "ann_type": "classification"}
                        ]
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)

                response = client.get(
                    f"/api/projects/{project_id}/samples",
                    params={
                        "label": "hardhat",
                        "labeler": "labeler@example.com",
                        "page_size": 100,
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["total"], 1)
                self.assertEqual(
                    response.json()["items"][0]["id"], labeled_sample["id"]
                )

                response = client.get(
                    f"/api/projects/{project_id}/samples",
                    params={"label": "vest", "page_size": 100},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["total"], 0)


class TestConsensusSampleFilters(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.volume = make_sample_volume(self.root)
        self.client_context = test_client(self.root)
        self.client, _, _ = self.client_context.__enter__()
        self.addCleanup(self.client_context.__exit__, None, None, None)
        self.owner = {"X-Forwarded-Email": "owner@example.com"}

    def create_project(self):
        response = self.client.post(
            "/api/projects",
            json={
                "name": "consensus-filters",
                "task_type": "classification",
                "class_list": ["cat", "dog"],
                "source_volume": str(self.volume),
                "consensus_enabled": True,
                "consensus_panel_size": 2,
                "consensus_coverage_percent": 100,
                "consensus_quorum": 2,
                "consensus_reviewer_emails": ["reviewer@example.com"],
                "consensus_labeller_emails": ["a@example.com", "b@example.com"],
            },
            headers=self.owner,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def claim(self, project_id, user):
        response = self.client.post(
            f"/api/projects/{project_id}/consensus/tasks/next",
            headers={"X-Forwarded-Email": user},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def submit(self, project_id, task, user, labels):
        response = self.client.post(
            f"/api/projects/{project_id}/consensus/tasks/{task['id']}/submit",
            headers={"X-Forwarded-Email": user},
            json={
                "lock_token": task["lock_token"],
                "annotations": [
                    {"label": label, "ann_type": "classification"}
                    for label in labels
                ],
                "allow_empty": not labels,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_mine_label_filter_uses_private_votes_until_resolution(self):
        project_id = self.create_project()["id"]
        first = self.claim(project_id, "a@example.com")
        self.submit(project_id, first, "a@example.com", ["cat"])

        unresolved = self.client.get(
            f"/api/projects/{project_id}/samples",
            params={"label": "cat", "page_size": 100},
            headers={"X-Forwarded-Email": "a@example.com"},
        )
        self.assertEqual(unresolved.status_code, 200, unresolved.text)
        self.assertEqual(unresolved.json()["total"], 0)

        mine = self.client.get(
            f"/api/projects/{project_id}/samples",
            params={"label": "cat", "label_scope": "mine", "page_size": 100},
            headers={"X-Forwarded-Email": "a@example.com"},
        )
        self.assertEqual(mine.status_code, 200, mine.text)
        self.assertEqual(mine.json()["total"], 1)
        self.assertEqual(mine.json()["items"][0]["id"], first["sample_id"])
        self.assertEqual(mine.json()["items"][0]["labels"], ["cat"])

        other = self.client.get(
            f"/api/projects/{project_id}/samples",
            params={"label": "cat", "label_scope": "mine", "page_size": 100},
            headers={"X-Forwarded-Email": "b@example.com"},
        )
        self.assertEqual(other.status_code, 200, other.text)
        self.assertEqual(other.json()["total"], 0)

        forbidden = self.client.get(
            f"/api/projects/{project_id}/samples",
            params={"labeler": "a@example.com", "page_size": 100},
            headers={"X-Forwarded-Email": "b@example.com"},
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.text)

        owner_during_collection = self.client.get(
            f"/api/projects/{project_id}/samples",
            params={"labeler": "a@example.com", "page_size": 100},
            headers=self.owner,
        )
        self.assertEqual(owner_during_collection.status_code, 200, owner_during_collection.text)
        self.assertEqual(owner_during_collection.json()["total"], 0)

        reviewer_during_collection = self.client.get(
            f"/api/projects/{project_id}/samples",
            params={"labeler": "a@example.com", "page_size": 100},
            headers={"X-Forwarded-Email": "reviewer@example.com"},
        )
        self.assertEqual(
            reviewer_during_collection.status_code, 200, reviewer_during_collection.text,
        )
        self.assertEqual(reviewer_during_collection.json()["total"], 0)

        own = self.client.get(
            f"/api/projects/{project_id}/samples",
            params={"labeler": "a@example.com", "page_size": 100},
            headers={"X-Forwarded-Email": "a@example.com"},
        )
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual(own.json()["total"], 1)

    def test_resolved_label_and_reviewer_state_filters(self):
        project_id = self.create_project()["id"]
        first = self.claim(project_id, "a@example.com")
        self.submit(project_id, first, "a@example.com", ["cat"])
        second = self.claim(project_id, "b@example.com")
        self.assertEqual(second["sample_id"], first["sample_id"])
        self.submit(project_id, second, "b@example.com", ["cat"])

        resolved = self.client.get(
            f"/api/projects/{project_id}/samples",
            params={"label": "cat", "page_size": 100},
            headers=self.owner,
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()["total"], 1)
        self.assertEqual(resolved.json()["items"][0]["labels"], ["cat"])

        by_state = self.client.get(
            f"/api/projects/{project_id}/samples",
            params={"consensus_state": "resolved", "page_size": 100},
            headers=self.owner,
        )
        self.assertEqual(by_state.status_code, 200, by_state.text)
        self.assertEqual(by_state.json()["total"], 1)

        collecting = self.client.get(
            f"/api/projects/{project_id}/samples",
            params={"consensus_state": "in_progress", "page_size": 100},
            headers=self.owner,
        )
        self.assertEqual(collecting.status_code, 200, collecting.text)
        self.assertEqual(collecting.json()["total"], 0)

        by_labeler = self.client.get(
            f"/api/projects/{project_id}/samples",
            params={"labeler": "a@example.com", "page_size": 100},
            headers={"X-Forwarded-Email": "reviewer@example.com"},
        )
        self.assertEqual(by_labeler.status_code, 200, by_labeler.text)
        self.assertEqual(by_labeler.json()["total"], 1)


if __name__ == "__main__":
    unittest.main()
