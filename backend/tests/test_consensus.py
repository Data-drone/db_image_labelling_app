"""End-to-end contracts for blind classification consensus."""

import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend import deps
from backend.models import (
    AnnotationHistory,
    ConsensusResolution,
    LabelingTask,
    ProjectSample,
    TaskAnnotation,
)
from backend.tests.conftest import make_sample_volume, test_client


class TestClassificationConsensus(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.volume = make_sample_volume(self.root)
        self.client_context = test_client(self.root)
        self.client, _, _ = self.client_context.__enter__()
        self.addCleanup(self.client_context.__exit__, None, None, None)
        self.owner = {"X-Forwarded-Email": "owner@example.com"}

    def create_project(self, **overrides):
        payload = {
            "name": "consensus",
            "task_type": "classification",
            "class_list": ["cat", "dog"],
            "source_volume": str(self.volume),
            "consensus_enabled": True,
            "consensus_panel_size": 3,
            "consensus_coverage_percent": 100,
            "consensus_quorum": 2,
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
        self.assertIsNotNone(response.json())
        return response.json()

    def submit(self, project_id, task, user, labels):
        return self.client.post(
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

    def test_votes_are_editable_until_panel_freezes_then_auto_resolve(self):
        project = self.create_project()
        project_id = project["id"]

        first = self.claim(project_id, "a@example.com")
        wrong_lock = self.client.post(
            f"/api/projects/{project_id}/consensus/tasks/{first['id']}/heartbeat",
            headers={"X-Forwarded-Email": "b@example.com"},
            json={"lock_token": first["lock_token"]},
        )
        self.assertEqual(wrong_lock.status_code, 409)

        response = self.submit(project_id, first, "a@example.com", ["cat"])
        self.assertEqual(response.status_code, 200, response.text)
        retry = self.submit(project_id, first, "a@example.com", ["cat"])
        self.assertEqual(retry.status_code, 200, retry.text)
        changed = self.submit(project_id, first, "a@example.com", ["dog"])
        self.assertEqual(changed.status_code, 200, changed.text)

        # Gallery claim hydrates the latest private vote while the panel is
        # still collecting, and release must not drop that submission.
        reopened = self.client.post(
            f"/api/projects/{project_id}/consensus/samples/{first['sample_id']}/claim",
            headers={"X-Forwarded-Email": "a@example.com"},
        )
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertEqual(
            [row["label"] for row in reopened.json()["annotations"]], ["dog"],
        )
        released = self.client.post(
            f"/api/projects/{project_id}/consensus/tasks/{first['id']}/release",
            headers={"X-Forwarded-Email": "a@example.com"},
            json={"lock_token": reopened.json()["lock_token"]},
        )
        self.assertEqual(released.status_code, 200, released.text)
        reopened = self.client.post(
            f"/api/projects/{project_id}/consensus/samples/{first['sample_id']}/claim",
            headers={"X-Forwarded-Email": "a@example.com"},
        )
        self.assertEqual(reopened.status_code, 200, reopened.text)
        editor = reopened.json()
        self.assertEqual(
            [row["label"] for row in editor["annotations"]], ["dog"],
        )
        revised = self.submit(
            project_id, editor, "a@example.com", ["dog"],
        )
        self.assertEqual(revised.status_code, 200, revised.text)

        # A second user can explicitly open the same gallery image and receives
        # an independent blank vote, never the first user's private labels.
        second_response = self.client.post(
            f"/api/projects/{project_id}/consensus/samples/{first['sample_id']}/claim",
            headers={"X-Forwarded-Email": "b@example.com"},
        )
        self.assertEqual(second_response.status_code, 200, second_response.text)
        second = second_response.json()
        self.assertEqual(second["sample_id"], first["sample_id"])
        self.assertEqual(second["sample"]["annotations"], [])
        self.assertEqual(second["annotations"], [])
        self.assertEqual(self.submit(project_id, second, "b@example.com", ["cat"]).status_code, 200)

        third = self.claim(project_id, "c@example.com")
        result = self.submit(project_id, third, "c@example.com", ["dog"])
        self.assertEqual(result.status_code, 200, result.text)
        resolution = result.json()["resolution"]
        self.assertEqual(resolution["method"], "auto-vote")
        self.assertEqual(resolution["final_labels"], ["dog"])
        self.assertEqual(resolution["vote_count"], 2)
        self.assertAlmostEqual(resolution["agreement_score"], 2 / 3)

        frozen = self.client.post(
            f"/api/projects/{project_id}/consensus/samples/{first['sample_id']}/claim",
            headers={"X-Forwarded-Email": "a@example.com"},
        )
        self.assertEqual(frozen.status_code, 200, frozen.text)
        self.assertIsNone(frozen.json())
        locked = self.submit(project_id, editor, "a@example.com", ["cat"])
        self.assertEqual(locked.status_code, 409, locked.text)

        sample = self.client.get(
            f"/api/projects/{project_id}/samples/{first['sample_id']}"
        ).json()
        self.assertEqual(sample["status"], "labeled")
        self.assertEqual([row["label"] for row in sample["annotations"]], ["dog"])
        self.assertEqual(sample["annotations"][0]["created_by"], "consensus:auto")

        with deps.get_session_factory()() as db:
            task = db.query(LabelingTask).filter_by(id=first["id"]).one()
            revisions = (
                db.query(TaskAnnotation)
                .filter_by(task_id=first["id"])
                .order_by(TaskAnnotation.revision)
                .all()
            )
            self.assertTrue(task.locked_for_review)
            self.assertEqual(task.annotation_revision, 2)
            self.assertEqual(
                [(row.revision, row.label) for row in revisions],
                [(1, "cat"), (2, "dog")],
            )

        metrics = self.client.get(
            f"/api/projects/{project_id}/consensus/metrics",
            headers=self.owner,
        )
        self.assertEqual(metrics.status_code, 200, metrics.text)
        body = metrics.json()
        self.assertEqual(body["resolved_items"], 1)
        self.assertEqual(body["submitted_tasks"], 3)
        self.assertEqual(body["auto_resolved"], 1)
        self.assertEqual(
            {row["user"]: row["agreement_rate"] for row in body["per_user"]},
            {"a@example.com": 1.0, "b@example.com": 0.0, "c@example.com": 1.0},
        )

    def test_concurrent_claims_assign_distinct_slots_on_the_same_case(self):
        project_id = self.create_project()["id"]

        def concurrent_claim(user):
            response = self.client.post(
                f"/api/projects/{project_id}/consensus/tasks/next",
                headers={"X-Forwarded-Email": user},
            )
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        with ThreadPoolExecutor(max_workers=3) as pool:
            tasks = list(pool.map(
                concurrent_claim,
                ["a@example.com", "b@example.com", "c@example.com"],
            ))
        self.assertEqual(len({task["id"] for task in tasks}), 3)
        self.assertEqual(len({task["case_id"] for task in tasks}), 1)
        self.assertEqual(len({task["sample_id"] for task in tasks}), 1)

    def test_stale_task_lock_is_recovered(self):
        project_id = self.create_project()["id"]
        first = self.claim(project_id, "a@example.com")
        with deps.get_session_factory()() as db:
            task = db.query(LabelingTask).filter_by(id=first["id"]).one()
            task.locked_at = datetime.now(timezone.utc) - timedelta(minutes=10)
            db.commit()

        recovered = self.claim(project_id, "b@example.com")
        self.assertEqual(recovered["id"], first["id"])
        self.assertEqual(recovered["assigned_to"], "b@example.com")
        self.assertNotEqual(recovered["lock_token"], first["lock_token"])

    def test_expired_reopen_does_not_block_panel_freeze(self):
        project_id = self.create_project()["id"]
        first = self.claim(project_id, "a@example.com")
        sample_id = first["sample_id"]
        self.assertEqual(
            self.submit(project_id, first, "a@example.com", ["cat"]).status_code, 200,
        )
        reopened = self.client.post(
            f"/api/projects/{project_id}/consensus/samples/{sample_id}/claim",
            headers={"X-Forwarded-Email": "a@example.com"},
        )
        self.assertEqual(reopened.status_code, 200, reopened.text)
        with deps.get_session_factory()() as db:
            task = db.query(LabelingTask).filter_by(id=first["id"]).one()
            task.locked_at = datetime.now(timezone.utc) - timedelta(minutes=10)
            db.commit()

        second = self.client.post(
            f"/api/projects/{project_id}/consensus/samples/{sample_id}/claim",
            headers={"X-Forwarded-Email": "b@example.com"},
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(
            self.submit(project_id, second.json(), "b@example.com", ["dog"]).status_code,
            200,
        )
        third = self.client.post(
            f"/api/projects/{project_id}/consensus/samples/{sample_id}/claim",
            headers={"X-Forwarded-Email": "c@example.com"},
        )
        self.assertEqual(third.status_code, 200, third.text)
        result = self.submit(project_id, third.json(), "c@example.com", ["dog"])
        self.assertEqual(result.status_code, 200, result.text)
        resolution = result.json()["resolution"]
        self.assertIsNotNone(resolution)
        self.assertEqual(resolution["method"], "auto-vote")
        self.assertEqual(resolution["final_labels"], ["dog"])
        self.assertEqual(result.json()["case_state"], "resolved")

    def test_items_outside_consensus_coverage_need_one_vote(self):
        project_id = self.create_project(
            consensus_coverage_percent=0,
            consensus_panel_size=3,
            consensus_quorum=2,
        )["id"]
        task = self.claim(project_id, "a@example.com")
        result = self.submit(project_id, task, "a@example.com", ["cat", "dog"])
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["resolution"]["method"], "single")

    def test_owner_can_reopen_resolved_case_after_coverage_increases(self):
        project_id = self.create_project(
            consensus_coverage_percent=0,
            consensus_panel_size=3,
            consensus_quorum=2,
        )["id"]
        first = self.claim(project_id, "a@example.com")
        resolved = self.submit(project_id, first, "a@example.com", ["cat"])
        self.assertEqual(resolved.status_code, 200, resolved.text)
        sample_id = first["sample_id"]

        updated = self.client.patch(
            f"/api/projects/{project_id}",
            headers=self.owner,
            json={
                "consensus_coverage_percent": 100,
                "reset_consensus_unstarted": True,
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        denied = self.client.post(
            f"/api/projects/{project_id}/consensus/reopen-under-voted",
            headers={"X-Forwarded-Email": "labeller@example.com"},
        )
        self.assertEqual(denied.status_code, 403)
        reopened = self.client.post(
            f"/api/projects/{project_id}/consensus/reopen-under-voted",
            headers=self.owner,
        )
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertEqual(reopened.json(), {
            "cases_reopened": 1,
            "tasks_added": 2,
            "annotations_removed": 1,
        })

        sample = self.client.get(
            f"/api/projects/{project_id}/samples/{sample_id}",
        ).json()
        self.assertEqual(sample["status"], "unlabeled")
        self.assertEqual(sample["annotations"], [])
        progress = self.client.get(
            f"/api/projects/{project_id}/consensus/progress",
            headers=self.owner,
        ).json()
        # Three source samples now each have a three-person panel.
        self.assertEqual(progress["total_tasks"], 9)
        self.assertEqual(progress["submitted_tasks"], 1)
        self.assertEqual(progress["available_tasks"], 8)
        self.assertEqual(progress["resolved_items"], 0)

        second = self.client.post(
            f"/api/projects/{project_id}/consensus/samples/{sample_id}/claim",
            headers={"X-Forwarded-Email": "b@example.com"},
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["annotations"], [])

    def test_owner_can_reopen_review_case_after_panel_increases(self):
        project_id = self.create_project()["id"]
        first = self.claim(project_id, "a@example.com")
        sample_id = first["sample_id"]
        self.assertEqual(
            self.submit(project_id, first, "a@example.com", ["cat"]).status_code, 200,
        )
        second = self.client.post(
            f"/api/projects/{project_id}/consensus/samples/{sample_id}/claim",
            headers={"X-Forwarded-Email": "b@example.com"},
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(
            self.submit(project_id, second.json(), "b@example.com", ["dog"]).status_code,
            200,
        )
        third = self.client.post(
            f"/api/projects/{project_id}/consensus/samples/{sample_id}/claim",
            headers={"X-Forwarded-Email": "c@example.com"},
        )
        self.assertEqual(third.status_code, 200, third.text)
        disagreed = self.submit(
            project_id, third.json(), "c@example.com", ["cat", "dog"],
        )
        self.assertEqual(disagreed.status_code, 200, disagreed.text)
        self.assertIsNone(disagreed.json()["resolution"])
        progress = self.client.get(
            f"/api/projects/{project_id}/consensus/progress",
            headers=self.owner,
        ).json()
        self.assertEqual(progress["review_items"], 1)
        self.assertEqual(progress["resolved_items"], 0)

        updated = self.client.patch(
            f"/api/projects/{project_id}",
            headers=self.owner,
            json={
                "consensus_panel_size": 5,
                "reset_consensus_unstarted": True,
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        reopened = self.client.post(
            f"/api/projects/{project_id}/consensus/reopen-under-voted",
            headers=self.owner,
        )
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertEqual(reopened.json(), {
            "cases_reopened": 1,
            "tasks_added": 2,
            "annotations_removed": 0,
        })

        progress = self.client.get(
            f"/api/projects/{project_id}/consensus/progress",
            headers=self.owner,
        ).json()
        self.assertEqual(progress["review_items"], 0)
        self.assertEqual(progress["resolved_items"], 0)
        self.assertEqual(progress["submitted_tasks"], 3)
        extra = self.client.post(
            f"/api/projects/{project_id}/consensus/samples/{sample_id}/claim",
            headers={"X-Forwarded-Email": "d@example.com"},
        )
        self.assertEqual(extra.status_code, 200, extra.text)
        self.assertIsNotNone(extra.json())
        self.assertEqual(extra.json()["annotations"], [])

    def test_owner_can_reset_all_consensus_labeling_work(self):
        project_id = self.create_project()["id"]
        sample_id = None
        for user in ("a@example.com", "b@example.com", "c@example.com"):
            task = self.claim(project_id, user)
            sample_id = task["sample_id"]
            response = self.submit(project_id, task, user, ["cat"])
            self.assertEqual(response.status_code, 200, response.text)

        with deps.get_session_factory()() as db:
            db.add(AnnotationHistory(
                sample_id=sample_id,
                project_id=project_id,
                action="create",
                new_label="cat",
                new_ann_type="classification",
                changed_by="owner@example.com",
            ))
            db.commit()

        unconfirmed = self.client.post(
            f"/api/projects/{project_id}/consensus/reset-labeling",
            headers=self.owner,
            json={"confirm": False},
        )
        self.assertEqual(unconfirmed.status_code, 400)
        denied = self.client.post(
            f"/api/projects/{project_id}/consensus/reset-labeling",
            headers={"X-Forwarded-Email": "labeller@example.com"},
            json={"confirm": True},
        )
        self.assertEqual(denied.status_code, 403)

        reset = self.client.post(
            f"/api/projects/{project_id}/consensus/reset-labeling",
            headers=self.owner,
            json={"confirm": True},
        )
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(reset.json(), {
            "samples_reset": 3,
            "annotations_deleted": 1,
            "history_deleted": 1,
            "cases_recreated": 3,
        })

        progress = self.client.get(
            f"/api/projects/{project_id}/consensus/progress",
            headers=self.owner,
        ).json()
        self.assertEqual(progress["total_items"], 3)
        self.assertEqual(progress["total_tasks"], 9)
        self.assertEqual(progress["submitted_tasks"], 0)
        self.assertEqual(progress["available_tasks"], 9)
        self.assertEqual(progress["resolved_items"], 0)
        samples = self.client.get(
            f"/api/projects/{project_id}/samples",
            params={"page_size": 10},
        ).json()["items"]
        self.assertTrue(all(row["status"] == "unlabeled" for row in samples))
        self.assertTrue(all(row["annotations"] == [] for row in samples))

    def test_tie_is_private_until_authorized_review_and_relabel_is_audited(self):
        project_id = self.create_project()["id"]
        for user, labels in (
            ("a@example.com", ["cat"]),
            ("b@example.com", ["dog"]),
            ("c@example.com", ["cat", "dog"]),
        ):
            task = self.claim(project_id, user)
            response = self.submit(project_id, task, user, labels)
            self.assertEqual(response.status_code, 200, response.text)

        denied = self.client.get(
            f"/api/projects/{project_id}/consensus/review",
            headers={"X-Forwarded-Email": "a@example.com"},
        )
        self.assertEqual(denied.status_code, 403)
        queue = self.client.get(
            f"/api/projects/{project_id}/consensus/review",
            headers={"X-Forwarded-Email": "reviewer@example.com"},
        )
        self.assertEqual(queue.status_code, 200, queue.text)
        case = queue.json()["items"][0]
        self.assertEqual(len(case["submissions"]), 3)

        relabel = self.client.post(
            f"/api/projects/{project_id}/consensus/review/{case['case_id']}",
            headers={"X-Forwarded-Email": "reviewer@example.com"},
            json={"action": "relabel"},
        )
        self.assertEqual(relabel.status_code, 200, relabel.text)
        self.assertEqual(relabel.json()["outcome"], "relabel_requested")

        fourth = self.claim(project_id, "d@example.com")
        self.assertEqual(fourth["case_id"], case["case_id"])
        resolved = self.submit(project_id, fourth, "d@example.com", ["cat"])
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()["resolution"]["final_labels"], ["cat"])

        with deps.get_session_factory()() as db:
            audit = (
                db.query(ConsensusResolution)
                .filter_by(case_id=case["case_id"])
                .order_by(ConsensusResolution.id)
                .all()
            )
            self.assertEqual(
                [(row.method, row.outcome) for row in audit],
                [("manual-relabel", "relabel_requested"), ("auto-vote", "resolved")],
            )

    def test_reviewer_pick_edit_and_reject_outcomes(self):
        project_id = self.create_project()["id"]

        def create_tie(suffix):
            for prefix, labels in (
                ("a", ["cat"]),
                ("b", ["dog"]),
                ("c", ["cat", "dog"]),
            ):
                user = f"{prefix}{suffix}@example.com"
                task = self.claim(project_id, user)
                response = self.submit(project_id, task, user, labels)
                self.assertEqual(response.status_code, 200, response.text)
            queue = self.client.get(
                f"/api/projects/{project_id}/consensus/review",
                headers=self.owner,
            ).json()
            self.assertEqual(queue["total"], 1)
            return queue["items"][0]

        picked_case = create_tie("1")
        picked = self.client.post(
            f"/api/projects/{project_id}/consensus/review/{picked_case['case_id']}",
            headers=self.owner,
            json={
                "action": "pick",
                "source_task_id": picked_case["submissions"][0]["task_id"],
            },
        )
        self.assertEqual(picked.status_code, 200, picked.text)
        self.assertEqual(picked.json()["method"], "manual-pick")

        edited_case = create_tie("2")
        edited = self.client.post(
            f"/api/projects/{project_id}/consensus/review/{edited_case['case_id']}",
            headers=self.owner,
            json={"action": "edit", "labels": ["dog"]},
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["method"], "manual-edit")
        self.assertEqual(edited.json()["final_labels"], ["dog"])

        rejected_case = create_tie("3")
        rejected = self.client.post(
            f"/api/projects/{project_id}/consensus/review/{rejected_case['case_id']}",
            headers=self.owner,
            json={"action": "reject"},
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(rejected.json()["outcome"], "rejected")
        sample = self.client.get(
            f"/api/projects/{project_id}/samples/{rejected_case['sample_id']}"
        ).json()
        self.assertEqual(sample["status"], "skipped")

    def test_owner_controls_settings_and_legacy_writes_are_blocked(self):
        project = self.create_project()
        project_id = project["id"]
        denied = self.client.patch(
            f"/api/projects/{project_id}",
            headers={"X-Forwarded-Email": "labeller@example.com"},
            json={"consensus_quorum": 3},
        )
        self.assertEqual(denied.status_code, 403)

        updated = self.client.patch(
            f"/api/projects/{project_id}",
            headers=self.owner,
            json={
                "consensus_panel_size": 5,
                "consensus_quorum": 3,
                "reset_consensus_unstarted": True,
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["consensus_panel_size"], 5)
        sample_id = self.client.get(
            f"/api/projects/{project_id}/samples", params={"page_size": 1},
        ).json()["items"][0]["id"]
        bypass = self.client.post(
            f"/api/projects/{project_id}/samples/{sample_id}/annotate-batch",
            headers={"X-Forwarded-Email": "labeller@example.com"},
            json={"annotations": [{"label": "cat", "ann_type": "classification"}]},
        )
        self.assertEqual(bypass.status_code, 409)

    def test_resolved_reference_export_includes_consensus_metadata(self):
        project_id = self.create_project(
            consensus_panel_size=1,
            consensus_quorum=1,
        )["id"]
        # Reference export requires UC paths; the local scan has already seeded samples.
        with deps.get_session_factory()() as db:
            sample = db.query(ProjectSample).filter_by(project_id=project_id).first()
            self.assertIsNotNone(sample)
            sample.project.source_volume = "/Volumes/catalog/schema/images"
            sample.filepath = "/Volumes/catalog/schema/images/a.jpg"
            db.commit()

        task = self.claim(project_id, "a@example.com")
        result = self.submit(project_id, task, "a@example.com", ["cat", "dog"])
        self.assertEqual(result.status_code, 200, result.text)

        workspace = MagicMock()
        files = {}
        workspace.files.upload.side_effect = (
            lambda path, content, overwrite: files.__setitem__(path, content.read())
        )
        workspace.files.download.side_effect = (
            lambda path: SimpleNamespace(contents=io.BytesIO(files[path]))
        )
        workspace.files.list_directory_contents.return_value = []
        with patch(
            "backend.routes.export._get_workspace_client", return_value=workspace,
        ):
            response = self.client.post(
                f"/api/projects/{project_id}/export",
                headers=self.owner,
                json={
                    "mode": "reference",
                    "export_volume": "/Volumes/catalog/schema/output",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        export_path = response.json()["export_path"]
        record = json.loads(files[f"{export_path}/train.jsonl"])
        self.assertEqual(record["labels"], [0, 1])
        self.assertNotIn("label", record)
        self.assertEqual(record["vote_count"], 1)
        self.assertEqual(record["resolution_method"], "single")
        self.assertEqual(record["agreement_score"], 1.0)
        metadata = json.loads(files[f"{export_path}/metadata.json"])
        self.assertTrue(metadata["consensus_enabled"])


if __name__ == "__main__":
    unittest.main()
