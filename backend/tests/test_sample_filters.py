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


if __name__ == "__main__":
    unittest.main()
