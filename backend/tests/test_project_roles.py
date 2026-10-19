"""Contracts for Databricks users and consensus project roles."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.tests.conftest import make_sample_volume, test_client


class TestProjectRoles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.volume = make_sample_volume(self.root)
        self.context = test_client(self.root)
        self.client, _, _ = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def _headers(self, email):
        return {
            "X-Forwarded-Email": email,
            "X-Forwarded-Preferred-Username": email.split("@")[0].title(),
        }

    def test_seen_users_drive_fallback_picker_and_roles(self):
        for email in ("owner@example.com", "reviewer@example.com", "labeller@example.com"):
            response = self.client.get("/api/users/me", headers=self._headers(email))
            self.assertEqual(response.status_code, 200, response.text)

        directory = self.client.get(
            "/api/users/directory",
            headers=self._headers("owner@example.com"),
        )
        self.assertEqual(directory.status_code, 200, directory.text)
        self.assertEqual(directory.json()["source"], "app_visitors")
        self.assertEqual(
            {row["email"] for row in directory.json()["users"]},
            {"owner@example.com", "reviewer@example.com", "labeller@example.com"},
        )
        self.assertIn("already opened this app", directory.json()["warning"] or "")

        created = self.client.post(
            "/api/projects",
            headers=self._headers("owner@example.com"),
            json={
                "name": "roles",
                "task_type": "classification",
                "class_list": ["cat", "dog"],
                "source_volume": str(self.volume),
                "consensus_enabled": True,
                "consensus_reviewer_emails": ["reviewer@example.com"],
                "consensus_labeller_emails": ["labeller@example.com"],
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        project_id = created.json()["id"]
        self.assertEqual(created.json()["consensus_role"], "owner")
        self.assertEqual(
            created.json()["consensus_labeller_emails"], ["labeller@example.com"],
        )

        reviewer = self.client.get(
            f"/api/projects/{project_id}",
            headers=self._headers("reviewer@example.com"),
        )
        self.assertEqual(reviewer.json()["consensus_role"], "reviewer")
        self.assertEqual(reviewer.json()["consensus_reviewer_emails"], [])

        labeller = self.client.get(
            f"/api/projects/{project_id}",
            headers=self._headers("labeller@example.com"),
        )
        self.assertEqual(labeller.json()["consensus_role"], "labeller")

        viewer = self.client.get(
            f"/api/projects/{project_id}",
            headers=self._headers("viewer@example.com"),
        )
        self.assertEqual(viewer.json()["consensus_role"], "viewer")
        denied = self.client.post(
            f"/api/projects/{project_id}/consensus/tasks/next",
            headers=self._headers("viewer@example.com"),
        )
        self.assertEqual(denied.status_code, 403, denied.text)

        claimed = self.client.post(
            f"/api/projects/{project_id}/consensus/tasks/next",
            headers=self._headers("labeller@example.com"),
        )
        self.assertEqual(claimed.status_code, 200, claimed.text)
        self.assertIsNotNone(claimed.json())

    def test_workspace_directory_is_admin_only(self):
        from backend.schemas import WorkspaceUserOut

        fake_scim = [
            WorkspaceUserOut(email="hidden@example.com", display_name="Hidden"),
            WorkspaceUserOut(email="admin@example.com", display_name="Admin"),
        ]
        with patch.dict(
            "os.environ",
            {
                "DATABRICKS_HOST": "https://example.cloud.databricks.com",
                "ADMIN_EMAILS": "admin@example.com",
            },
            clear=False,
        ), patch(
            "backend.routes.users._workspace_users_from_scim",
            return_value=fake_scim,
        ):
            self.client.get("/api/users/me", headers=self._headers("owner@example.com"))
            denied = self.client.get("/api/users/directory")
            self.assertEqual(denied.status_code, 401)

            visitor = self.client.get(
                "/api/users/directory",
                headers=self._headers("owner@example.com"),
            )
            self.assertEqual(visitor.status_code, 200, visitor.text)
            self.assertEqual(visitor.json()["source"], "app_visitors")
            self.assertNotIn(
                "hidden@example.com",
                {row["email"] for row in visitor.json()["users"]},
            )

            admin = self.client.get(
                "/api/users/directory",
                headers=self._headers("admin@example.com"),
            )
            self.assertEqual(admin.status_code, 200, admin.text)
            self.assertEqual(admin.json()["source"], "workspace_scim")
            self.assertEqual(
                {row["email"] for row in admin.json()["users"]},
                {"hidden@example.com", "admin@example.com"},
            )

    def test_same_user_cannot_have_both_roles(self):
        response = self.client.post(
            "/api/projects",
            headers=self._headers("owner@example.com"),
            json={
                "name": "overlap",
                "task_type": "classification",
                "class_list": ["cat"],
                "source_volume": str(self.volume),
                "consensus_enabled": True,
                "consensus_reviewer_emails": ["person@example.com"],
                "consensus_labeller_emails": ["person@example.com"],
            },
        )
        self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
